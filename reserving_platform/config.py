"""平台配置与模拟器真实参数。

模拟器的所有"上帝视角"参数集中在本模块：出险频率、报案延迟、赔付模式、
估损偏差、重开/拒赔/追偿概率、大额阈值等。模拟器用这些参数直接生成每个赔案
的**真实最终赔款**（``true_ultimate``），作为检验链梯/BF 估计偏差的标准答案。

精算人员在页面上看到的只是截断到估值日的流水，看不到这些潜变量。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .period_utils import quarter_index

# ---------------------------------------------------------------------------
# 业务维度
# ---------------------------------------------------------------------------
PRODUCT_LINES = ["住院医疗", "门诊医疗", "重疾险"]
CHANNELS = ["代理人", "互联网"]
SEGMENTS = [f"{p}|{c}" for p in PRODUCT_LINES for c in CHANNELS]

# 默认估值日：2025-12-31，模拟数据覆盖 2021Q1~2025Q4 共 20 个出险季度
DEFAULT_VALUATION_DATE = date(2025, 12, 31)
SIM_START_DATE = date(2021, 1, 1)
SIM_END_DATE = date(2025, 12, 31)
SIM_START_Q = quarter_index(SIM_START_DATE)  # 8084
SIM_END_Q = quarter_index(SIM_END_DATE)      # 8103

# 各产品线大额赔案阈值（真实最终赔款超过该值即标记为大额，三角形中可剔除）
LARGE_CLAIM_THRESHOLD = {
    "住院医疗": 80_000.0,
    "门诊医疗": 20_000.0,
    "重疾险": 300_000.0,
}
DEFAULT_LARGE_THRESHOLD = 100_000.0  # 页面默认统一阈值，可按产品线覆盖


# ---------------------------------------------------------------------------
# 模拟器分段参数
# ---------------------------------------------------------------------------
@dataclass
class SegmentSpec:
    """一个 产品线|渠道 分段的真实生成参数。"""

    segment: str
    product_line: str
    channel: str
    # 每季度期望出险件数（scale=1 时）
    freq_per_quarter: float
    # 单件最终赔款：对数正态 log(mu, sigma)，mu 为对数均值
    severity_mu: float
    severity_sigma: float
    # 报案延迟（季度）离散分布，下标 = 延迟季度数，概率自动归一化
    report_delay_p: tuple[float, ...]
    # 赔付模式：累计赔付比例服从对数正态 CDF，median_pay_q 为中位赔付时点（季度）
    median_pay_q: float
    pay_pattern_sigma: float
    # 首次估损偏差：case = 剩余真实赔款 * lognormal(case_bias, case_sigma)
    case_bias: float
    case_sigma: float
    # 特殊流水概率
    deny_prob: float          # 拒赔率
    reopen_prob: float        # 结案后重开率
    recovery_prob: float      # 追偿发生概率
    recovery_frac: float      # 追偿金额占最终赔款比例（期望值）
    # 估损调整：每个未结季度向真实剩余值收缩的强度（0=不调整, 1=完全调整到位）
    case_adjust_shrink: float = 0.35


def _delay(p0: float, p1: float, p2: float, p3: float, p4: float = 0.015,
           p5: float = 0.005) -> tuple[float, ...]:
    total = p0 + p1 + p2 + p3 + p4 + p5
    return tuple(x / total for x in (p0, p1, p2, p3, p4, p5))


def default_segment_specs() -> list[SegmentSpec]:
    """6 个 产品线×渠道 分段的默认真实参数。"""
    return [
        # 住院医疗：件均数千、尾部较重，理赔较慢
        SegmentSpec("住院医疗|代理人", "住院医疗", "代理人", 1800,
                    8.6, 0.95, _delay(0.50, 0.30, 0.12, 0.06),
                    1.3, 0.70, 1.06, 0.28, 0.03, 0.05, 0.09, 0.16),
        SegmentSpec("住院医疗|互联网", "住院医疗", "互联网", 2400,
                    8.45, 0.90, _delay(0.62, 0.26, 0.08, 0.03),
                    1.0, 0.65, 1.04, 0.30, 0.05, 0.05, 0.08, 0.14),
        # 门诊医疗：高频低额、报案赔付都快，互联网渠道拒赔率高
        SegmentSpec("门诊医疗|代理人", "门诊医疗", "代理人", 1200,
                    6.6, 0.70, _delay(0.66, 0.26, 0.06, 0.02),
                    0.6, 0.55, 1.05, 0.25, 0.04, 0.04, 0.06, 0.12),
        SegmentSpec("门诊医疗|互联网", "门诊医疗", "互联网", 3000,
                    6.45, 0.65, _delay(0.78, 0.17, 0.04, 0.01),
                    0.45, 0.50, 1.03, 0.27, 0.08, 0.04, 0.05, 0.10),
        # 重疾险：低频高额、报案延迟长、赔付尾部很长
        SegmentSpec("重疾险|代理人", "重疾险", "代理人", 500,
                    11.1, 0.80, _delay(0.35, 0.33, 0.20, 0.09),
                    2.6, 0.85, 1.08, 0.22, 0.02, 0.06, 0.12, 0.18),
        SegmentSpec("重疾险|互联网", "重疾险", "互联网", 350,
                    10.9, 0.78, _delay(0.45, 0.32, 0.16, 0.06),
                    2.2, 0.80, 1.06, 0.24, 0.03, 0.06, 0.10, 0.16),
    ]


@dataclass
class SimConfig:
    """模拟器全局配置。"""

    scale: float = 0.3                  # 频率缩放系数（演示用 0.3，全量取 1.0）
    seed: int = 20261116
    start_date: date = SIM_START_DATE
    end_date: date = SIM_END_DATE
    valuation_date: date = DEFAULT_VALUATION_DATE
    max_pay_dev: int = 16               # 最晚支付进展期（季度），超出压到尾部
    # 数据质量问题注入比例（清洗模块需要识别的脏数据）
    dup_txn_rate: float = 0.005         # 重复流水
    missing_dim_rate: float = 0.010     # 产品线/渠道缺失
    bad_date_rate: float = 0.003        # 报案日期早于出险日期
    neg_amount_rate: float = 0.003      # 支付金额符号录反（应为正）
    orphan_txn_rate: float = 0.002      # 流水找不到赔案主表
    specs: list[SegmentSpec] = field(default_factory=default_segment_specs)

    @property
    def start_q(self) -> int:
        return quarter_index(self.start_date)

    @property
    def end_q(self) -> int:
        return quarter_index(self.end_date)

    @property
    def valuation_q(self) -> int:
        return quarter_index(self.valuation_date)

    def spec_map(self) -> dict[str, SegmentSpec]:
        return {s.segment: s for s in self.specs}
