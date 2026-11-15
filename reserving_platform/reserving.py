"""准备金估计：链梯法（CL）与 Bornhuetter-Ferguson 法（BF）。

设计要点
--------
1. 进展因子支持四种选取方式：volume（金额加权，默认）、simple（简单平均）、
   median（中位数）、manual（精算手动指定每个进展期的因子）。
2. 尾部因子单独给出，可手动覆盖。
3. BF 法需要先验期望最终赔款，提供两种来源：显式传入先验，或用模拟器的
   真实参数生成的纯保费先验（页面上也允许手工调整）。
4. 大额赔案剔除后单独估计：大额件的未决用逐案估损（CASE）或手工指定金额，
   与常规三角形的估计结果相加。
5. 每个估计返回完整的因子表、每个起始季度的最新值/终极值/准备金，便于存档。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .triangles import Triangle

FACTOR_METHODS = ("volume", "simple", "median", "manual")


# ---------------------------------------------------------------------------
# 进展因子
# ---------------------------------------------------------------------------
def development_factors(tri: Triangle, method: str = "volume",
                        manual_factors: dict[int, float] | None = None,
                        tail_factor: float | None = None,
                        exclude_last_n: int = 0
                        ) -> pd.DataFrame:
    """计算各进展期的年龄-年龄因子。

    返回 DataFrame：dev（从 dev 到 dev+1 的因子）、volume_weighted、simple、
    median、selected、used_points。
    """
    df = tri.data
    devs = list(df.columns)
    rows = []
    for i in range(len(devs) - 1):
        d0, d1 = devs[i], devs[i + 1]
        x, y = df[d0], df[d1]
        mask = x.notna() & y.notna()
        if exclude_last_n:
            mask.iloc[-exclude_last_n:] = False
        x_v, y_v = x[mask], y[mask]
        # 分母整列为 0（如 dev 0 尚无支付）时因子无定义，标记 NaN
        if x_v.sum() <= 0:
            vw = np.nan
            ratios = pd.Series(dtype=float)
        else:
            vw = float(y_v.sum() / x_v.sum())
            ratios = (y_v / x_v).replace([np.inf, -np.inf], np.nan).dropna()
        sm = float(ratios.mean()) if len(ratios) else np.nan
        md = float(ratios.median()) if len(ratios) else np.nan
        rows.append({"dev": d0, "next_dev": d1, "n_points": int(mask.sum()),
                     "volume_weighted": vw, "simple": sm, "median": md})

    factors = pd.DataFrame(rows)

    def _pick(row):
        if method == "manual" and manual_factors is not None \
                and row["dev"] in manual_factors:
            return float(manual_factors[row["dev"]])
        if method == "simple":
            return row["simple"]
        if method == "median":
            return row["median"]
        return row["volume_weighted"]

    factors["selected"] = factors.apply(_pick, axis=1)
    # 无定义的因子（零分母列）视为 1.0：该进展期不产生增长
    factors["selected"] = factors["selected"].fillna(1.0)
    # 尾部：默认取最后一个选中因子的超额部分（factor-1）按 50% 折算，且 >=1
    last = factors["selected"].iloc[-1] if len(factors) else 1.0
    default_tail = max(1.0 + (last - 1.0) * 0.5, 1.0)
    factors.loc[len(factors)] = {
        "dev": devs[-1], "next_dev": "tail", "n_points": 0,
        "volume_weighted": np.nan, "simple": np.nan, "median": np.nan,
        "selected": float(tail_factor) if tail_factor is not None else default_tail,
    }
    factors["is_manual"] = (method == "manual")
    return factors


def cdf_from_factors(factors: pd.DataFrame) -> dict[int, float]:
    """由选中因子计算各进展期到终极的累计进展因子（CDF）。"""
    sel = factors["selected"].to_numpy()
    devs = factors["dev"].to_numpy()
    cdf: dict[int, float] = {}
    cum = 1.0
    for i in range(len(sel) - 1, -1, -1):
        cum *= sel[i]
        cdf[int(devs[i])] = float(cum)
    return cdf


# ---------------------------------------------------------------------------
# 估计结果容器
# ---------------------------------------------------------------------------
@dataclass
class ReserveEstimate:
    method: str
    triangle: Triangle
    factors: pd.DataFrame
    cdf: dict[int, float]
    detail: pd.DataFrame                # 每个 origin 季度一行
    total_reserve: float
    total_ultimate: float
    latest_total: float
    prior: pd.Series | None = None
    params: dict = field(default_factory=dict)
    large_claim_reserve: float = 0.0
    large_claim_detail: pd.DataFrame | None = None

    @property
    def total_reserve_with_large(self) -> float:
        return self.total_reserve + self.large_claim_reserve


# ---------------------------------------------------------------------------
# 链梯法
# ---------------------------------------------------------------------------
def chain_ladder(tri: Triangle, method: str = "volume",
                 manual_factors: dict[int, float] | None = None,
                 tail_factor: float | None = None,
                 exclude_last_n: int = 0) -> ReserveEstimate:
    """链梯法：ultimate = 最新对角线值 × 对应 CDF。"""
    factors = development_factors(tri, method, manual_factors,
                                  tail_factor, exclude_last_n)
    cdf = cdf_from_factors(factors)
    diag = tri.latest_diagonal()

    detail_rows = []
    max_dev = tri.data.columns.max()
    for origin_label in tri.data.index:
        row = tri.data.loc[origin_label]
        visible = row.dropna()
        if visible.empty:
            continue
        latest = float(visible.iloc[-1])
        cur_dev = int(visible.index[-1])
        # 最新值为 0（已报案未支付）：用下一期的 CDF，否则准备金会被算成 0
        if latest == 0.0 and cur_dev < max_dev:
            c = cdf.get(cur_dev + 1, 1.0)
        else:
            c = cdf.get(cur_dev, 1.0)
        ult = latest * c
        detail_rows.append({
            "origin": origin_label, "latest_dev": cur_dev,
            "latest": latest, "cdf": c,
            "ultimate": ult, "reserve": ult - latest,
        })
    detail = pd.DataFrame(detail_rows)
    return ReserveEstimate(
        method="CL", triangle=tri, factors=factors, cdf=cdf, detail=detail,
        total_reserve=float(detail["reserve"].sum()),
        total_ultimate=float(detail["ultimate"].sum()),
        latest_total=float(detail["latest"].sum()),
        params={"factor_method": method, "manual_factors": manual_factors or {},
                "tail_factor": tail_factor, "exclude_last_n": exclude_last_n},
    )


# ---------------------------------------------------------------------------
# BF 法
# ---------------------------------------------------------------------------
def bornhuetter_ferguson(tri: Triangle, prior_ultimate: pd.Series,
                         method: str = "volume",
                         manual_factors: dict[int, float] | None = None,
                         tail_factor: float | None = None,
                         exclude_last_n: int = 0,
                         prior_strength: float = 1.0) -> ReserveEstimate:
    """BF 法。

    prior_ultimate: index 为 origin 季度标签，值为先验期望最终赔款。
    公式：ultimate = latest + prior × (1 − 1/CDF) × prior_strength
    """
    factors = development_factors(tri, method, manual_factors,
                                  tail_factor, exclude_last_n)
    cdf = cdf_from_factors(factors)
    diag = tri.latest_diagonal()

    detail_rows = []
    max_dev = tri.data.columns.max()
    for origin_label in tri.data.index:
        row = tri.data.loc[origin_label]
        visible = row.dropna()
        if visible.empty:
            continue
        latest = float(visible.iloc[-1])
        cur_dev = int(visible.index[-1])
        c = cdf.get(cur_dev, 1.0)
        prior = float(prior_ultimate.get(origin_label, np.nan))
        unexpired = max(1.0 - 1.0 / c, 0.0) * prior_strength
        ult = latest + prior * unexpired
        detail_rows.append({
            "origin": origin_label, "latest_dev": cur_dev,
            "latest": latest, "cdf": c, "prior_ultimate": prior,
            "unexpired_pct": unexpired,
            "ultimate": ult, "reserve": ult - latest,
        })
    detail = pd.DataFrame(detail_rows)
    return ReserveEstimate(
        method="BF", triangle=tri, factors=factors, cdf=cdf, detail=detail,
        total_reserve=float(detail["reserve"].sum()),
        total_ultimate=float(detail["ultimate"].sum()),
        latest_total=float(detail["latest"].sum()),
        prior=prior_ultimate,
        params={"factor_method": method, "manual_factors": manual_factors or {},
                "tail_factor": tail_factor, "exclude_last_n": exclude_last_n,
                "prior_strength": prior_strength},
    )


# ---------------------------------------------------------------------------
# 先验：基于模拟器真实参数的纯保费先验
# ---------------------------------------------------------------------------
def parametric_prior(claims: pd.DataFrame, valuation_q: int,
                     basis: str = "accident",
                     group_dim: str | None = None,
                     group_value: str | None = None,
                     expected_loss_ratio: float = 1.0) -> pd.Series:
    """用真实参数生成先验期望最终赔款。

    实际工作中先验来自费率/纯保费/预算；这里直接用模拟器中各分段的
    频率×件均（对数正态均值 = exp(mu + sigma²/2)）按事故季度汇总，
    页面上允许按 ELR 系数调整。
    """
    from .config import default_segment_specs
    from .period_utils import quarter_label

    c = claims
    if group_dim and group_value:
        c = c[c[group_dim] == group_value]
    specs = {s.segment: s for s in default_segment_specs()}
    origin_col = "accident_q" if basis == "accident" else "report_q"

    # 用每个分段实际件数 × 该分段理论件均，还原"参数先验"
    c = c.copy()
    c["_theo_mean"] = c["segment"].map(
        lambda sg: float(np.exp(specs[sg].severity_mu
                                + specs[sg].severity_sigma ** 2 / 2))
        if sg in specs else np.nan)
    # 拒赔赔案 true_ultimate=0，先验按非拒赔率折算
    deny_adj = c["segment"].map(
        lambda sg: 1.0 - specs[sg].deny_prob if sg in specs else 1.0)
    c["_prior"] = c["_theo_mean"] * deny_adj * expected_loss_ratio
    prior = c.groupby(origin_col)["_prior"].sum()
    prior.index = [quarter_label(int(q)) for q in prior.index]
    prior.name = "prior_ultimate"
    return prior


# ---------------------------------------------------------------------------
# 大额赔案单独处理
# ---------------------------------------------------------------------------
def large_claim_reserve(claims: pd.DataFrame, txns: pd.DataFrame,
                        valuation_date, threshold: float,
                        manual_override: dict[int, float] | None = None
                        ) -> tuple[float, pd.DataFrame]:
    """大额赔案逐案估计。

    默认未决 = 最近一次 CASE 估损；可通过 manual_override 按 claim_id 手工指定。
    已决部分不进准备金，只汇总未决。
    """
    vd = pd.Timestamp(valuation_date)
    large = claims[(claims["true_ultimate"] >= threshold)
                   & (claims["report_date"] <= vd)].copy()
    if large.empty:
        return 0.0, pd.DataFrame(columns=["claim_id", "product_line",
                                          "true_ultimate", "case_outstanding",
                                          "manual_reserve", "reserve"])
    cut = txns[(txns["claim_id"].isin(large["claim_id"]))
               & (txns["txn_date"] <= vd)]
    last_case = (cut[cut["txn_type"].isin(["CASE", "CLOSE", "REOPEN"])]
                 .sort_values("txn_date").groupby("claim_id").tail(1)
                 .set_index("claim_id")["case_estimate"])
    rows = []
    for _, r in large.iterrows():
        cid = r["claim_id"]
        case_val = float(last_case.get(cid, 0.0))
        man = manual_override.get(cid) if manual_override else None
        reserve = float(man) if man is not None else case_val
        rows.append({"claim_id": cid, "product_line": r["product_line"],
                     "true_ultimate": r["true_ultimate"],
                     "case_outstanding": case_val,
                     "manual_reserve": man, "reserve": max(reserve, 0.0)})
    detail = pd.DataFrame(rows)
    return float(detail["reserve"].sum()), detail
