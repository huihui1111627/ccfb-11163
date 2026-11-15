"""端到端流水线演示（不依赖页面）。

运行：python -m scripts.run_demo
输出：模拟 -> 清洗 -> 三角形 -> CL/BF 估计 -> 与真实终极对比 -> 回溯检验 -> 存档
"""
from __future__ import annotations

import pandas as pd

from reserving_platform.archive import list_runs, save_run
from reserving_platform.backtest import run_backtest
from reserving_platform.cleaning import add_quarter_columns, clean, quality_checks
from reserving_platform.config import DEFAULT_LARGE_THRESHOLD, SimConfig
from reserving_platform.reserving import (bornhuetter_ferguson, chain_ladder,
                                          large_claim_reserve,
                                          parametric_prior)
from reserving_platform.simulator import simulate
from reserving_platform.triangles import build_triangle

DB_PATH = "data/archive/demo_runs.db"


def main() -> None:
    pd.set_option("display.float_format", lambda x: f"{x:,.2f}")

    print("=" * 70)
    print("1. 模拟五年理赔流水")
    print("=" * 70)
    sim = simulate(SimConfig(scale=0.3))
    print(f"赔案件数: {len(sim.claims):,}  流水条数: {len(sim.transactions):,}")
    print("注入脏数据:", sim.issue_manifest)

    print("\n" + "=" * 70)
    print("2. 数据质量检查与清洗")
    print("=" * 70)
    qc = quality_checks(sim.claims, sim.transactions)
    print(qc.to_string(index=False))
    claims, txns, quarantined, report = clean(sim.claims, sim.transactions)
    claims, txns = add_quarter_columns(claims, txns)
    print(f"\n清洗后: 赔案 {len(claims):,} 件, 流水 {len(txns):,} 条, "
          f"隔离 {len(quarantined):,} 条")

    vq = sim.config.valuation_q
    print("\n" + "=" * 70)
    print("3. 进展三角形（出险口径 / 累计已决，单位：元）")
    print("=" * 70)
    tri = build_triangle(claims, txns, "accident", "paid", vq,
                         exclude_large=True,
                         large_threshold=DEFAULT_LARGE_THRESHOLD)
    print(tri.data.iloc[:, :8].to_string())

    print("\n" + "=" * 70)
    print("4. 准备金估计（剔除大额，常规三角形）")
    print("=" * 70)
    cl = chain_ladder(tri)
    print("\n[链梯法] 进展因子:")
    print(cl.factors[["dev", "n_points", "volume_weighted",
                      "simple", "median", "selected"]].to_string(index=False))
    prior = parametric_prior(claims, vq)
    bf = bornhuetter_ferguson(tri, prior)
    print(f"\n链梯法  常规终极: {cl.total_ultimate:,.0f}  "
          f"准备金: {cl.total_reserve:,.0f}")
    print(f"BF 法   常规终极: {bf.total_ultimate:,.0f}  "
          f"准备金: {bf.total_reserve:,.0f}")

    lg_reserve, lg_detail = large_claim_reserve(
        claims, txns, sim.config.valuation_date, DEFAULT_LARGE_THRESHOLD)
    print(f"\n大额赔案 {len(lg_detail)} 件, 逐案未决准备金: {lg_reserve:,.0f}")
    print(f"合计准备金 CL: {cl.total_reserve_with_large + lg_reserve:,.0f}")

    print("\n" + "=" * 70)
    print("5. 与模拟器标准答案（真实最终赔款）对比")
    print("=" * 70)
    true_ult = claims["true_ultimate"].sum()
    # 大额件的真实终极单独加回，保持口径一致
    lg_true = lg_detail["true_ultimate"].sum() if len(lg_detail) else 0.0
    for name, est in [("链梯法", cl), ("BF 法", bf)]:
        total = est.total_ultimate + lg_true
        bias = total - true_ult
        print(f"{name}: 估计终极 {total:,.0f} | 真实终极 {true_ult:,.0f} | "
              f"偏差 {bias:,.0f} ({bias / true_ult:+.2%})")

    print("\n" + "=" * 70)
    print("6. 历史回溯检验（CL，6 个估值点，观察期 4 个季度）")
    print("=" * 70)
    bt = run_backtest(claims, txns, method="CL", n_points=6, horizon=4)
    print(bt.summary().to_string(index=False))
    mape = bt.runs["bias_pct_vs_true"].abs().mean()
    print(f"\n平均绝对百分比偏差 MAPE: {mape:.2%}")

    print("\n" + "=" * 70)
    print("7. 存档")
    print("=" * 70)
    cl.large_claim_reserve = lg_reserve
    run_id = save_run(cl, valuation_date=str(sim.config.valuation_date),
                      basis="accident", note="demo 链梯法（剔除大额）",
                      db_path=DB_PATH, large_detail=lg_detail)
    print(f"已存档 run_id={run_id}")
    print(list_runs(DB_PATH)[["run_id", "method", "basis",
                              "total_reserve_with_large"]].to_string(index=False))


if __name__ == "__main__":
    main()
