"""估算存档：每次准备金估算的参数选择与结果完整落库。

使用 SQLite（单文件、零依赖），一次估算（run）包含：

- ``runs``        主表：时间、方法、口径、指标、分组、参数 JSON、汇总金额
- ``run_factors`` 选中的进展因子表
- ``run_detail``  每个起始季度的最新值/终极值/准备金
- ``run_large``   大额赔案逐案估计（如有）

页面可列出历史 run、查看详情、对比两次 run 的参数与结果差异。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

from .reserving import ReserveEstimate

DEFAULT_DB = "data/archive/runs.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    method TEXT NOT NULL,
    basis TEXT NOT NULL,
    metric TEXT NOT NULL,
    group_dim TEXT,
    group_value TEXT,
    valuation_date TEXT,
    factor_method TEXT,
    params_json TEXT,
    latest_total REAL,
    total_ultimate REAL,
    total_reserve REAL,
    large_claim_reserve REAL,
    total_reserve_with_large REAL,
    note TEXT
);
CREATE TABLE IF NOT EXISTS run_factors (
    run_id INTEGER, dev INTEGER, next_dev TEXT, n_points INTEGER,
    volume_weighted REAL, simple REAL, median REAL, selected REAL,
    is_manual INTEGER
);
CREATE TABLE IF NOT EXISTS run_detail (
    run_id INTEGER, origin TEXT, latest_dev INTEGER, latest REAL,
    cdf REAL, prior_ultimate REAL, ultimate REAL, reserve REAL
);
CREATE TABLE IF NOT EXISTS run_large (
    run_id INTEGER, claim_id INTEGER, product_line TEXT,
    true_ultimate REAL, case_outstanding REAL, manual_reserve REAL,
    reserve REAL
);
"""


def connect(db_path: str | Path = DEFAULT_DB) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    return conn


def save_run(est: ReserveEstimate, *, valuation_date: str,
             basis: str, group_dim: str = "ALL", group_value: str = "ALL",
             note: str = "", db_path: str | Path = DEFAULT_DB,
             large_detail: pd.DataFrame | None = None) -> int:
    """把一次估计完整存档，返回 run_id。"""
    conn = connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO runs (created_at, method, basis, metric, group_dim,
               group_value, valuation_date, factor_method, params_json,
               latest_total, total_ultimate, total_reserve,
               large_claim_reserve, total_reserve_with_large, note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (datetime.now().isoformat(timespec="seconds"), est.method, basis,
             est.triangle.metric, group_dim, group_value, str(valuation_date),
             est.params.get("factor_method"),
             json.dumps(est.params, ensure_ascii=False, default=str),
             est.latest_total, est.total_ultimate, est.total_reserve,
             est.large_claim_reserve, est.total_reserve_with_large, note))
        run_id = int(cur.lastrowid)

        f = est.factors.copy()
        f["run_id"] = run_id
        f[["run_id", "dev", "next_dev", "n_points", "volume_weighted",
           "simple", "median", "selected", "is_manual"]].to_sql(
            "run_factors", conn, if_exists="append", index=False)

        d = est.detail.copy()
        d["run_id"] = run_id
        if "prior_ultimate" not in d.columns:
            d["prior_ultimate"] = None
        d[["run_id", "origin", "latest_dev", "latest", "cdf",
           "prior_ultimate", "ultimate", "reserve"]].to_sql(
            "run_detail", conn, if_exists="append", index=False)

        if large_detail is not None and len(large_detail):
            lg = large_detail.copy()
            lg["run_id"] = run_id
            lg[["run_id", "claim_id", "product_line", "true_ultimate",
                "case_outstanding", "manual_reserve", "reserve"]].to_sql(
                "run_large", conn, if_exists="append", index=False)
        conn.commit()
        return run_id
    finally:
        conn.close()


def list_runs(db_path: str | Path = DEFAULT_DB) -> pd.DataFrame:
    conn = connect(db_path)
    try:
        return pd.read_sql_query(
            "SELECT * FROM runs ORDER BY run_id DESC", conn)
    finally:
        conn.close()


def load_run(run_id: int, db_path: str | Path = DEFAULT_DB
             ) -> dict[str, pd.DataFrame]:
    conn = connect(db_path)
    try:
        out = {"run": pd.read_sql_query(
            "SELECT * FROM runs WHERE run_id=?", conn, params=[run_id])}
        for table in ("run_factors", "run_detail", "run_large"):
            out[table] = pd.read_sql_query(
                f"SELECT * FROM {table} WHERE run_id=? ORDER BY rowid",
                conn, params=[run_id])
        return out
    finally:
        conn.close()


def delete_run(run_id: int, db_path: str | Path = DEFAULT_DB) -> None:
    conn = connect(db_path)
    try:
        for table in ("runs", "run_factors", "run_detail", "run_large"):
            conn.execute(f"DELETE FROM {table} WHERE run_id=?", [run_id])
        conn.commit()
    finally:
        conn.close()
