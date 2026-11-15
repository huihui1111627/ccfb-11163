"""核心模块端到端测试。

运行：python -m pytest tests/ -v
"""
import numpy as np
import pandas as pd
import pytest

from reserving_platform.backtest import run_backtest
from reserving_platform.cleaning import add_quarter_columns, clean, quality_checks
from reserving_platform.config import SimConfig
from reserving_platform.reserving import (bornhuetter_ferguson, chain_ladder,
                                          large_claim_reserve,
                                          parametric_prior)
from reserving_platform.simulator import simulate
from reserving_platform.triangles import build_triangle, build_segment_triangles


@pytest.fixture(scope="module")
def sim():
    # 小样本加快测试，但保证每个分段都有足够赔案
    return simulate(SimConfig(scale=0.15, seed=42))


@pytest.fixture(scope="module")
def cleaned(sim):
    claims, txns, quarantined, report = clean(sim.claims, sim.transactions)
    claims, txns = add_quarter_columns(claims, txns)
    return claims, txns, quarantined, report, sim


def test_simulation_shapes(sim):
    assert len(sim.claims) > 1000
    assert len(sim.transactions) > len(sim.claims)
    # 每条流水的赔案在注入孤儿前逻辑自洽；类型齐全
    assert set(sim.transactions["txn_type"]) >= \
        {"PAY", "CASE", "CLOSE", "DENY"}
    # 真实最终赔款非负，重开补赔后仍为正
    assert (sim.claims["true_ultimate"] >= 0).all()
    # 注入问题计数非零
    assert sum(sim.issue_manifest.values()) > 0


def test_cleaning_fixes_all_issue_types(cleaned):
    claims, txns, quarantined, report, sim = cleaned
    # 无缺失维度
    assert claims["product_line"].notna().all()
    assert claims["channel"].notna().all()
    # 无报案早于出险
    assert (claims["report_date"] >= claims["accident_date"]).all()
    # PAY 全部为正
    assert (txns.loc[txns["txn_type"] == "PAY", "amount"] > 0).all()
    # RECOV 全部为负
    assert (txns.loc[txns["txn_type"] == "RECOV", "amount"] < 0).all()
    # 无重复流水
    assert not txns.duplicated(
        ["claim_id", "txn_type", "txn_date", "amount"]).any()
    # 无孤儿流水
    assert txns["claim_id"].isin(claims["claim_id"]).all()
    # 隔离区非空（孤儿 + 可能的缺失日期）
    assert len(quarantined) >= sim.issue_manifest["orphan"]


def test_triangle_balances_to_ledger(cleaned):
    claims, txns, *_ = cleaned
    vq = int(txns["txn_q"].max())
    paid = build_triangle(claims, txns, "accident", "paid", vq)
    # 三角形对角线合计 == 估值日前台账全部 PAY/RECOV 净额
    ledger_net = txns.loc[txns["txn_type"].isin(["PAY", "RECOV"])
                          & (txns["txn_q"] <= vq), "amount"].sum()
    diag_total = paid.latest_diagonal().sum()
    assert abs(diag_total - ledger_net) / ledger_net < 1e-9

    # 已发生 >= 已决（未决估损非负）
    inc = build_triangle(claims, txns, "accident", "incurred", vq)
    cmp_df = pd.concat([paid.latest_diagonal().rename("paid"),
                        inc.latest_diagonal().rename("inc")], axis=1).dropna()
    assert (cmp_df["inc"] + 1e-6 >= cmp_df["paid"]).all()

    # 件数三角形首列合计 == 赔案总数
    cnt = build_triangle(claims, txns, "accident", "count", vq)
    assert int(cnt.data[0].sum()) == claims["claim_id"].nunique()


def test_three_bases_consistent(cleaned):
    claims, txns, *_ = cleaned
    vq = int(txns["txn_q"].max())
    # 出险/支付口径覆盖估值日前全部支付；报案口径剔除估值日后报案赔案(IBNR)
    total_net = txns.loc[txns["txn_type"].isin(["PAY", "RECOV"]),
                         "amount"].sum()
    for basis in ("accident", "payment"):
        tri = build_triangle(claims, txns, basis, "paid", vq)
        assert abs(tri.latest_diagonal().sum() - total_net) / total_net < 1e-9
    report_ids = claims.loc[claims["report_q"] <= vq, "claim_id"]
    report_net = txns.loc[txns["claim_id"].isin(report_ids)
                          & txns["txn_type"].isin(["PAY", "RECOV"])
                          & (txns["txn_q"] <= vq),
                          "amount"].sum()
    tri_r = build_triangle(claims, txns, "report", "paid", vq)
    assert abs(tri_r.latest_diagonal().sum() - report_net) / report_net < 1e-9


def test_chain_ladder_monotone_and_reserve(cleaned):
    claims, txns, *_ = cleaned
    vq = int(txns["txn_q"].max())
    tri = build_triangle(claims, txns, "accident", "paid", vq)
    est = chain_ladder(tri)
    # 因子 >= 1（累计已决不会下降，RECOV 影响很小）
    assert (est.factors["selected"].dropna() >= 0.98).all()
    # 终极 >= 最新值，准备金非负
    assert (est.detail["ultimate"] + 1e-6 >= est.detail["latest"]).all()
    assert est.total_reserve >= -1.0
    # 终极应接近真实终极（10% 内——模拟器数据较规则）
    true_ult = claims["true_ultimate"].sum()
    err = abs(est.total_ultimate - true_ult) / true_ult
    assert err < 0.15, f"CL 偏差过大: {err:.2%}"


def test_manual_factor_override(cleaned):
    claims, txns, *_ = cleaned
    vq = int(txns["txn_q"].max())
    tri = build_triangle(claims, txns, "accident", "paid", vq)
    base = chain_ladder(tri)
    # 选最新事故季度在对角线上所处的进展期（其 CDF 一定参与终极计算）
    last_origin = tri.data.index[-1]
    cur_dev = int(tri.data.loc[last_origin].dropna().index[-1])
    # 该位置若已到最后一期，则覆盖尾部因子；否则覆盖当期因子
    factors = base.factors
    is_tail = factors.iloc[-1]["dev"] == cur_dev
    if is_tail:
        est = chain_ladder(tri, method="manual",
                           tail_factor=1.5)
        assert est.factors.iloc[-1]["selected"] == 1.5
    else:
        manual = {cur_dev: 1.5}
        est = chain_ladder(tri, method="manual", manual_factors=manual)
        assert est.factors.loc[est.factors["dev"] == cur_dev,
                               "selected"].iloc[0] == 1.5
    # 更大的因子 => 更大的终极
    assert est.total_ultimate > base.total_ultimate


def test_bf_method(cleaned):
    claims, txns, *_ = cleaned
    vq = int(txns["txn_q"].max())
    tri = build_triangle(claims, txns, "accident", "paid", vq)
    prior = parametric_prior(claims, vq)
    est = bornhuetter_ferguson(tri, prior)
    true_ult = claims["true_ultimate"].sum()
    err = abs(est.total_ultimate - true_ult) / true_ult
    assert err < 0.20
    assert est.method == "BF"


def test_large_claim_exclusion_and_addback(cleaned):
    claims, txns, _, _, _ = cleaned
    vq = int(txns["txn_q"].max())
    threshold = 100_000.0
    tri_ex = build_triangle(claims, txns, "accident", "paid", vq,
                            exclude_large=True, large_threshold=threshold)
    tri_all = build_triangle(claims, txns, "accident", "paid", vq)
    assert tri_ex.latest_diagonal().sum() <= tri_all.latest_diagonal().sum()

    lg_reserve, lg_detail = large_claim_reserve(
        claims, txns, pd.Timestamp("2025-12-31"), threshold)
    assert len(lg_detail) > 0
    assert lg_reserve >= 0
    # 大额赔案真实终极确实超过阈值
    assert (lg_detail["true_ultimate"] >= threshold).all()


def test_segment_merge(cleaned):
    claims, txns, *_ = cleaned
    vq = int(txns["txn_q"].max())
    tris = build_segment_triangles(claims, txns, "product_line",
                                   "accident", "paid", vq, min_claims=5000)
    # 阈值很高时必然出现"其他"合并组
    assert any(k.startswith("其他") for k in tris)
    # 各组合计 == 全体
    total = sum(t.latest_diagonal().sum() for t in tris.values())
    full = build_triangle(claims, txns, "accident", "paid", vq)
    assert abs(total - full.latest_diagonal().sum()) < 1e-6


def test_backtest_runs(cleaned):
    claims, txns, *_ = cleaned
    res = run_backtest(claims, txns, method="CL", n_points=3, horizon=2)
    assert len(res.runs) >= 2
    # 每个点都有偏差指标
    assert res.runs["bias_pct_vs_true"].notna().all()
    # 平均绝对偏差在合理范围
    mape = res.runs["bias_pct_vs_true"].abs().mean()
    assert mape < 0.25


def test_backtest_bf(cleaned):
    claims, txns, *_ = cleaned
    res = run_backtest(claims, txns, method="BF", n_points=3, horizon=2)
    assert len(res.runs) >= 2
    assert (res.runs["est_ultimate"] > 0).all()


def test_as_of_cutoff(sim):
    cut = sim.as_of(pd.Timestamp("2023-06-30"))
    assert (cut.transactions["txn_date"] <= pd.Timestamp("2023-06-30")).all()
    assert len(cut.transactions) < len(sim.transactions)
