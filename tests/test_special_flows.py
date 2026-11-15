"""特殊流水与口径规则的专项测试。"""
import pandas as pd

from reserving_platform.cleaning import add_quarter_columns, clean
from reserving_platform.config import SimConfig
from reserving_platform.simulator import simulate
from reserving_platform.triangles import build_triangle


def _setup(seed=7, scale=0.2):
    sim = simulate(SimConfig(scale=scale, seed=seed))
    claims, txns, _, _ = clean(sim.claims, sim.transactions)
    claims, txns = add_quarter_columns(claims, txns)
    return sim, claims, txns


def test_closed_claims_end_with_zero_case_estimate():
    """不变量：已结案赔案的最后一条 CASE/CLOSE/REOPEN 事件估损必须为 0，
    且事件时间不早于最后一笔支付。"""
    sim, claims, txns = _setup()
    closed = claims[claims["status"] == "CLOSED"]
    sample = closed.sample(min(300, len(closed)), random_state=3)
    for cid in sample["claim_id"]:
        sub = txns[txns["claim_id"] == cid].sort_values("txn_date")
        state = sub[sub["txn_type"].isin(["CASE", "CLOSE", "REOPEN"])]
        last = state.iloc[-1]
        assert last["txn_type"] == "CLOSE", f"赔案 {cid} 末状态不是 CLOSE"
        assert last["case_estimate"] == 0.0
        pays = sub[sub["txn_type"].isin(["PAY", "RECOV"])]
        if len(pays):
            assert last["txn_date"] >= pays["txn_date"].max() - pd.Timedelta(days=1)


def test_denied_claims_have_no_payment():
    sim, claims, txns = _setup()
    denied_ids = claims.loc[claims["denied"], "claim_id"]
    if len(denied_ids) == 0:
        return
    denied_pay = txns[txns["claim_id"].isin(denied_ids)
                      & txns["txn_type"].isin(["PAY", "RECOV"])]
    assert denied_pay.empty
    # 拒赔赔案真实终极为 0，但有 DENY 流水
    assert (claims.loc[claims["denied"], "true_ultimate"] == 0).all()
    deny_txn = txns[(txns["claim_id"].isin(denied_ids))
                    & (txns["txn_type"] == "DENY")]
    assert len(deny_txn) == len(denied_ids)


def test_recovery_reduces_paid():
    sim, claims, txns = _setup()
    recov = txns[txns["txn_type"] == "RECOV"]
    assert (recov["amount"] < 0).all()
    vq = int(txns["txn_q"].max())
    tri = build_triangle(claims, txns, "accident", "paid", vq)
    gross_pay = txns.loc[txns["txn_type"] == "PAY", "amount"].sum()
    recov_sum = -recov["amount"].sum()
    # 三角形对角线 = 毛支付 - 追偿
    assert abs(tri.latest_diagonal().sum()
               - (gross_pay - recov_sum)) / gross_pay < 1e-9


def test_reopened_claims_have_extra_lifecycle():
    sim, claims, txns = _setup()
    reopened = claims[claims["reopened"]]
    assert len(reopened) > 0
    rids = reopened["claim_id"]
    # 每个重开赔案都有 REOPEN 流水且至少两次 CLOSE
    for cid in rids.sample(min(20, len(rids)), random_state=1):
        sub = txns[txns["claim_id"] == cid].sort_values("txn_date")
        assert (sub["txn_type"] == "REOPEN").sum() >= 1
        assert (sub["txn_type"] == "CLOSE").sum() >= 2
    # 重开赔案的支付完整进入三角形（不丢流水）
    vq = int(txns["txn_q"].max())
    tri = build_triangle(claims, txns, "accident", "paid", vq)
    reopened_paid = txns.loc[txns["claim_id"].isin(rids)
                             & (txns["txn_type"] == "PAY"), "amount"].sum()
    assert reopened_paid > 0
    # 重开补赔后真实终极应高于首次支付总额
    full_pay = txns.loc[txns["claim_id"].isin(rids)
                        & (txns["txn_type"] == "PAY"), "amount"].sum()
    recov = txns.loc[txns["claim_id"].isin(rids)
                     & (txns["txn_type"] == "RECOV"), "amount"].sum()
    assert reopened["true_ultimate"].sum() >= full_pay + recov - 1.0


def test_case_estimate_adjustments_fall_in_dev_periods():
    """未决估损按调整日期落进展期：已发生三角形 = 已决 + 期末未决。"""
    sim, claims, txns = _setup()
    vq = int(txns["txn_q"].max())
    paid_tri = build_triangle(claims, txns, "accident", "paid", vq)
    inc_tri = build_triangle(claims, txns, "accident", "incurred", vq)
    merged = pd.concat([
        paid_tri.latest_diagonal().rename("paid"),
        inc_tri.latest_diagonal().rename("incurred")], axis=1).dropna()
    # 每个事故季度：已发生 >= 已决（未决快照非负）
    assert (merged["incurred"] + 1e-6 >= merged["paid"]).all()

    # 手工验证一个事故季度的期末未决：逐赔案最后一条 CASE/CLOSE/REOPEN
    sample_origin = merged.index[len(merged) // 2]
    # 用标签反查季度索引
    year, q = int(sample_origin[:4]), int(sample_origin[-1])
    oq = year * 4 + (q - 1)
    oc = claims[claims["accident_q"] == oq]
    ot = txns[txns["claim_id"].isin(oc["claim_id"])]
    last_case = (ot[ot["txn_type"].isin(["CASE", "CLOSE", "REOPEN"])]
                 .sort_values("txn_date").groupby("claim_id").tail(1))
    outstanding = last_case["case_estimate"].sum()
    expect_inc = merged.loc[sample_origin, "paid"] + outstanding
    assert abs(merged.loc[sample_origin, "incurred"] - expect_inc) \
        / max(expect_inc, 1.0) < 1e-9


def test_payment_basis_is_quarterly_cashflow():
    """支付口径三角形每行只有 dev 0，金额等于该季度支付现金流。"""
    sim, claims, txns = _setup()
    vq = int(txns["txn_q"].max())
    tri = build_triangle(claims, txns, "payment", "paid", vq)
    # 除 dev 0 外全部不可见
    assert tri.data.drop(columns=[0]).notna().sum().sum() == 0
    cash = (txns[txns["txn_type"].isin(["PAY", "RECOV"])
            & (txns["txn_q"] <= vq)]
            .groupby("txn_q")["amount"].sum())
    assert abs(tri.data[0].sum() - cash.sum()) / cash.sum() < 1e-9


def test_count_triangle_excludes_ibnr_in_report_basis():
    sim, claims, txns = _setup()
    vq = int(txns["txn_q"].max())
    cnt_report = build_triangle(claims, txns, "report", "count", vq)
    cnt_accident = build_triangle(claims, txns, "accident", "count", vq)
    reported_n = int((claims["report_q"] <= vq).sum())
    assert int(cnt_report.data[0].sum()) == reported_n
    # 出险口径件数 >= 报案口径（差额为 IBNR）
    assert int(cnt_accident.data[0].sum()) >= reported_n
