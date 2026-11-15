"""进展三角形构建。

支持三种口径（``basis``）：

- ``accident`` 出险口径：行为事故季度，进展期 = 事件季度 − 事故季度
- ``report``   报案口径：行为报案季度，进展期 = 事件季度 − 报案季度
- ``payment``  支付口径：行为支付季度（现金流视角），进展期为支付季度内的季度序号

每种口径可输出三类指标（``metric``）：

- ``paid``     累计已决赔款（PAY 为正、RECOV 为负，自动轧差）
- ``incurred`` 累计已发生 = 已决 + 期末未决估损（CASE 快照取每个进展期最后值）
- ``count``    赔案件数（口径起点季度新增的赔案数；支付口径为发生支付的赔案数）

未决估损的调整按 **CASE 流水的调整日期** 落入对应进展期——这是本模块的关键规则。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .period_utils import quarter_label


VALID_BASES = ("accident", "report", "payment")
VALID_METRICS = ("paid", "incurred", "count")


@dataclass
class Triangle:
    """一个进展三角形（累计值，下三角为 NaN）。"""

    data: pd.DataFrame                  # index=origin 季度标签, columns=进展期
    basis: str
    metric: str
    group_value: str = "ALL"
    group_dim: str = "ALL"
    is_incremental: bool = False

    @property
    def origin_quarters(self) -> list[int]:
        return list(self.data.index.astype(int))

    def latest_diagonal(self) -> pd.Series:
        """取每行在估值日所处的最新进展期值。"""
        vals = {}
        for origin in self.data.index:
            row = self.data.loc[origin].dropna()
            if len(row):
                vals[origin] = row.iloc[-1]
        return pd.Series(vals, name="latest")

    def to_incremental(self) -> "Triangle":
        """累计三角形转增量三角形。"""
        d = self.data.copy()
        d.iloc[:, 1:] = d.iloc[:, 1:].values - d.iloc[:, :-1].values
        return Triangle(d, self.basis, self.metric, self.group_value,
                        self.group_dim, is_incremental=True)


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _origin_and_dev(txns: pd.DataFrame, claims: pd.DataFrame,
                    basis: str, valuation_q: int) -> pd.DataFrame:
    """给每条流水挂上 origin 季度与进展期。"""
    t = txns.copy()
    if basis == "accident":
        t["origin_q"] = t["accident_q"]
        t["dev"] = t["txn_q"] - t["accident_q"]
    elif basis == "report":
        t["origin_q"] = t["report_q"]
        t["dev"] = t["txn_q"] - t["report_q"]
        # 估值日之后才报案的赔案属于 IBNR，在报案口径下整体不可见
        reported = claims.loc[claims["report_q"] <= valuation_q, "claim_id"]
        t = t[t["claim_id"].isin(reported)]
    elif basis == "payment":
        # 支付口径：origin 就是支付季度本身，全部落在进展期 0；
        # 行含义为"该季度发生的支付"，常用于现金流/支付模式分析
        t["origin_q"] = t["txn_q"]
        t["dev"] = 0
    else:
        raise ValueError(f"未知口径: {basis}")
    t = t[(t["origin_q"] <= valuation_q) & (t["dev"] >= 0)]
    return t


def _case_outstanding_by_cell(t: pd.DataFrame) -> pd.DataFrame:
    """未决估损快照：每个 (赔案, origin, dev) 单元格取该期最后一条 CASE 值。

    CASE 流水是快照语义（值 = 调整日时点的未决估损），CLOSE/REOPEN 通过
    0 值快照参与；同一进展期内多次调整只保留最后一次。
    """
    case_events = t[t["txn_type"].isin(["CASE", "CLOSE", "REOPEN"])].copy()
    case_events["case_val"] = np.where(
        case_events["txn_type"] == "CASE",
        case_events["case_estimate"], 0.0)
    case_events = (case_events.sort_values(["claim_id", "txn_date", "txn_id"])
                   .groupby(["claim_id", "origin_q", "dev"], as_index=False)
                   .tail(1))
    return case_events


def _pivot(agg: pd.DataFrame, values: str, valuation_q: int,
           max_dev: int | None,
           all_origins: pd.DataFrame | None = None) -> pd.DataFrame:
    """长表透视成三角形增量表，并遮蔽估值日之后的单元格。

    可见性按**单元格的事件季度** origin+dev 判断：不可见 -> NaN；
    可见但无流水 -> 0（由累计逻辑负责区分）。
    """
    pivot = agg.pivot_table(index="origin_q", columns="dev",
                            values=values, aggfunc="sum")
    if all_origins is not None:
        pivot = pivot.reindex(all_origins["origin_q"])
    if max_dev is None:
        max_dev = int(pivot.columns.max()) if len(pivot.columns) else 0
    pivot = pivot.reindex(columns=range(0, max_dev + 1))

    origins = pivot.columns.to_numpy() * 0 + pivot.index.to_numpy().reshape(-1, 1)
    event_q = origins + pivot.columns.to_numpy().reshape(1, -1)
    visible = event_q <= valuation_q
    # 可见区域内的缺失值填 0，不可见区域保持 NaN
    pivot = pivot.where(~visible, pivot.fillna(0.0))
    pivot.index = [quarter_label(int(q)) for q in pivot.index]
    pivot.index.name = "origin"
    pivot.columns.name = "dev"
    return pivot


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def build_triangle(claims: pd.DataFrame, txns: pd.DataFrame,
                   basis: str = "accident", metric: str = "paid",
                   valuation_q: int | None = None,
                   group_dim: str | None = None, group_value: str | None = None,
                   exclude_large: bool = False,
                   large_threshold: float | None = None,
                   max_dev: int | None = None) -> Triangle:
    """构建单个进展三角形。

    Parameters
    ----------
    basis: accident / report / payment
    metric: paid / incurred / count
    group_dim, group_value: 按产品线或渠道切分，None 表示全体
    exclude_large: 是否剔除大额赔案（剔除件单独处理，见 reserving 模块）
    large_threshold: 大额阈值；None 时使用 claims 中已有的 ``is_large`` 标记
    """
    if basis not in VALID_BASES:
        raise ValueError(f"basis 必须是 {VALID_BASES} 之一")
    if metric not in VALID_METRICS:
        raise ValueError(f"metric 必须是 {VALID_METRICS} 之一")
    if valuation_q is None:
        valuation_q = int(txns["txn_q"].max())

    c, t = claims, txns
    if group_dim and group_value:
        c = c[c[group_dim] == group_value]
        t = t[t[group_dim] == group_value]

    if exclude_large:
        if large_threshold is not None:
            large_ids = c.loc[c["true_ultimate"] >= large_threshold, "claim_id"]
        else:
            large_ids = c.loc[c["is_large"], "claim_id"]
        c = c[~c["claim_id"].isin(large_ids)]
        t = t[~t["claim_id"].isin(large_ids)]

    label = group_value or "ALL"
    dim = group_dim or "ALL"

    # ---------------- 件数三角形 ----------------
    if metric == "count":
        if basis == "payment":
            pay = t[t["txn_type"].isin(["PAY", "RECOV"])]
            cells = (pay.groupby(["origin_q", "claim_id"]).size()
                     .reset_index(name="v").groupby("origin_q")
                     .size().reset_index(name="v"))
            cells["dev"] = 0
        else:
            origin_col = "accident_q" if basis == "accident" else "report_q"
            c_count = c
            if basis == "report":
                c_count = c[c["report_q"] <= valuation_q]
            cells = (c_count.groupby(origin_col)["claim_id"].nunique()
                     .reset_index())
            cells.columns = ["origin_q", "v"]
            cells["dev"] = 0
        data = _pivot(cells, "v", valuation_q, max_dev or 0)
        return Triangle(data, basis, metric, label, dim)

    t = _origin_and_dev(t, c, basis, valuation_q)
    money = t[t["txn_type"].isin(["PAY", "RECOV"])].copy()
    money["paid_amt"] = money["amount"]  # RECOV 本身为负

    # 全部 origin 季度（含已报案但尚无支付的新季度，补零行，
    # 避免链梯把这类事故季度整体漏掉）
    if basis == "payment":
        origins = t.loc[t["txn_q"] <= valuation_q, "txn_q"].unique()
    elif basis == "report":
        origins = c.loc[c["report_q"] <= valuation_q, "report_q"].unique()
    else:
        origins = c["accident_q"].unique()
    all_origins = pd.DataFrame({"origin_q": sorted(origins)})

    # ---------------- 已决三角形 ----------------
    if metric == "paid":
        agg = (money.groupby(["origin_q", "dev"], as_index=False)["paid_amt"]
               .sum().rename(columns={"paid_amt": "v"}))
        data = _pivot(agg, "v", valuation_q, max_dev, all_origins)
        # 可见区已是 0/数值、不可见区为 NaN；cumsum 仅沿可见区累计
        data = data.cumsum(axis=1)
        return Triangle(data, basis, metric, label, dim)

    # ---------------- 已发生（已决 + 未决）三角形 ----------------
    paid_cells = (money.groupby(["origin_q", "dev"], as_index=False)["paid_amt"]
                  .sum().rename(columns={"paid_amt": "paid_v"}))
    case_cells = (_case_outstanding_by_cell(t)
                  .groupby(["origin_q", "dev"], as_index=False)["case_val"]
                  .sum().rename(columns={"case_val": "case_v"}))
    cells = paid_cells.merge(case_cells, on=["origin_q", "dev"], how="outer")
    cells[["paid_v", "case_v"]] = cells[["paid_v", "case_v"]].fillna(0.0)

    # 已决按增量累计；未决是期末快照直接相加
    paid_pivot = _pivot(cells.rename(columns={"paid_v": "v"}), "v",
                        valuation_q, max_dev, all_origins)
    case_pivot = _pivot(cells.rename(columns={"case_v": "v"}), "v",
                        valuation_q, max_dev, all_origins)
    incurred = paid_pivot.cumsum(axis=1) + case_pivot
    return Triangle(incurred, basis, metric, label, dim)


def build_segment_triangles(claims: pd.DataFrame, txns: pd.DataFrame,
                            group_dim: str, basis: str, metric: str,
                            valuation_q: int,
                            min_claims: int = 200,
                            exclude_large: bool = False,
                            large_threshold: float | None = None
                            ) -> dict[str, Triangle]:
    """按维度切分三角形；样本量低于 ``min_claims`` 的分段并入"其他"组合并。

    合并策略：先按件数排序，不足阈值的分段全部归入 ``其他(<维度>)``，
    用合并后的流水重建一个三角形，保证小样本不单独出估计。
    """
    counts = claims.groupby(group_dim)["claim_id"].nunique()
    keep = counts[counts >= min_claims].index.tolist()
    merge_values = counts[counts < min_claims].index.tolist()

    out: dict[str, Triangle] = {}
    for value in keep:
        out[value] = build_triangle(
            claims, txns, basis, metric, valuation_q,
            group_dim=group_dim, group_value=value,
            exclude_large=exclude_large, large_threshold=large_threshold)

    if merge_values:
        merged_c = claims[claims[group_dim].isin(merge_values)].copy()
        merged_t = txns[txns[group_dim].isin(merge_values)].copy()
        merged_name = f"其他({group_dim})"
        out[merged_name] = build_triangle(
            merged_c, merged_t, basis, metric, valuation_q,
            group_dim=None, group_value=merged_name,
            exclude_large=exclude_large, large_threshold=large_threshold)
    return out


def triangle_long_table(tri: Triangle) -> pd.DataFrame:
    """三角形转长表，方便页面画折线与导出。"""
    df = tri.data.reset_index().melt(id_vars="origin", var_name="dev",
                                     value_name="value")
    df = df.dropna(subset=["value"]).sort_values(["origin", "dev"])
    df["basis"] = tri.basis
    df["metric"] = tri.metric
    df["group"] = tri.group_value
    return df.reset_index(drop=True)
