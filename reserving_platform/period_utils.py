"""季度期间工具。

平台统一以**季度**为进展期单位（健康险理赔尾部较短，季度颗粒度兼顾稳定性与
及时性）。每个季度用一个整数索引 ``q_index`` 表示：

    q_index = year * 4 + (quarter - 1)

例如 2021Q1 -> 8084，2025Q4 -> 8103。日期 -> 所在季度索引，季度索引 ->
季度标签，进展期 = 估值季度 - 起始季度。
"""
from __future__ import annotations

from datetime import date

import pandas as pd

QUARTERS_PER_YEAR = 4


def quarter_index(d: date | pd.Timestamp) -> int:
    """返回日期所在季度的整数索引。"""
    ts = pd.Timestamp(d)
    return ts.year * QUARTERS_PER_YEAR + (ts.quarter - 1)


def quarter_label(q: int) -> str:
    """季度索引 -> ``2021Q1`` 形式的标签。"""
    year, qq = divmod(q, QUARTERS_PER_YEAR)
    return f"{year}Q{qq + 1}"


def quarter_start(q: int) -> pd.Timestamp:
    """季度索引 -> 该季度第一天。"""
    year, qq = divmod(q, QUARTERS_PER_YEAR)
    return pd.Timestamp(year=year, month=qq * 3 + 1, day=1)


def quarter_end(q: int) -> pd.Timestamp:
    """季度索引 -> 该季度最后一天（23:59:59）。"""
    return quarter_start(q + 1) - pd.Timedelta(seconds=1)


def dev_period(origin_q: int, event_q: int) -> int:
    """事件相对起始季度的进展期（0 表示同一季度）。"""
    return event_q - origin_q


def date_to_quarter_series(s: pd.Series) -> pd.Series:
    """日期序列批量转季度索引（向量化实现）。"""
    ts = pd.to_datetime(s)
    return ts.dt.year * QUARTERS_PER_YEAR + (ts.dt.quarter - 1)


def valuation_quarter(valuation_date: date | pd.Timestamp) -> int:
    """估值日所在季度（取估值日之前的完整季度，即估值日本身所在季度）。"""
    return quarter_index(valuation_date)


def list_origin_quarters(start_q: int, end_q: int) -> list[int]:
    """返回 [start_q, end_q] 闭区间内的全部季度索引。"""
    return list(range(start_q, end_q + 1))
