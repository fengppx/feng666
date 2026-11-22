# QLab · A 股多因子选股 + 趋势择时量化交易系统

> 课程作业实现：**数据接入 → 因子计算 → 策略信号 → 回测撮合 → 指标与图表 → Web 看板 / Word 报告**，
> 全部模块本地可运行、结果可复现，不依赖任何付费数据或云服务。
>
> **第一次使用？先看 [新手使用说明](新手使用说明.md)，或直接双击 `回测.bat`。**

## 1. 项目概览

QLab 是一个面向 A 股日频数据的多因子选股系统：在样本内用因子 IC/ICIR 挑选有效因子并确定权重，
每个调仓日按综合得分买入前 N 只等权持有，再叠加一层基于沪深 300 的均线择时控制总仓位，
最后用逐日撮合的回测引擎（含佣金、印花税、滑点、整手约束）评估策略相对基准的表现。

- 数据层：腾讯 / 新浪行情适配器，本地 CSV 缓存，可切换 akshare / baostock / yfinance
- 计算层：12 个候选因子（动量、反转、波动、流动性、偏度等），横截面缩尾 + z-score，IC/ICIR 分析
- 策略层：样本内选因子、ICIR 加权合成、月末调仓、120 日均线择时
- 回测层：t 日收盘出信号、t+1 开盘撮合，先卖后买，停牌跳过，逐日市值结算
- 展示层：Pillow 绘制净值/回撤/热力图/IC/仓位图，标准库 HTTP 服务 + 原生 JS 看板，python-docx 生成技术报告

## 2. 快速开始

环境要求：Python **3.10+**（推荐 3.12），Windows / macOS / Linux 均可。

```bash
# 1) 建虚拟环境（Windows PowerShell）
python -m venv .venv
.venv\Scripts\Activate.ps1          # 若被执行策略拦截：Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

# 2) 安装依赖
pip install -r requirements.txt
# 需要跑单元测试时（Word 报告依赖的 python-docx 已在 requirements 中）：
pip install pytest

# 3) 首次抓取行情（联网，300 只约 10~20 分钟，接口有限流会自动补抓）
python scripts/download_data.py --size 300 --rounds 3

# 4) 跑完整回测（生成指标表、图表与结构化结果）
python scripts/run_backtest.py

# 5) 起 Web 看板（带后端，可现场改参数重跑回测）
python -m qlab web            # http://127.0.0.1:8000

# 6) 生成 Word 技术报告（需先跑过回测）
python -m qlab report         # output/report/项目技术报告.docx
```

若只想快速验证流程，可以把股票池调小：

```bash
python scripts/download_data.py --size 30 --rounds 1
python scripts/run_backtest.py --top-n 10 --no-ablation
```

### 在 VS Code 里打开

1. `文件 → 打开文件夹`，选择本目录（不要只打开单个 `.py` 文件，否则模块导入和调试配置都会失效）。
2. `Ctrl+Shift+P` → `Python: Select Interpreter` → 选 `.venv\Scripts\python.exe`。
3. `Ctrl+Shift+D` 打开运行与调试，内置 6 个配置：跑回测、跳过灵敏度实验、抓取数据、启动看板、生成报告、调试当前文件。
4. 也可以在集成终端里直接执行上面的命令（`python.terminal.activateEnvironment` 会让终端自动激活 `.venv`）。

## 3. 命令速查

| 命令 | 作用 |
| --- | --- |
| `python -m qlab info` | 查看数据缓存、股票池、产物概况 |
| `python scripts/download_data.py [--size N] [--provider tencent\|sina\|akshare\|baostock] [--force]` | 抓取/更新行情缓存 |
| `python scripts/run_backtest.py [--top-n 30] [--factor-k 5] [--weighting icir\|equal] [--no-timing] [--no-ablation]` | 回测主流程 |
| `python -m qlab backtest` | 等价于上面脚本的简写入口 |
| `python -m qlab web [--port 8000]` | 启动 Web 看板 |
| `python -m qlab report` | 用最近一次回测结果生成 Word 技术报告 |
| `python -m unittest discover -s tests -v` | 运行单元测试（撮合、成本、指标、因子无前视） |

## 4. 目录结构

```
qlab-quant/
├── qlab/
│   ├── config.py              # 路径、回测参数、交易成本、因子池（所有可调项集中于此）
│   ├── cli.py                 # python -m qlab 命令行入口
│   ├── data/                  # 数据层：providers（tencent/sina/akshare…）、缓存、质量体检、面板装配
│   ├── factors/               # 计算层：因子库、预处理、IC/ICIR 分析
│   ├── strategy/              # 策略层：因子筛选与加权、择时、目标权重生成
│   ├── backtest/              # 回测层：broker（费用/成交）与组合引擎
│   ├── analytics/             # 指标层：绩效指标、分段统计、CSV/Excel/JSON/Markdown 导出
│   ├── viz/                   # 展示层：Pillow 绘图（不依赖 matplotlib）
│   ├── webapp/                # 展示层：http.server 后端 + 原生 JS 看板
│   ├── report_builder.py      # Word 技术报告生成
│   └── logging_utils.py
├── scripts/
│   ├── download_data.py       # 一键抓取行情
│   └── run_backtest.py        # 一键回测流水线
├── tests/                     # 26 个单元测试（撮合/费用/指标/因子/权重约束）
├── data/raw/                  # 行情缓存（不入库，可重新抓取）
├── output/{tables,figures,report,logs}/
├── requirements.txt
└── pyproject.toml
```

## 5. 策略设计

1. **候选因子池**（`qlab/config.py: FACTOR_POOL`）：12 个月动量、6 个月动量、1 个月反转、5 日反转、
   20/60 日波动率、20 日最大单日涨幅、20 日换手、60 日乖离、14 日 RSI、20 日振幅、60 日偏度。
2. **样本内筛选**：只用 2019-01-01 ~ 2022-12-31 的数据，按月度采样计算每个因子的 Rank IC 与 ICIR，
   保留 `|ICIR|` 最大的前 k 个（默认 5 个），剔除相关性高于 0.75 的共线因子。
3. **因子合成**：方向取样本内 IC 符号，权重 ∝ `|ICIR|`（可切换等权），每日横截面 2% 双侧缩尾后 z-score 加权。
4. **组合构建**：综合得分前 `top_n` 只（默认 30）等权，单票权重上限 8%，月末最后一个交易日生成信号，
   次一交易日开盘调仓。
5. **趋势择时**：沪深 300 收盘价高于 120 日均线时满仓，否则仓位系数降到 30%；只调总仓位，不改选股。
6. **交易成本**：佣金万 2.5（单笔最低 5 元）、卖出印花税（2023-08-28 前千 1、之后千 0.5）、
   单边滑点 5bp、100 股整手约束。

## 6. 回测设置与指标

- 区间：默认 2019-01-01 至数据末端；样本内 2019-2022，样本外 2023 起，两段分别报告
- 基准：沪深 300（价格指数，与个股不复权价格同口径）；基准缓存缺失时退化为股票池等权净值
- 必报指标：累计收益率、年化收益率、最大回撤、夏普比率；另附索提诺、卡玛、信息比率、
  超额收益（年化）、Beta/Alpha、胜率、换手率等
- 净值曲线统一归一化到 1.0 起算，并标注建仓/加仓/降仓点与样本内外分界线

指标定义见 `qlab/analytics/metrics.py`，年化统一按 252 个交易日。

## 7. 输出产物

| 路径 | 内容 |
| --- | --- |
| `output/tables/metrics_by_segment.csv` | 全样本 / 样本内 / 样本外指标表 |
| `output/tables/nav_daily.csv` | 策略与基准的逐日净值、回撤、仓位 |
| `output/tables/trades.csv` | 逐笔成交明细（含费用） |
| `output/tables/metrics_long.csv`、`monthly_returns.csv`、`factor_ic.csv`、`factor_selected.csv` | 指标长表、月度收益、因子 IC、入选因子 |
| `output/tables/backtest_result.xlsx` | 以上内容的 Excel 汇总（答辩展示用） |
| `output/tables/result_summary.json` | Web 看板与报告读取的结构化结果 |
| `output/tables/ablation.csv` | 参数敏感性 / 消融实验结果（关闭择时、换持仓数、季度调仓等） |
| `output/tables/config.json` | 本次运行的完整参数快照（数据、回测、策略配置），用于复现与追溯 |
| `output/figures/*.png` | 净值曲线、回撤、月度热力图、因子 IC、仓位、架构图 |
| `output/report/项目技术报告.docx` | 自动生成的技术报告（`python -m qlab report`） |

## 8. 数据源说明

- 默认使用**腾讯 / 新浪**公开行情接口，`pip install -r requirements.txt` 后即可用，无需注册；
  接口对高频访问有限流，抓取失败会自动补抓。
- 可选：`pip install akshare` 或 `pip install baostock`，用 `--provider akshare|baostock` 切换；
  美股可用 `pip install yfinance` 后 `--provider yfinance`。
- 行情缓存写入 `data/raw/*.csv`，体积较大，已加入 `.gitignore`；复现时重新执行抓取脚本即可。

## 9. 复现性

- 全部参数集中在 `qlab/config.py`，命令行可覆盖；随机性来源已固定（无随机采样）。
- 因子与回测严格遵守「只用当日及之前信息」：滚动窗口全部向后取，信号 t 日收盘生成、t+1 开盘成交；
  `tests/test_engine.py` 对此有专门用例。
- 样本内 / 样本外切分固定，样本外数据不参与任何因子选择或参数确定。

## 10. 已知限制

- 当前仓库内的行情缓存只覆盖部分标的（网络抓取未跑满 300 只），首次使用请在本地执行
  `python scripts/download_data.py --size 300 --rounds 3` 补齐；基准缓存缺失时会退化为等权基准。
- 回测为日频、按开盘价成交，未模拟涨跌停无法成交、停牌复牌跳空等极端情形。
- 不包含实盘交易接口，仅用于策略研究与课程演示。

## 11. AI 辅助说明

本项目在开发过程中使用 AI 编程助手辅助生成工程骨架、部分模块实现与调试；
策略设计、因子定义、回测规则与全部结论均由作者复核、运行验证并负责答辩。

## 12. License

MIT，见 `LICENSE`。
