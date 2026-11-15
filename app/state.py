"""Streamlit 页面共享状态：数据生成、清洗结果与配置在 session_state 中缓存。"""
from __future__ import annotations

import streamlit as st

from reserving_platform.cleaning import add_quarter_columns, clean, quality_checks
from reserving_platform.config import SimConfig
from reserving_platform.simulator import SimulationResult, simulate

DATA_DIR = "data/generated"
ARCHIVE_DB = "data/archive/runs.db"


def init_state() -> None:
    defaults = {
        "sim": None,
        "raw_claims": None,
        "raw_txns": None,
        "clean_claims": None,
        "clean_txns": None,
        "quarantined": None,
        "qc_report": None,
        "clean_report": None,
        "sim_params": {"scale": 0.3, "seed": 20261116},
        "large_threshold": 100_000.0,
        "db_path": ARCHIVE_DB,
    }
    for k, v in defaults.items():
        st.session_state.setdefault(k, v)


def generate_data(scale: float, seed: int) -> SimulationResult:
    """运行模拟器并自动执行清洗，结果写入 session_state。"""
    cfg = SimConfig(scale=scale, seed=seed)
    with st.spinner("正在模拟五年理赔流水并执行清洗…"):
        sim = simulate(cfg)
        claims, txns, quarantined, report = clean(sim.claims, sim.transactions)
        claims, txns = add_quarter_columns(claims, txns)
    st.session_state["sim"] = sim
    st.session_state["raw_claims"] = sim.claims
    st.session_state["raw_txns"] = sim.transactions
    st.session_state["clean_claims"] = claims
    st.session_state["clean_txns"] = txns
    st.session_state["quarantined"] = quarantined
    st.session_state["qc_report"] = quality_checks(sim.claims, sim.transactions)
    st.session_state["clean_report"] = report
    st.session_state["sim_params"] = {"scale": scale, "seed": seed}
    return sim


def require_data():
    """页面入口守卫：未生成数据时提示并停止。"""
    if st.session_state.get("clean_claims") is None:
        st.warning("请先到「理赔流水」页面生成模拟数据。")
        st.stop()
    return (st.session_state["clean_claims"],
            st.session_state["clean_txns"],
            st.session_state["sim"])


def fmt_money(x: float) -> str:
    return f"{x:,.0f}"
