"""健康险准备金数据平台核心包。

模块说明
--------
- period_utils: 季度期间工具（季度索引、进展期换算）
- config:       平台配置与模拟器真实参数
- simulator:    理赔流水模拟器（含真实最终赔款标准答案）
- cleaning:     数据质量检查与清洗规则
- triangles:    进展三角形构建（出险/报案/支付三口径）
- reserving:    链梯法、BF 法准备金估计
- backtest:     历史回溯检验
- archive:      估算参数与结果存档
"""

__version__ = "1.0.0"
