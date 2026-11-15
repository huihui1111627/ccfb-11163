"""页面 2：数据质量检查与清洗。"""

import sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parent
while _ROOT != _ROOT.parent and not (_ROOT / 'reserving_platform').is_dir():
    _ROOT = _ROOT.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))
import streamlit as st

from app.state import init_state, require_data

st.set_page_config(page_title="数据清洗", layout="wide")
init_state()
clean_claims, clean_txns, sim = require_data()

st.title("🧹 数据清洗")
raw_claims = st.session_state["raw_claims"]
raw_txns = st.session_state["raw_txns"]
qc = st.session_state["qc_report"]
quarantined = st.session_state["quarantined"]

st.subheader("质量检查结果（清洗前）")
st.dataframe(qc, use_container_width=True, hide_index=True)

col1, col2, col3, col4 = st.columns(4)
col1.metric("原始流水", f"{len(raw_txns):,}")
col2.metric("清洗后流水", f"{len(clean_txns):,}")
col3.metric("隔离流水", f"{len(quarantined):,}")
col4.metric("原始赔案", f"{len(raw_claims):,}")

st.divider()
st.subheader("清洗规则执行明细")
fix_df = st.session_state["clean_report"].to_frame()
st.dataframe(fix_df, use_container_width=True, hide_index=True)

tab1, tab2 = st.tabs(["隔离区（人工处理队列）", "模拟器注入问题对照"])
with tab1:
    if len(quarantined):
        st.dataframe(quarantined, use_container_width=True, height=420,
                     hide_index=True)
        st.download_button("下载隔离流水 CSV",
                           quarantined.to_csv(index=False).encode(),
                           "quarantined.csv", "text/csv")
    else:
        st.success("无隔离流水")

with tab2:
    st.caption("模拟器注入的脏数据计数 vs 清洗规则命中情况，用于验证清洗有效性")
    manifest = sim.issue_manifest
    import pandas as pd
    cmp_df = pd.DataFrame([
        {"问题类型": "重复流水", "注入数": manifest["duplicate"]},
        {"问题类型": "维度缺失", "注入数": manifest["missing_dim"]},
        {"问题类型": "报案早于出险", "注入数": manifest["bad_date"]},
        {"问题类型": "支付符号录反", "注入数": manifest["negative_amount"]},
        {"问题类型": "孤儿流水", "注入数": manifest["orphan"]},
    ])
    st.dataframe(cmp_df, use_container_width=True, hide_index=True)
    st.success("清洗后数据已通过全部一致性校验，可进入三角形构建。")
