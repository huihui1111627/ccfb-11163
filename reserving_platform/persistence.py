"""模拟数据的 Parquet 持久化与加载。

实际项目中理赔系统的流水以文件/数仓表落地；本模块把模拟器输出按
``claims.parquet`` / ``transactions.parquet`` 存盘，页面与脚本均可从文件加载，
不必每次重新模拟。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from reserving_platform.config import SimConfig
from reserving_platform.simulator import SimulationResult, simulate


def export_parquet(sim: SimulationResult, out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    sim.claims.to_parquet(out / "claims.parquet", index=False)
    sim.transactions.to_parquet(out / "transactions.parquet", index=False)
    pd.DataFrame([
        {"key": "seed", "value": str(sim.config.seed)},
        {"key": "scale", "value": str(sim.config.scale)},
        {"key": "valuation_date", "value": str(sim.config.valuation_date)},
    ]).to_parquet(out / "sim_meta.parquet", index=False)
    return out


def load_parquet(in_dir: str | Path) -> SimulationResult:
    p = Path(in_dir)
    claims = pd.read_parquet(p / "claims.parquet")
    txns = pd.read_parquet(p / "transactions.parquet")
    meta_df = pd.read_parquet(p / "sim_meta.parquet")
    meta = dict(zip(meta_df["key"], meta_df["value"]))
    cfg = SimConfig(seed=int(meta["seed"]), scale=float(meta["scale"]))
    for col in ("accident_date", "report_date", "close_date"):
        if col in claims.columns:
            claims[col] = pd.to_datetime(claims[col])
    if "txn_date" in txns.columns:
        txns["txn_date"] = pd.to_datetime(txns["txn_date"])
    return SimulationResult(claims, txns,
                            issue_manifest={"loaded_from_parquet": 1},
                            config=cfg)


def generate_and_export(out_dir: str | Path = "data/generated",
                        scale: float = 0.3, seed: int = 20261116
                        ) -> SimulationResult:
    sim = simulate(SimConfig(scale=scale, seed=seed))
    export_parquet(sim, out_dir)
    return sim
