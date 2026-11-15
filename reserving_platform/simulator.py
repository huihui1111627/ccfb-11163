"""理赔流水模拟器。

生成两张表：

1. **赔案主表** ``claims``：一案一行，含出险/报案/结案日期、产品线、渠道、
   真实最终赔款 ``true_ultimate``、是否大额等。
2. **交易流水台账** ``transactions``：一案多行，事件类型包括

   - ``PAY``     支付（正数）
   - ``RECOV``   追偿回收（负支付）
   - ``CASE``    未决估损设定/调整（快照值 = 调整日时点的未决估损）
   - ``CLOSE``   结案
   - ``REOPEN``  重开（重开后可再次 PAY/CASE/CLOSE）
   - ``DENY``    拒赔

时间一律取到具体日期，三角形模块再按季度归集。模拟器先在上帝视角生成完整
生命周期，再按估值日截断；``true_ultimate`` 永远保留，作为准备金估计的
标准答案。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import SimConfig
from .period_utils import quarter_end, quarter_index, quarter_start

CLAIM_ID_OFFSET = 1_000_000
TXN_ID_OFFSET = 50_000_000

# 主表与流水的标准列顺序
CLAIM_COLS = [
    "claim_id", "product_line", "channel", "segment",
    "accident_date", "report_date", "close_date", "status",
    "true_ultimate", "is_large", "denied", "reopened", "has_recovery",
]
TXN_COLS = [
    "txn_id", "claim_id", "txn_type", "txn_date",
    "amount", "case_estimate", "product_line", "channel",
]


# ---------------------------------------------------------------------------
# 分布工具（仅依赖 numpy）
# ---------------------------------------------------------------------------
def lognormal_cdf(x: float, mu: float, sigma: float) -> float:
    """对数正态分布 CDF。"""
    if x <= 0:
        return 0.0
    return 0.5 * (1.0 + math.erf((math.log(x) - mu) / (sigma * math.sqrt(2.0))))


def _date_in_quarter(rng: np.random.Generator, q: int) -> pd.Timestamp:
    """季度内均匀取一个日期。"""
    start = quarter_start(q)
    end = quarter_start(q + 1) - pd.Timedelta(days=1)
    offset = rng.integers(0, max((end - start).days, 1) + 1)
    return start + pd.Timedelta(days=int(offset))


def _jitter_date(rng: np.random.Generator, ts: pd.Timestamp,
                 low: int = 0, high: int = 80) -> pd.Timestamp:
    return ts + pd.Timedelta(days=int(rng.integers(low, high)))


@dataclass
class SimulationResult:
    claims: pd.DataFrame
    transactions: pd.DataFrame
    issue_manifest: dict          # 注入的脏数据计数（用于核对清洗效果）
    config: SimConfig

    def as_of(self, valuation_date) -> "SimulationResult":
        """返回截断到估值日的副本（主表状态也按估值日重算）。"""
        vd = pd.Timestamp(valuation_date)
        vq = quarter_index(vd)
        cut_txns = self.transactions[self.transactions["txn_date"] <= vd].copy()
        cut_claims = self.claims.copy()
        # 估值日之后才结案的赔案，在截断视图中视为未决
        cut_claims["status"] = np.where(
            cut_claims["close_date"].isna() | (cut_claims["close_date"] > vd),
            "OPEN", "CLOSED",
        )
        cut_claims.loc[cut_claims["denied"] & (cut_claims["report_date"] <= vd),
                       "status"] = "DENIED"
        return SimulationResult(cut_claims, cut_txns, self.issue_manifest, self.config)


# ---------------------------------------------------------------------------
# 主生成逻辑
# ---------------------------------------------------------------------------
def simulate(cfg: SimConfig | None = None) -> SimulationResult:
    """按配置生成五年理赔流水。"""
    cfg = cfg or SimConfig()
    rng = np.random.default_rng(cfg.seed)

    claim_rows: list[dict] = []
    txn_rows: list[dict] = []
    claim_seq = 0
    txn_seq = 0
    issues = {"duplicate": 0, "missing_dim": 0, "bad_date": 0,
              "negative_amount": 0, "orphan": 0}

    for spec in cfg.specs:
        for aq in range(cfg.start_q, cfg.end_q + 1):
            n_claims = rng.poisson(spec.freq_per_quarter * cfg.scale)
            for _ in range(n_claims):
                claim_seq += 1
                cid = CLAIM_ID_OFFSET + claim_seq
                accident_date = _date_in_quarter(rng, aq)

                # 报案延迟（季度）+ 季度内抖动；报案不得早于出险
                delay_q = int(rng.choice(len(spec.report_delay_p),
                                         p=spec.report_delay_p))
                report_q = aq + delay_q
                report_date = _jitter_date(
                    rng, quarter_start(report_q), 0,
                    min(80, (quarter_end(report_q) - quarter_start(report_q)).days + 1))
                if report_date < accident_date:
                    # 同季度内报案日早于出险日：顺延到出险日之后
                    span = max((quarter_end(report_q) - accident_date).days, 2)
                    report_date = accident_date + pd.Timedelta(
                        days=int(rng.integers(0, span)))

                # 真实最终赔款（拒赔为 0）
                denied = rng.random() < spec.deny_prob
                if denied:
                    true_ult = 0.0
                else:
                    true_ult = float(rng.lognormal(spec.severity_mu,
                                                   spec.severity_sigma))

                # ---- 拒赔：只有一条 DENY ----
                if denied:
                    txn_seq += 1
                    txn_rows.append(_txn(txn_seq, cid, "DENY", report_date,
                                         0.0, 0.0, spec))
                    claim_rows.append(_claim(cid, spec, accident_date, report_date,
                                             report_date, "DENIED", true_ult,
                                             rng, cfg, issues))
                    continue

                # ---- 正常赔案：生成支付计划 ----
                # 支付模式以事故季度为基准的对数正态 CDF；支付季度不得早于
                # 报案季度（业务约束），被报案延迟截断的支付压到报案后首期
                n_pay = int(rng.integers(2, 6))  # 2~5 笔支付
                pay_qs: list[int] = []
                for k in range(n_pay):
                    target = (k + 1) / (n_pay + 1)
                    dq = 1
                    while (dq < cfg.max_pay_dev
                           and lognormal_cdf(dq, math.log(max(spec.median_pay_q, 0.1)),
                                             spec.pay_pattern_sigma) < target):
                        dq += 1
                    pay_q = max(aq + dq, report_q)
                    if pay_qs:
                        pay_q = max(pay_q, pay_qs[-1])
                    pay_qs.append(min(pay_q, aq + cfg.max_pay_dev))
                pay_qs = sorted(pay_qs)

                # 各笔支付金额：按季度内均匀权重切分真实最终赔款，再加轻微抖动
                weights = rng.dirichlet([2.0] * n_pay)
                pay_amounts = true_ult * weights
                # 追偿：最后一笔支付后产生负向回收
                has_recovery = rng.random() < spec.recovery_prob
                recovery_amt = (true_ult * spec.recovery_frac
                                * rng.uniform(0.5, 1.5) if has_recovery else 0.0)

                # 用日期游标生成事件，严格保证时序：
                # 报案 -> CASE -> PAY/CASE 交替 -> (RECOV) -> CLOSE
                # -> [REOPEN -> CASE -> PAY -> CLOSE]
                events: list[tuple[str, pd.Timestamp, float, float]] = []
                cursor = report_date
                first_case = max(true_ult * rng.lognormal(
                    math.log(spec.case_bias), spec.case_sigma), 1.0)
                cursor += pd.Timedelta(days=int(rng.integers(2, 20)))
                events.append(("CASE", cursor, 0.0, round(first_case, 2)))
                case = first_case

                cumulative_paid = 0.0
                for idx, (pq, amt) in enumerate(zip(pay_qs, pay_amounts)):
                    # 支付日：在目标季度内、且严格晚于上一事件
                    q_start = quarter_start(pq)
                    pay_date = q_start + pd.Timedelta(
                        days=int(rng.integers(0, 80)))
                    if pay_date <= cursor:
                        pay_date = cursor + pd.Timedelta(
                            days=int(rng.integers(5, 20)))
                    cursor = pay_date
                    cumulative_paid += amt
                    events.append(("PAY", cursor, round(amt, 2), 0.0))

                    remaining = max(true_ult - cumulative_paid, 0.0)
                    is_last = idx == len(pay_qs) - 1
                    if remaining > 1.0 and not is_last:
                        target_case = remaining * rng.lognormal(
                            math.log(spec.case_bias), spec.case_sigma * 0.7)
                        case = (case * (1 - spec.case_adjust_shrink)
                                + target_case * spec.case_adjust_shrink)
                        cursor += pd.Timedelta(days=int(rng.integers(5, 25)))
                        events.append(("CASE", cursor, 0.0, round(case, 2)))

                # 追偿（最后一笔支付之后）
                if has_recovery:
                    cursor += pd.Timedelta(days=int(rng.integers(15, 60)))
                    events.append(("RECOV", cursor,
                                   -round(recovery_amt, 2), 0.0))

                # 结案：估损清零，严格晚于最后一个事件
                cursor += pd.Timedelta(days=int(rng.integers(10, 40)))
                close_date = cursor
                events.append(("CLOSE", close_date, 0.0, 0.0))

                # 重开：结案后重开，补一笔支付再结案
                reopen = False
                if rng.random() < spec.reopen_prob:
                    reopen = True
                    extra = true_ult * rng.uniform(0.03, 0.12)
                    true_ult += extra  # 重开补赔计入真实最终赔款
                    cursor += pd.Timedelta(days=int(rng.integers(40, 120)))
                    reopen_date = cursor
                    events.append(("REOPEN", reopen_date, 0.0, 0.0))
                    cursor += pd.Timedelta(days=3)
                    events.append(("CASE", cursor, 0.0,
                                   round(extra * 1.05, 2)))
                    cursor += pd.Timedelta(days=int(rng.integers(5, 25)))
                    pay2_date = cursor
                    events.append(("PAY", pay2_date, round(extra, 2), 0.0))
                    cursor += pd.Timedelta(days=20)
                    close_date = cursor
                    events.append(("CLOSE", close_date, 0.0, 0.0))

                # 落流水（事件已按时间有序）
                for etype, edate, eamt, ecase in events:
                    txn_seq += 1
                    txn_rows.append(_txn(txn_seq, cid, etype, edate,
                                         eamt, ecase, spec))

                claim_rows.append(_claim(cid, spec, accident_date, report_date,
                                         close_date, "CLOSED", true_ult,
                                         rng, cfg, issues,
                                         reopened=reopen,
                                         has_recovery=has_recovery))

    claims = pd.DataFrame(claim_rows)[CLAIM_COLS]
    txns = pd.DataFrame(txn_rows)[TXN_COLS]

    # ------------------------------------------------------------------
    # 注入数据质量问题（清洗模块负责识别）
    # ------------------------------------------------------------------
    txns, issues = _inject_issues(txns, claims, rng, cfg, issues)

    for df in (claims, txns):
        for col in ("accident_date", "report_date", "close_date", "txn_date"):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col])

    return SimulationResult(claims, txns, issues, cfg)


def _txn(seq: int, cid: int, ttype: str, tdate, amount: float,
         case_est: float, spec) -> dict:
    return {
        "txn_id": TXN_ID_OFFSET + seq,
        "claim_id": cid,
        "txn_type": ttype,
        "txn_date": tdate,
        "amount": round(float(amount), 2),
        "case_estimate": round(float(case_est), 2),
        "product_line": spec.product_line,
        "channel": spec.channel,
    }


def _claim(cid: int, spec, accident_date, report_date, close_date,
           status: str, true_ult: float, rng, cfg: SimConfig,
           issues: dict, reopened: bool = False,
           has_recovery: bool = False) -> dict:
    product_line = spec.product_line
    channel = spec.channel
    # 缺失维度注入（在主表上做标记，由清洗模块修复）
    if rng.random() < cfg.missing_dim_rate:
        product_line = None
        issues["missing_dim"] += 1
    if rng.random() < cfg.missing_dim_rate:
        channel = None
        issues["missing_dim"] += 1
    return {
        "claim_id": cid,
        "product_line": product_line,
        "channel": channel,
        "segment": spec.segment,
        "accident_date": accident_date,
        "report_date": report_date,
        "close_date": close_date,
        "status": status,
        "true_ultimate": round(float(true_ult), 2),
        "is_large": bool(true_ult >= _threshold(spec, cfg)),
        "denied": status == "DENIED",
        "reopened": reopened,
        "has_recovery": has_recovery,
    }


def _threshold(spec, cfg: SimConfig) -> float:
    from .config import LARGE_CLAIM_THRESHOLD
    return LARGE_CLAIM_THRESHOLD.get(spec.product_line, DEFAULT_T)


DEFAULT_T = 100_000.0


def _inject_issues(txns: pd.DataFrame, claims: pd.DataFrame,
                   rng: np.random.Generator, cfg: SimConfig,
                   issues: dict) -> tuple[pd.DataFrame, dict]:
    txns = txns.sort_values("txn_date").reset_index(drop=True)

    # 1. 重复流水：复制部分 PAY/RECOV 行，分配新 txn_id
    pay_mask = txns["txn_type"].isin(["PAY", "RECOV"])
    dup_idx = txns.index[pay_mask & (rng.random(len(txns)) < cfg.dup_txn_rate)]
    if len(dup_idx):
        dups = txns.loc[dup_idx].copy()
        max_id = txns["txn_id"].max()
        dups["txn_id"] = range(max_id + 1, max_id + 1 + len(dups))
        txns = pd.concat([txns, dups], ignore_index=True)
        issues["duplicate"] = len(dups)

    # 2. 报案日期早于出险日期（改主表）
    bad = rng.random(len(claims)) < cfg.bad_date_rate
    claims.loc[bad, "report_date"] = claims.loc[bad, "accident_date"] \
        - pd.to_timedelta(rng.integers(1, 30, bad.sum()), unit="D")
    issues["bad_date"] = int(bad.sum())

    # 3. 支付金额符号录反（PAY 应为正）
    pay_rows = txns.index[txns["txn_type"] == "PAY"]
    neg_sel = pay_rows[rng.random(len(pay_rows)) < cfg.neg_amount_rate]
    txns.loc[neg_sel, "amount"] = -txns.loc[neg_sel, "amount"].abs()
    issues["negative_amount"] = len(neg_sel)

    # 4. 孤儿流水：claim_id 指向不存在的赔案
    orphan_sel = txns.index[(txns["txn_type"] == "PAY")
                            & (rng.random(len(txns)) < cfg.orphan_txn_rate)]
    if len(orphan_sel):
        fake_id = claims["claim_id"].max() + 900_000
        txns.loc[orphan_sel, "claim_id"] = fake_id
        issues["orphan"] = len(orphan_sel)

    return txns.sort_values(["txn_date", "txn_id"]).reset_index(drop=True), issues
