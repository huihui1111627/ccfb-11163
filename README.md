# 健康险准备金数据平台

从理赔流水到进展三角形、准备金估计与回溯检验的一体化平台。解决准备金季度评估中
数据组与精算口径不一致、改口径反复返工的问题：**口径、清洗规则、估计参数全部代码化，
一次定义、可复算、可存档、可回溯。**

## 能力对照（需求 → 实现）

| # | 需求 | 实现 |
|---|------|------|
| 1 | 出险/报案/支付三种口径三角形 | `triangles.build_triangle(basis=accident/report/payment)`，报案口径自动剔除估值日后报案的 IBNR 赔案 |
| 2 | 已决/未决分开，未决调整按调整日期入进展期 | paid / incurred / count 三类指标；CASE 为快照语义，同进展期多次调整取最后值，已发生 = 累计已决 + 期末未决 |
| 3 | 大额赔案阈值剔除单独处理 | `exclude_large` + `reserving.large_claim_reserve` 逐案估计（支持手工覆盖），常规三角形与大额准备金分别计算后加回 |
| 4 | 链梯法 + BF 法，因子可手动选取 | `chain_ladder` / `bornhuetter_ferguson`；因子支持金额加权/简单平均/中位数/手动四种方式，尾部因子可覆盖 |
| 5 | 产品线/渠道切分，小样本合并 | `build_segment_triangles(min_claims=...)`，低于件数阈值的分段自动并入"其他"组合并重建 |
| 6 | 重开/拒赔/追偿正确计入 | REOPEN 后新支付按支付日入池；DENY 不计金额但计件数；RECOV 为负支付冲减已决（有专项测试） |
| 7 | 历史回溯检验 | `backtest.run_backtest` 在历史估值点重跑，对比观察期后实际进展与模拟器真实终极 |
| 8 | 每次估算参数与结果存档 | SQLite 落库：runs / run_factors / run_detail / run_large 四张表，可浏览、导出、删除 |

模拟器保留每个赔案的**真实最终赔款** `true_ultimate`（上帝视角潜变量），作为度量
CL/BF 估计偏差的标准答案；默认参数下链梯法对真实终极的回溯 MAPE 约 3%~4%。

## 目录结构

```
reserving_platform/        # 核心计算层（纯 Python，可独立测试/复用）
  config.py                # 6 个产品线×渠道分段的真实参数（频率/延迟/赔付/估损/特殊事件）
  period_utils.py          # 季度索引与进展期工具
  simulator.py             # 五年流水模拟器（赔案主表 + 交易台账 + 脏数据注入）
  cleaning.py              # 6 类质量检查与清洗规则、大额标记、季度列
  triangles.py             # 三口径 × 三指标三角形、分段切分与小样本合并
  reserving.py             # 进展因子、链梯法、BF 法、参数先验、大额逐案估计
  backtest.py              # 历史估值点回溯检验
  archive.py               # SQLite 存档
  persistence.py           # Parquet 导出/加载
app/                       # Streamlit 分析页面（6 页）
  Home.py                  # 数据生成入口与平台说明
  pages/1_理赔流水.py       # 主表/台账浏览与导出
  pages/2_数据清洗.py       # 质量报告、清洗明细、隔离区
  pages/3_进展三角形.py     # 口径/指标/切分/大额剔除，累计与增量三角形
  pages/4_准备金估计.py     # CL/BF、因子手动编辑、大额清单、偏差对比、存档
  pages/5_回溯检验.py       # 偏差趋势图与逐点对比
  pages/6_估算存档.py       # 历史 run 浏览与详情
scripts/
  generate_data.py         # 生成并导出 Parquet
  run_demo.py              # 命令行端到端演示
  smoke_app.py             # 页面无头冒烟测试
tests/                     # pytest：19 个测试（核心计算 + 特殊流水不变量）
```

## 快速开始

```bash
pip install -r requirements.txt

# 方式一：分析页面（推荐）
streamlit run app/Home.py
# 打开后先在首页生成数据，再依次走 流水 → 清洗 → 三角形 → 估计 → 回溯 → 存档

# 方式二：命令行端到端演示
python -m scripts.run_demo

# 方式三：生成 Parquet 供外部系统使用
python -m scripts.generate_data --scale 0.3 --out data/generated

# 测试
python -m pytest tests/ -v
python scripts/smoke_app.py
```

## 数据模型

**赔案主表 claims**（一案一行）：`claim_id, product_line, channel, accident_date,
report_date, close_date, status, true_ultimate, is_large, denied, reopened,
has_recovery`

**交易流水 transactions**（一案多行）：

| txn_type | 含义 | 金额字段 |
|---|---|---|
| PAY | 赔款支付 | amount（正） |
| RECOV | 追偿回收 | amount（负，冲减已决） |
| CASE | 未决估损设定/调整 | case_estimate（快照值） |
| CLOSE | 结案，估损清零 | case_estimate=0 |
| REOPEN | 结案后重开 | — |
| DENY | 拒赔 | — |

时间精确到日，三角形模块按季度归集；进展期单位为季度（0 = 同季度）。

## 关键口径说明

- **出险口径**：行为事故季度，准备金评估主口径。
- **报案口径**：行为报案季度；估值日后才报案的赔案（IBNR）整体不可见。
- **支付口径**：行为支付季度，全部落在进展期 0，现金流/支付模式视角。
- 三种口径只是归集方式不同，估值日前的支付总额在出险/支付口径下严格相等
  （有对账测试保证）。
- **清洗规则**：重复流水去重（保留最早录入）、维度众数回填、报案早于出险修正、
  支付符号纠正、孤儿流水与关键日期缺失隔离。清洗幂等，修复数与模拟器注入数可对照。

## 方法学

- 链 ladder：`ultimate = 最新对角线 × CDF`；零分母进展期因子视为 1.0；
  已报案未支付的新事故季度按补零行处理并用下一期 CDF，不会被漏估。
- BF：`ultimate = latest + 先验终极 × (1 − 1/CDF) × 先验强度`；
  先验默认由模拟器分段参数（频率 × 对数正态件均 × 非拒赔率）生成，页面可按 ELR 调整。
- 大额件：阈值可按统一值或产品线配置，未决默认取最近 CASE 快照，支持逐案手工覆盖。
