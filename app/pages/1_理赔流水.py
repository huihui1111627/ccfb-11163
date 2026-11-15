"""页面 1：理赔流水浏览（赔案主表 + 交易台账）。"""

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

st.set_page_config(page_title="理赔流水", layout="wide")
init_state()
claims, txns, sim = require_data()

st.title("📄 理赔流水")
st.caption("赔案主表（一案一行）与交易流水台账（一案多行），"
           "事件类型 PAY 支付 / RECOV 追偿 / CASE 未决估损 / CLOSE 结案 / "
           "REOPEN 重开 / DENY 拒赔")

tab1, tab2, tab3 = st.tabs(["赔案主表", "交易流水台账", "流水结构说明"])

with tab1:
    c1, c2, c3 = st.columns(3)
    c1.metric("赔案总数", f"{len(claims):,}")
    c2.metric("已决率",
              f"{(claims['status'] == 'CLOSED').mean():.1%}")
    c3.metric("真实最终赔款合计(元)",
              f"{claims['true_ultimate'].sum():,.0f}")
    st.divider()
    fcol1, fcol2, fcol3 = st.columns(3)
    pl = fcol1.selectbox("产品线", ["全部"] + sorted(claims["product_line"].unique()))
    ch = fcol2.selectbox("渠道", ["全部"] + sorted(claims["channel"].unique()))
    status = fcol3.selectbox("状态", ["全部"] + sorted(claims["status"].unique()))
    view = claims.copy()
    if pl != "全部":
        view = view[view["product_line"] == pl]
    if ch != "全部":
        view = view[view["channel"] == ch]
    if status != "全部":
        view = view[view["status"] == status]
    st.dataframe(view, use_container_width=True, height=460, hide_index=True)
    st.download_button("下载赔案主表 CSV", view.to_csv(index=False).encode(),
                       "claims.csv", "text/csv")

with tab2:
    st.write(f"共 {len(txns):,} 条流水")
    tcol1, tcol2, tcol3 = st.columns(3)
    ttype = tcol1.selectbox("事件类型", ["全部"] + sorted(txns["txn_type"].unique()))
    cid = tcol2.text_input("按赔案号查询 claim_id", "")
    tview = txns
    if ttype != "全部":
        tview = tview[tview["txn_type"] == ttype]
    if cid.strip():
        tview = tview[tview["claim_id"] == int(cid.strip())]
    st.dataframe(tview, use_container_width=True, height=460, hide_index=True)
    st.download_button("下载流水台账 CSV", tview.to_csv(index=False).encode(),
                       "transactions.csv", "text/csv")

with tab3:
    st.markdown(
        "| 事件类型 | 含义 | 金额字段 | 三角形处理 |\n"
        "|---|---|---|---|\n"
        "| PAY | 赔款支付（正数） | amount | 计入累计已决 |\n"
        "| RECOV | 追偿回收（负数） | amount | 冲减累计已决 |\n"
        "| CASE | 未决估损设定/调整（快照值） | case_estimate | "
        "按调整日期落入对应进展期，期末取值 |\n"
        "| CLOSE | 结案，估损清零 | case_estimate=0 | 未决快照归零 |\n"
        "| REOPEN | 结案后重开 | — | 恢复未决，后续支付正常计入 |\n"
        "| DENY | 拒赔 | — | 不产生赔款，计入件数 |")
    st.info("模拟器还在原始数据中注入了 5 类脏数据（重复流水、维度缺失、"
            "报案早于出险、支付符号录反、孤儿流水），请在「数据清洗」页查看处理。")
