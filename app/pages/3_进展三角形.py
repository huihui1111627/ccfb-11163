"""页面 3：进展三角形构建与展示。"""

import sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parent
while _ROOT != _ROOT.parent and not (_ROOT / 'reserving_platform').is_dir():
    _ROOT = _ROOT.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))
import numpy as np
import pandas as pd
import streamlit as st

from app.state import fmt_money, init_state, require_data
from reserving_platform.period_utils import quarter_label
from reserving_platform.triangles import (build_segment_triangles,
                                          build_triangle, triangle_long_table)

st.set_page_config(page_title="进展三角形", layout="wide")
init_state()
claims, txns, sim = require_data()
vq = sim.config.valuation_q

st.title("🔺 进展三角形")

# ----------------------------- 控制面板 -----------------------------
with st.sidebar:
    st.header("三角形参数")
    basis_label = st.radio("口径", ["出险时间", "报案时间", "支付时间"],
                           index=0)
    basis = {"出险时间": "accident", "报案时间": "report",
             "支付时间": "payment"}[basis_label]
    metric_label = st.radio("指标", ["累计已决 paid", "累计已发生 incurred",
                                    "件数 count"], index=0)
    metric = {"累计已决 paid": "paid", "累计已发生 incurred": "incurred",
              "件数 count": "count"}[metric_label]
    group_dim_label = st.radio("切分维度", ["不切分(全体)", "产品线", "渠道"])
    group_dim = {"不切分(全体)": None, "产品线": "product_line",
                 "渠道": "channel"}[group_dim_label]
    min_claims = st.number_input("小样本合并阈值（件）", 50, 5000, 200, step=50)
    st.divider()
    exclude_large = st.checkbox("剔除大额赔案（单独处理）", value=True)
    threshold = st.number_input("大额阈值（元）", 10_000, 2_000_000,
                                int(st.session_state["large_threshold"]),
                                step=10_000)
    st.session_state["large_threshold"] = float(threshold)
    show_incremental = st.checkbox("同时显示增量三角形", value=False)

# ----------------------------- 构建 -----------------------------
@st.cache_data(show_spinner=False)
def _build(claims_hash, txns_hash, basis, metric, group_dim, min_claims,
           exclude_large, threshold, vq):
    if group_dim is None:
        tri = build_triangle(claims, txns, basis, metric, vq,
                             exclude_large=exclude_large,
                             large_threshold=threshold)
        return {"ALL": tri}
    return build_segment_triangles(claims, txns, group_dim, basis, metric,
                                   vq, min_claims=min_claims,
                                   exclude_large=exclude_large,
                                   large_threshold=threshold)


tris = _build(pd.util.hash_pandas_object(claims).sum(),
              pd.util.hash_pandas_object(txns).sum(),
              basis, metric, group_dim, int(min_claims),
              exclude_large, float(threshold), vq)

st.caption(f"口径：{basis_label} ｜ 指标：{metric_label} ｜ 估值季度："
           f"{quarter_label(vq)} ｜ 大额剔除：{'是' if exclude_large else '否'}"
           f"{'（阈值 ' + fmt_money(threshold) + ' 元）' if exclude_large else ''}")

names = list(tris.keys())
tabs = st.tabs([f"{n}（{len(claims if group_dim is None else claims[claims[group_dim] == n]) if n in claims[group_dim].unique() else '合并组'}）"
                for n in names]) if group_dim else st.tabs(names)

for tab, name in zip(tabs, names):
    tri = tris[name]
    with tab:
        c1, c2, c3 = st.columns(3)
        diag = tri.latest_diagonal()
        c1.metric("对角线合计", fmt_money(diag.sum()) if metric != "count"
                  else f"{int(diag.sum()):,}")
        c2.metric("起始季度数", len(tri.data))
        c3.metric("最大可见进展期(季度)", int(tri.data.apply(
            lambda r: r.dropna().index.max(), axis=1).max()))
        st.markdown("**累计三角形**")
        st.dataframe(tri.data.style.format(
            lambda x: "" if pd.isna(x) else
            (f"{x:,.0f}" if metric != "count" else f"{int(x):,}")),
            use_container_width=True)
        if show_incremental:
            st.markdown("**增量三角形**")
            st.dataframe(tri.to_incremental().data.style.format(
                lambda x: "" if pd.isna(x) else
                (f"{x:,.0f}" if metric != "count" else f"{int(x):,}")),
                use_container_width=True)

st.divider()
st.subheader("各起始季度进展趋势")
long = pd.concat([triangle_long_table(t).assign(group=n)
                  for n, t in tris.items()], ignore_index=True)
pivot_trend = long.pivot_table(index="dev", columns="origin",
                               values="value", aggfunc="sum")
st.line_chart(pivot_trend, height=380)
st.caption("横轴为进展期（季度），每条线为一个起始季度；线越短代表起始季度越晚。")

# ----------------------------- 口径说明 -----------------------------
with st.expander("三种口径与未决估损的处理口径说明"):
    st.markdown(
        "1. **出险口径**：行为事故季度，进展期 = 事件季度 − 事故季度，"
        "是准备金评估的主口径。\n"
        "2. **报案口径**：行为报案季度，估值日后报案的赔案（IBNR）整体不可见，"
        "用于报案延迟分析。\n"
        "3. **支付口径**：行为支付季度，全部落在进展期 0，是现金流/支付模式视角。\n"
        "4. **未决估损**：CASE 流水为快照语义，同一进展期内多次调整只取最后一次；"
        "已发生 = 累计已决 + 期末未决。\n"
        "5. **追偿 RECOV** 为负支付，自动冲减已决；**重开 REOPEN** 后新增支付"
        "按支付日落入对应进展期；**拒赔 DENY** 不计金额但计入件数。")
