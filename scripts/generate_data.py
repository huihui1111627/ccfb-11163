"""生成模拟数据并导出 Parquet。

用法：
    python -m scripts.generate_data --scale 0.3 --seed 20261116 --out data/generated
"""
from __future__ import annotations

import argparse

from reserving_platform.persistence import generate_and_export


def main() -> None:
    ap = argparse.ArgumentParser(description="生成五年理赔模拟数据")
    ap.add_argument("--scale", type=float, default=0.3,
                    help="频率缩放系数（1.0 约 9 万件）")
    ap.add_argument("--seed", type=int, default=20261116)
    ap.add_argument("--out", default="data/generated")
    args = ap.parse_args()

    sim = generate_and_export(args.out, args.scale, args.seed)
    print(f"已生成并导出到 {args.out}/")
    print(f"  赔案 {len(sim.claims):,} 件，流水 {len(sim.transactions):,} 条")
    print(f"  注入脏数据: {sim.issue_manifest}")
    print(f"  真实最终赔款合计: {sim.claims['true_ultimate'].sum():,.2f} 元")


if __name__ == "__main__":
    main()
