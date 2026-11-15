"""首页：平台说明与数据生成入口。

运行：streamlit run app/Home.py
"""

import sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parent
while _ROOT != _ROOT.parent and not (_ROOT / 'reserving_platform').is_dir():
    _ROOT = _ROOT.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))
import streamlit as st

from app.state import fmt_money, generate_data, init_state
from reserving_platform.config import (CHANNELS, DEFAULT_VALUATION_DATE,
                                       PRODUCT_LINES, SimConfig)

st.set_page_config(page_title="健康险准备金数据平台", layout="wide",
                   page_icon="📊")
init_state()

st.title("📊 健康险准备金数据平台")
st.caption("从理赔流水 → 数据清洗 → 进展三角形 → 准备金估计 → 回溯检验 → 存档")

col1, col2 = st.columns([1.1, 1])
with col1:
    st.subheader("① 生成模拟理赔流水")
    st.write("模拟器按已知真实参数（出险频率、报案延迟、赔付模式、估损偏差、"
             "重开/拒赔/追偿概率）生成五年流水，并保留每个赔案的"
             "**真实最终赔款**作为检验估计偏差的标准答案。")
    with st.form("gen_form"):
        scale = st.slider("频率缩放系数（1.0=全量约 9 万件，演示建议 0.3）",
                          0.05, 1.0,
                          st.session_state["sim_params"]["scale"], 0.05)
        seed = st.number_input("随机种子", min_value=1, max_value=10**8,
                               value=int(st.session_state["sim_params"]["seed"]))
        submitted = st.form_submit_button("生成 / 重新生成数据",
                                          type="primary")
    if submitted:
        sim = generate_data(float(scale), int(seed))
        st.success(f"生成完成：{len(sim.claims):,} 件赔案，"
                   f"{len(sim.transactions):,} 条流水")

with col2:
    st.subheader("平台覆盖的业务规则")
    st.markdown(
        "- **三种口径**：出险时间 / 报案时间 / 支付时间\n"
        "- **已决与未决分开**：未决估损按调整日期落入对应进展期\n"
        "- **大额赔案**：按阈值剔除，逐案单独估计后加回\n"
        "- **两种方法**：链梯法 CL 与 BF 法，进展因子可手动选取\n"
        "- **维度切分**：产品线 / 渠道，小样本自动合并\n"
        "- **特殊流水**：赔案重开、拒赔、追偿正确计入\n"
        "- **回溯检验**：历史估值点重跑，对比实际与真实终极\n"
        "- **完整存档**：每次估算的参数、因子、结果落 SQLite")

st.divider()
st.subheader("模拟参数（真实值，精算页面不可见）")
c1, c2, c3 = st.columns(3)
c1.metric("产品线", " / ".join(PRODUCT_LINES))
c2.metric("渠道", " / ".join(CHANNELS))
c3.metric("默认估值日", str(DEFAULT_VALUATION_DATE))
cfg = SimConfig()
st.caption(f"数据区间 {cfg.start_date} ~ {cfg.end_date}，"
           f"共 {cfg.end_q - cfg.start_q + 1} 个事故季度；"
           "6 个 产品线×渠道 分段各自独立的频率/延迟/赔付/估损参数。")

if st.session_state["raw_claims"] is not None:
    st.divider()
    st.subheader("当前数据集概览")
    sim = st.session_state["sim"]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("赔案件数", fmt_money(len(sim.claims)))
    m2.metric("流水条数", fmt_money(len(sim.transactions)))
    m3.metric("真实最终赔款合计(元)",
              fmt_money(sim.claims["true_ultimate"].sum()))
    m4.metric("注入脏数据(条)",
              fmt_money(sum(sim.issue_manifest.values())))
    st.page_link("pages/1_理赔流水.py", label="下一步：查看理赔流水 →",
                 icon="📄")
