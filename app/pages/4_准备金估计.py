"""页面 4：准备金估计（链梯法 / BF 法）与存档。"""

import sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parent
while _ROOT != _ROOT.parent and not (_ROOT / 'reserving_platform').is_dir():
    _ROOT = _ROOT.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))
import pandas as pd
import streamlit as st

from app.state import fmt_money, init_state, require_data
from reserving_platform.archive import list_runs, save_run
from reserving_platform.reserving import (bornhuetter_ferguson, chain_ladder,
                                          large_claim_reserve,
                                          parametric_prior)
from reserving_platform.triangles import build_triangle

st.set_page_config(page_title="准备金估计", layout="wide")
init_state()
claims, txns, sim = require_data()
vq = sim.config.valuation_q
threshold = float(st.session_state["large_threshold"])

st.title("🧮 准备金估计")

# ----------------------------- 参数 -----------------------------
with st.sidebar:
    st.header("估计参数")
    basis_label = st.radio("三角形口径", ["出险时间", "报案时间"])
    basis = "accident" if basis_label == "出险时间" else "report"
    metric_label = st.radio("估计基础", ["已决 paid", "已发生 incurred"])
    metric = "paid" if metric_label == "已决 paid" else "incurred"
    group_dim_label = st.radio("切分维度", ["不切分(全体)", "产品线", "渠道"])
    group_dim = None if group_dim_label == "不切分(全体)" else (
        "product_line" if group_dim_label == "产品线" else "channel")
    exclude_large = st.checkbox("剔除大额赔案", value=True)
    st.divider()
    method_label = st.radio("估计方法", ["链梯法 CL", "BF 法"])
    factor_method_label = st.selectbox(
        "进展因子选取方式", ["金额加权 volume", "简单平均 simple",
                          "中位数 median", "手动 manual"])
    factor_method = {"金额加权 volume": "volume",
                     "简单平均 simple": "simple",
                     "中位数 median": "median",
                     "手动 manual": "manual"}[factor_method_label]
    tail_factor = st.number_input("尾部因子（留空=自动）", min_value=1.0,
                                  value=1.0, step=0.01, format="%.3f")
    tail = tail_factor if tail_factor > 1.0 else None
    elr = st.slider("BF 先验强度系数（ELR）", 0.5, 1.5, 1.0, 0.05)
    note = st.text_input("存档备注", "")

# ----------------------------- 分组 -----------------------------
if group_dim is None:
    groups = [("ALL", None)]
else:
    groups = [(v, v) for v in sorted(claims[group_dim].unique())]

all_est, all_lg_detail = [], []
factor_tables = {}
for name, gv in groups:
    tri = build_triangle(claims, txns, basis, metric, vq,
                         group_dim=group_dim, group_value=gv,
                         exclude_large=exclude_large,
                         large_threshold=threshold)
    if method_label.startswith("链梯"):
        est = chain_ladder(tri, factor_method, tail_factor=tail)
    else:
        prior = parametric_prior(claims, vq, basis=basis,
                                 group_dim=group_dim, group_value=gv,
                                 expected_loss_ratio=elr)
        est = bornhuetter_ferguson(tri, prior, factor_method,
                                   tail_factor=tail,
                                   prior_strength=elr)
    est.group_name = name
    all_est.append(est)
    factor_tables[name] = est.factors

# ----------------------------- 手动因子编辑 -----------------------------
st.subheader("进展因子")
if factor_method == "manual":
    st.info("在下表 selected 列直接修改因子（需为 ≥1 的数值），修改后即时重算。")
    base_name = groups[0][0]
    fdf = factor_tables[base_name].copy()
    edited = st.data_editor(fdf[["dev", "next_dev", "n_points",
                                 "volume_weighted", "simple", "median",
                                 "selected"]], num_rows="fixed",
                            use_container_width=True, hide_index=True,
                            disabled=["dev", "next_dev", "n_points",
                                      "volume_weighted", "simple", "median"])
    manual_factors = {int(r["dev"]): float(r["selected"])
                      for _, r in edited.iterrows()
                      if r["next_dev"] != "tail"}
    manual_tail = float(edited.iloc[-1]["selected"])
    # 用手动因子重算
    all_est = []
    for name, gv in groups:
        tri = build_triangle(claims, txns, basis, metric, vq,
                             group_dim=group_dim, group_value=gv,
                             exclude_large=exclude_large,
                             large_threshold=threshold)
        if method_label.startswith("链梯"):
            est = chain_ladder(tri, "manual", manual_factors, manual_tail)
        else:
            prior = parametric_prior(claims, vq, basis=basis,
                                     group_dim=group_dim, group_value=gv,
                                     expected_loss_ratio=elr)
            est = bornhuetter_ferguson(tri, prior, "manual",
                                       manual_factors, manual_tail,
                                       prior_strength=elr)
        est.group_name = name
        all_est.append(est)
else:
    for name, est in factor_tables.items():
        with st.expander(f"{name} 的因子表（{len(groups)} 个分组）"
                         if group_dim else "因子表"):
            st.dataframe(est[["dev", "next_dev", "n_points",
                              "volume_weighted", "simple", "median",
                              "selected"]].style.format(
                {"volume_weighted": "{:.4f}", "simple": "{:.4f}",
                 "median": "{:.4f}", "selected": "{:.4f}"}),
                use_container_width=True, hide_index=True)
        if not group_dim:
            break

# ----------------------------- 估计结果 -----------------------------
st.divider()
st.subheader("估计结果")
summary_rows = []
for est in all_est:
    summary_rows.append({
        "分组": est.group_name,
        "最新对角线": est.latest_total,
        "估计终极赔款": est.total_ultimate,
        "常规准备金": est.total_reserve,
    })
summary = pd.DataFrame(summary_rows)

# 大额赔案逐案估计
lg_reserve_total = 0.0
lg_detail_all = []
if exclude_large:
    for name, gv in groups:
        c_sub = claims if gv is None else claims[claims[group_dim] == gv]
        t_sub = txns if gv is None else txns[txns[group_dim] == gv]
        lg_r, lg_d = large_claim_reserve(c_sub, t_sub,
                                         sim.config.valuation_date, threshold)
        if len(lg_d):
            lg_d["分组"] = name
            lg_detail_all.append(lg_d)
        lg_reserve_total += lg_r
    summary["大额逐案准备金"] = [
        float(large_claim_reserve(
            claims if gv is None else claims[claims[group_dim] == gv],
            txns if gv is None else txns[txns[group_dim] == gv],
            sim.config.valuation_date, threshold)[0])
        for _, gv in groups]
    summary["合计准备金"] = summary["常规准备金"] + summary["大额逐案准备金"]

st.dataframe(summary.style.format({c: "{:,.0f}" for c in summary.columns
                                   if c != "分组"}),
             use_container_width=True, hide_index=True)

m1, m2, m3 = st.columns(3)
m1.metric("估计终极赔款合计(元)",
          fmt_money(summary["估计终极赔款"].sum()))
m2.metric("常规准备金合计(元)",
          fmt_money(summary["常规准备金"].sum()))
if exclude_large:
    m3.metric("含大额总准备金(元)",
              fmt_money(summary["合计准备金"].sum()))

# 逐季明细
with st.expander("各起始季度估计明细"):
    for est in all_est:
        st.markdown(f"**{est.group_name}**")
        st.dataframe(est.detail.style.format({
            c: "{:,.2f}" for c in est.detail.columns
            if c not in ("origin",)}),
            use_container_width=True, hide_index=True)

# 大额清单
if lg_detail_all:
    with st.expander(f"大额赔案逐案清单（{sum(len(d) for d in lg_detail_all)} 件）"):
        lg_all = pd.concat(lg_detail_all, ignore_index=True)
        st.dataframe(lg_all, use_container_width=True, hide_index=True,
                     height=320)

# 与真实终极对比（模拟器标准答案）
st.divider()
st.subheader("与标准答案对比（模拟器真实最终赔款）")
true_ult = claims["true_ultimate"].sum()
est_total = summary["估计终极赔款"].sum()
if exclude_large:
    lg_true = pd.concat(lg_detail_all)["true_ultimate"].sum() \
        if lg_detail_all else 0.0
    est_total += lg_true
bias = est_total - true_ult
b1, b2, b3 = st.columns(3)
b1.metric("估计终极(含大额)", fmt_money(est_total))
b2.metric("真实终极", fmt_money(true_ult))
b3.metric("偏差率", f"{bias / true_ult:+.2%}")
st.caption("实际工作中没有真实终极，需以多年后结案数据替代；本平台由模拟器"
           "保留潜变量，直接度量估计方法的偏差。")

# ----------------------------- 存档 -----------------------------
st.divider()
st.subheader("估算存档")
if st.button("将本次估算存入档案库", type="primary"):
    db = st.session_state["db_path"]
    ids = []
    lg_concat = pd.concat(lg_detail_all) if lg_detail_all else None
    for est in all_est:
        rid = save_run(est, valuation_date=str(sim.config.valuation_date),
                       basis=basis, group_dim=group_dim or "ALL",
                       group_value=est.group_name, note=note,
                       db_path=db, large_detail=(
                           lg_concat[lg_concat["分组"] == est.group_name]
                           if lg_concat is not None else None))
        ids.append(rid)
    st.success(f"已存档 run_id={ids}")

runs = list_runs(st.session_state["db_path"])
if len(runs):
    st.markdown("**历史估算记录**")
    st.dataframe(runs[["run_id", "created_at", "method", "basis", "metric",
                       "group_value", "factor_method",
                       "total_reserve_with_large", "note"]],
                 use_container_width=True, hide_index=True, height=260)
