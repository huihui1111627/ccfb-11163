"""页面 5：历史回溯检验。"""

import sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parent
while _ROOT != _ROOT.parent and not (_ROOT / 'reserving_platform').is_dir():
    _ROOT = _ROOT.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))
import pandas as pd
import streamlit as st

from app.state import init_state, require_data
from reserving_platform.backtest import run_backtest

st.set_page_config(page_title="回溯检验", layout="wide")
init_state()
claims, txns, sim = require_data()

st.title("🔍 回溯检验")
st.caption("在历史估值日重跑完整估计流程：对比当时估计与①观察期后的实际进展、"
           "②模拟器保留的真实最终赔款")

with st.sidebar:
    st.header("回溯参数")
    method_label = st.radio("估计方法", ["链梯法 CL", "BF 法"], key="bt_method")
    method = "CL" if method_label.startswith("链梯") else "BF"
    basis_label = st.radio("口径", ["出险时间", "报案时间"], key="bt_basis")
    basis = "accident" if basis_label == "出险时间" else "report"
    metric_label = st.radio("指标", ["已决 paid", "已发生 incurred"],
                            key="bt_metric")
    metric = "paid" if metric_label.startswith("已决") else "incurred"
    n_points = st.slider("估值点个数", 3, 8, 6)
    horizon = st.slider("观察期（季度）", 2, 6, 4)
    run_btn = st.button("运行回溯检验", type="primary")

if run_btn or "bt_result" in st.session_state:
    if run_btn:
        with st.spinner("正在各历史估值点重跑估计…"):
            res = run_backtest(claims, txns, method=method, basis=basis,
                               metric=metric, n_points=n_points,
                               horizon=horizon)
        st.session_state["bt_result"] = res
    res = st.session_state["bt_result"]
    runs = res.runs
    if runs.empty:
        st.warning("样本不足，无法构建历史三角形")
    else:
        mape_true = runs["bias_pct_vs_true"].abs().mean()
        mape_act = runs["bias_pct_vs_actual"].abs().mean()
        mean_bias = runs["bias_pct_vs_true"].mean()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("估值点数", len(runs))
        c2.metric("MAPE(对真实终极)", f"{mape_true:.2%}")
        c3.metric("MAPE(对观察期实际)", f"{mape_act:.2%}")
        c4.metric("平均偏差方向", f"{mean_bias:+.2%}",
                  help="负值=系统性低估，正值=系统性高估")

        st.subheader("各估值点结果")
        show = runs.copy()
        show["valuation_date"] = show["valuation_date"]
        st.dataframe(show[["valuation_date", "n_claims", "latest_at_valuation",
                           "est_ultimate", "actual_to_horizon", "true_ultimate",
                           "bias_pct_vs_true", "bias_pct_vs_actual"]]
                     .style.format({
                         "latest_at_valuation": "{:,.0f}",
                         "est_ultimate": "{:,.0f}",
                         "actual_to_horizon": "{:,.0f}",
                         "true_ultimate": "{:,.0f}",
                         "bias_pct_vs_true": "{:+.2%}",
                         "bias_pct_vs_actual": "{:+.2%}"}),
                     use_container_width=True, hide_index=True)

        st.subheader("偏差趋势")
        chart_df = runs[["valuation_date", "bias_pct_vs_true",
                         "bias_pct_vs_actual"]].set_index("valuation_date")
        st.line_chart(chart_df, height=320)

        st.subheader("估计终极 vs 真实终极")
        cmp_df = runs[["valuation_date", "est_ultimate",
                       "true_ultimate"]].set_index("valuation_date")
        st.bar_chart(cmp_df, height=340)

        with st.expander("估值点 × 起始季度明细"):
            st.dataframe(res.detail, use_container_width=True,
                         hide_index=True, height=380)
else:
    st.info("在左侧设置参数后点击「运行回溯检验」。")
