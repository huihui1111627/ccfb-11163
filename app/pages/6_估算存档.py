"""页面 6：估算存档库。"""

import sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parent
while _ROOT != _ROOT.parent and not (_ROOT / 'reserving_platform').is_dir():
    _ROOT = _ROOT.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))
import json
import pandas as pd
import streamlit as st

from app.state import init_state
from reserving_platform.archive import delete_run, list_runs, load_run

st.set_page_config(page_title="估算存档", layout="wide")
init_state()

st.title("🗄️ 估算存档库")
st.caption("每次估算的参数选择、进展因子、逐季结果与大额清单均持久化在 SQLite")

db = st.session_state["db_path"]
runs = list_runs(db)
if runs.empty:
    st.info("暂无存档记录，请到「准备金估计」页面执行估算并存档。")
    st.stop()

st.dataframe(runs[["run_id", "created_at", "method", "basis", "metric",
                   "group_dim", "group_value", "valuation_date",
                   "factor_method", "latest_total", "total_ultimate",
                   "total_reserve", "large_claim_reserve",
                   "total_reserve_with_large", "note"]]
             .style.format({c: "{:,.2f}" for c in
                            ["latest_total", "total_ultimate",
                             "total_reserve", "large_claim_reserve",
                             "total_reserve_with_large"]}),
             use_container_width=True, hide_index=True, height=300)

col1, col2 = st.columns([1, 3])
run_id = col1.selectbox("选择 run_id 查看详情", runs["run_id"].tolist())
data = load_run(int(run_id), db)

st.subheader(f"Run {run_id} 详情")
meta = data["run"].iloc[0]
with st.expander("参数 JSON", expanded=True):
    st.json(json.loads(meta["params_json"]))

tab1, tab2, tab3 = st.tabs(["进展因子", "逐季估计明细", "大额赔案清单"])
with tab1:
    st.dataframe(data["run_factors"], use_container_width=True,
                 hide_index=True)
with tab2:
    st.dataframe(data["run_detail"].style.format({
        c: "{:,.2f}" for c in ("latest", "cdf", "prior_ultimate",
                               "ultimate", "reserve")}),
        use_container_width=True, hide_index=True)
with tab3:
    if len(data["run_large"]):
        st.dataframe(data["run_large"], use_container_width=True,
                     hide_index=True)
    else:
        st.caption("本次估算未包含大额赔案")

st.divider()
c1, c2 = st.columns([1, 3])
if c1.button("删除该存档", type="secondary"):
    delete_run(int(run_id), db)
    st.success(f"已删除 run_id={run_id}")
    st.rerun()
c2.download_button("下载该 run 明细 CSV",
                   data["run_detail"].to_csv(index=False).encode(),
                   f"run_{run_id}_detail.csv", "text/csv")
