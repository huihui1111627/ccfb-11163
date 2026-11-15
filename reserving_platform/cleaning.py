"""数据质量检查与清洗。

清洗规则与模拟器注入的五类问题一一对应，每条规则产出：

- 问题描述与命中行数
- 修复后的干净表
- 被隔离（无法自动修复）的流水

所有规则都是幂等的纯函数，页面侧可展示"修复前/修复后"对照。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .config import DEFAULT_LARGE_THRESHOLD
from .period_utils import date_to_quarter_series


@dataclass
class CleaningReport:
    issues: list[dict] = field(default_factory=list)
    rows_before: int = 0
    rows_after: int = 0

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.issues)


def quality_checks(claims: pd.DataFrame,
                   txns: pd.DataFrame) -> pd.DataFrame:
    """只检查不修改，返回问题清单。"""
    rows = []

    # 1. 重复流水：同赔案、同类型、同日期、同金额出现多次
    dup_key = ["claim_id", "txn_type", "txn_date", "amount"]
    dup_mask = txns.duplicated(dup_key, keep="first")
    rows.append({"rule": "重复流水", "severity": "高",
                 "object": "transactions", "count": int(dup_mask.sum()),
                 "fix": "保留首条，删除重复"})

    # 2. 维度缺失
    miss_claim = int(claims["product_line"].isna().sum()
                     + claims["channel"].isna().sum())
    miss_txn = int(txns["product_line"].isna().sum()
                   + txns["channel"].isna().sum())
    rows.append({"rule": "产品线/渠道缺失", "severity": "中",
                 "object": "both", "count": miss_claim + miss_txn,
                 "fix": "用同分段众数回填，无法回填则隔离"})

    # 3. 报案日期早于出险日期
    bad_date = int((claims["report_date"] < claims["accident_date"]).sum())
    rows.append({"rule": "报案早于出险", "severity": "高",
                 "object": "claims", "count": bad_date,
                 "fix": "报案日期修正为出险日期"})

    # 4. PAY 金额为负（RECOV 本身为负，属正常）
    neg = int(((txns["txn_type"] == "PAY") & (txns["amount"] < 0)).sum())
    rows.append({"rule": "支付金额符号异常", "severity": "高",
                 "object": "transactions", "count": neg,
                 "fix": "取绝对值纠正符号"})

    # 5. 孤儿流水：主表中找不到赔案
    orphan = int((~txns["claim_id"].isin(claims["claim_id"])).sum())
    rows.append({"rule": "孤儿流水(无赔案主表)", "severity": "高",
                 "object": "transactions", "count": orphan,
                 "fix": "隔离至人工处理队列"})

    # 6. 日期缺失
    null_dates = int(txns["txn_date"].isna().sum()
                     + claims[["accident_date", "report_date"]].isna().sum().sum())
    rows.append({"rule": "关键日期缺失", "severity": "高",
                 "object": "both", "count": null_dates,
                 "fix": "隔离至人工处理队列"})

    return pd.DataFrame(rows)


def clean(claims: pd.DataFrame, txns: pd.DataFrame
          ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, CleaningReport]:
    """执行清洗，返回 (干净主表, 干净流水, 隔离流水, 报告)。"""
    report = CleaningReport(rows_before=len(txns))
    claims = claims.copy()
    txns = txns.copy()
    quarantined: list[pd.DataFrame] = []

    # ---- 规则 6 先隔离关键日期缺失 ----
    bad_txn_date = txns["txn_date"].isna()
    if bad_txn_date.any():
        quarantined.append(txns[bad_txn_date].assign(_reason="关键日期缺失"))
        txns = txns[~bad_txn_date]
    bad_claim_date = claims["accident_date"].isna() | claims["report_date"].isna()
    if bad_claim_date.any():
        cids = claims.loc[bad_claim_date, "claim_id"]
        qmask = txns["claim_id"].isin(cids)
        quarantined.append(txns[qmask].assign(_reason="赔案关键日期缺失"))
        txns = txns[~qmask]
        claims = claims[~bad_claim_date]

    # ---- 规则 3 报案早于出险 ----
    bad_date = claims["report_date"] < claims["accident_date"]
    report.issues.append({"rule": "报案早于出险", "fixed": int(bad_date.sum())})
    claims.loc[bad_date, "report_date"] = claims.loc[bad_date, "accident_date"]

    # ---- 规则 4 支付符号 ----
    neg_mask = (txns["txn_type"] == "PAY") & (txns["amount"] < 0)
    report.issues.append({"rule": "支付金额符号异常", "fixed": int(neg_mask.sum())})
    txns.loc[neg_mask, "amount"] = txns[neg_mask]["amount"].abs()

    # ---- 规则 5 孤儿流水：先隔离，避免其重复副本残留 ----
    orphan_mask = ~txns["claim_id"].isin(claims["claim_id"])
    if orphan_mask.any():
        quarantined.append(txns[orphan_mask].assign(_reason="孤儿流水"))
        report.issues.append({"rule": "孤儿流水隔离",
                              "fixed": int(orphan_mask.sum())})
        txns = txns[~orphan_mask]

    # ---- 规则 1 重复流水：按业务键去重，保留最早录入（txn_id 最小）----
    dup_key = ["claim_id", "txn_type", "txn_date", "amount"]
    txns = txns.sort_values("txn_id")
    dup_mask = txns.duplicated(dup_key, keep="first")
    report.issues.append({"rule": "重复流水", "fixed": int(dup_mask.sum())})
    txns = txns[~dup_mask]

    # ---- 规则 2 维度缺失：用同表已知众数回填 ----
    pl_mode = claims["product_line"].mode()
    ch_mode = claims["channel"].mode()
    pl_fill = pl_mode.iloc[0] if len(pl_mode) else "未知产品线"
    ch_fill = ch_mode.iloc[0] if len(ch_mode) else "未知渠道"
    n_miss = int(claims[["product_line", "channel"]].isna().sum().sum())
    claims["product_line"] = claims["product_line"].fillna(pl_fill)
    claims["channel"] = claims["channel"].fillna(ch_fill)
    # 流水维度优先按赔案主表回填，再用众数兜底
    txns = txns.merge(
        claims[["claim_id", "product_line", "channel"]].rename(
            columns={"product_line": "_pl", "channel": "_ch"}),
        on="claim_id", how="left")
    txns["product_line"] = txns["product_line"].fillna(txns["_pl"]).fillna(pl_fill)
    txns["channel"] = txns["channel"].fillna(txns["_ch"]).fillna(ch_fill)
    txns = txns.drop(columns=["_pl", "_ch"])
    report.issues.append({"rule": "维度缺失回填", "fixed": n_miss})

    # 同步 segment 字段
    claims["segment"] = claims["product_line"] + "|" + claims["channel"]

    report.rows_after = len(txns)
    qdf = (pd.concat(quarantined, ignore_index=True)
           if quarantined else pd.DataFrame(columns=list(txns.columns) + ["_reason"]))
    return claims, txns.sort_values(["claim_id", "txn_date"]).reset_index(drop=True), \
        qdf, report


def tag_large_claims(claims: pd.DataFrame,
                     threshold: float = DEFAULT_LARGE_THRESHOLD,
                     by_product: dict | None = None) -> pd.DataFrame:
    """按阈值标记大额赔案。``by_product`` 可按产品线覆盖统一阈值。"""
    claims = claims.copy()
    if by_product:
        thr = claims["product_line"].map(by_product).fillna(threshold)
    else:
        thr = threshold
    claims["is_large"] = claims["true_ultimate"] >= thr
    claims.attrs["threshold"] = threshold
    return claims


def add_quarter_columns(claims: pd.DataFrame,
                        txns: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """预计算事故/报案/支付季度索引，供三角形模块复用（幂等）。"""
    claims = claims.copy()
    txns = txns.copy()
    if "accident_q" not in claims.columns:
        claims["accident_q"] = date_to_quarter_series(claims["accident_date"])
    if "report_q" not in claims.columns:
        claims["report_q"] = date_to_quarter_series(claims["report_date"])
    if "txn_q" not in txns.columns:
        txns["txn_q"] = date_to_quarter_series(txns["txn_date"])
    if "accident_q" not in txns.columns:
        txns = txns.merge(
            claims[["claim_id", "accident_q", "report_q"]],
            on="claim_id", how="left")
        txns["dev_from_accident"] = txns["txn_q"] - txns["accident_q"]
        txns["dev_from_report"] = txns["txn_q"] - txns["report_q"]
    return claims, txns
