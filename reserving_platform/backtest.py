"""历史回溯检验（backtesting）。

在若干历史估值日上重跑完整的准备金估计流程，回答两个问题：

1. **预测 vs 后续实际**：当时估计的终极赔款，与再观察 ``horizon`` 个季度后的
   实际已发生/已决进展相比，偏差多少。
2. **估计 vs 标准答案**：模拟器知道每个赔案的真实最终赔款 ``true_ultimate``，
   直接比较估计终极与真实终极的偏差（实际工作中用多年后结案的最终数据替代）。

为控制运行时间，估值点按 ``step`` 个季度抽样（默认每 2 个季度一个点）。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .cleaning import add_quarter_columns
from .period_utils import quarter_end, quarter_label
from .reserving import (bornhuetter_ferguson, chain_ladder,
                        parametric_prior)
from .triangles import build_triangle


@dataclass
class BacktestResult:
    runs: pd.DataFrame                  # 每个估值点一行汇总
    detail: pd.DataFrame                # 每个估值点 × origin 季度
    metric: str
    method: str

    def summary(self) -> pd.DataFrame:
        cols = ["valuation_q", "est_ultimate", "true_ultimate",
                "actual_to_horizon", "bias_vs_true", "bias_pct_vs_true",
                "bias_vs_actual", "bias_pct_vs_actual"]
        return self.runs[cols]


def _cut_data(claims: pd.DataFrame, txns: pd.DataFrame, vq: int):
    """截断到某估值季度末（输入需已含季度列）。"""
    vd = quarter_end(vq)
    c = claims[claims["report_date"] <= vd].copy()
    t = txns[txns["txn_date"] <= vd].copy()
    return c, t, vd


def run_backtest(claims: pd.DataFrame, txns: pd.DataFrame,
                 method: str = "CL", basis: str = "accident",
                 metric: str = "paid",
                 valuation_q: int | None = None,
                 n_points: int = 6, step: int = 2,
                 horizon: int = 4,
                 factor_method: str = "volume",
                 manual_factors: dict | None = None,
                 tail_factor: float | None = None,
                 group_dim: str | None = None,
                 group_value: str | None = None,
                 elr: float = 1.0) -> BacktestResult:
    """执行回溯检验。

    n_points: 估值点个数（从最早可建三角形的季度向估值日等步长取样）
    horizon:  每个估值点向前观察多少个季度，作为"后续实际"
    """
    claims, txns = add_quarter_columns(claims, txns)
    max_q = valuation_q or int(txns["txn_q"].max())
    min_q = int(txns["accident_q"].min())
    # 至少留出 4 个进展期才能估计因子
    first_vq = min_q + 4
    candidate = list(range(first_vq, max_q - horizon + 1, step))
    if len(candidate) > n_points:
        idx = np.linspace(0, len(candidate) - 1, n_points).astype(int)
        candidate = [candidate[i] for i in sorted(set(idx))]

    run_rows, detail_rows = [], []
    for vq in candidate:
        c, t, vd = _cut_data(claims, txns, vq)
        if group_dim and group_value:
            c = c[c[group_dim] == group_value]
            t = t[t[group_dim] == group_value]
        if len(c) < 50:
            continue
        tri = build_triangle(c, t, basis=basis, metric=metric,
                             valuation_q=vq)
        if tri.data.shape[1] < 3:
            continue

        if method == "CL":
            est = chain_ladder(tri, factor_method, manual_factors, tail_factor)
        else:
            prior = parametric_prior(c, vq, basis=basis,
                                     group_dim=group_dim,
                                     group_value=group_value,
                                     expected_loss_ratio=elr)
            est = bornhuetter_ferguson(tri, prior, factor_method,
                                       manual_factors, tail_factor)

        # 标准答案：截断赔案的真实终极赔款（模拟上帝视角）
        true_ult = float(c["true_ultimate"].sum())

        # 后续实际：horizon 季度后的实际累计值
        hq = min(vq + horizon, max_q)
        ch, th, _ = _cut_data(claims, txns, hq)
        if group_dim and group_value:
            ch = ch[ch[group_dim] == group_value]
            th = th[th[group_dim] == group_value]
        # 只统计估值日已报案的赔案，保持口径可比
        known_ids = c["claim_id"]
        th = th[th["claim_id"].isin(known_ids)]
        ch = ch[ch["claim_id"].isin(known_ids)]
        tri_h = build_triangle(ch, th, basis=basis, metric=metric,
                               valuation_q=hq)
        actual_h = float(tri_h.latest_diagonal().reindex(
            est.detail["origin"]).fillna(0.0).sum())

        run_rows.append({
            "valuation_q": vq,
            "valuation_date": quarter_label(vq),
            "n_claims": len(c),
            "est_ultimate": est.total_ultimate,
            "true_ultimate": true_ult,
            "actual_to_horizon": actual_h,
            "latest_at_valuation": est.latest_total,
            "est_reserve": est.total_reserve,
            "bias_vs_true": est.total_ultimate - true_ult,
            "bias_pct_vs_true": (est.total_ultimate - true_ult) / true_ult,
            "bias_vs_actual": est.total_ultimate - actual_h,
            "bias_pct_vs_actual": ((est.total_ultimate - actual_h) / actual_h
                                   if actual_h else np.nan),
        })
        d = est.detail.copy()
        d["valuation_q"] = vq
        origin_col = "accident_q" if basis == "accident" else "report_q"
        true_by_origin = (c.groupby(origin_col)["true_ultimate"].sum())
        true_by_origin.index = [quarter_label(int(q))
                                for q in true_by_origin.index]
        d["true_ultimate_by_origin"] = d["origin"].map(true_by_origin)
        detail_rows.append(d)

    runs = pd.DataFrame(run_rows)
    detail = pd.concat(detail_rows, ignore_index=True) if detail_rows \
        else pd.DataFrame()
    return BacktestResult(runs, detail, metric, method)
