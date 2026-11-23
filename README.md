# QLab：A 股多因子选股 + 趋势择时

> 周三上午班量化交易课程的小组作业。简单说，我们做了一套能自己跑通的 A 股量化流水线：
> 抓行情、算因子、选股、回测、出指标和图，另外带一个网页看板和一个自动生成的 Word 报告。
>
> 第一次用的话，先看 [新手使用说明](新手使用说明.md)，或者直接双击 `回测.bat`。

## 1. 这东西是干什么的

选股逻辑不复杂。先准备十来个候选因子，用 2019–2022 这段样本内的数据算出每个因子的 IC 和
ICIR，挑出还算有区分度的几个，按 ICIR 加权合成一个综合分；每个月末按分数买前 30 只，等权持有。
上面再叠一层择时：看沪深 300 在不在 120 日均线上方，决定这一期满仓还是只放三成。

回测是自己写的，没有套 backtrader 这类框架。做法是 T 日收盘算信号、T+1 开盘价成交，
买卖都算了佣金、印花税、滑点，买入按 100 股整手向下取整，停牌那天跳过不调仓。

代码分了几层：数据、计算、策略、回测、输出。层与层之间只通过 `MarketPanel`、`BacktestResult`
这些数据结构打交道，所以换数据源或者换策略，回测那部分不用动。

## 2. 跑起来

环境 Python 3.10+（我们用的是 3.12），Windows / macOS / Linux 都行。

```bash
# 建环境
python -m venv .venv
.venv\Scripts\Activate.ps1        # 被策略拦了就 Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

# 装依赖
pip install -r requirements.txt
pip install pytest                # 要跑单元测试才需要

# 抓行情（联网，300 只大概十几分钟，接口会限流，脚本自己会补抓）
python scripts/download_data.py --size 300 --rounds 3

# 跑回测
python scripts/run_backtest.py

# 起看板
python -m qlab web                # http://127.0.0.1:8000

# 生成 Word 报告（要先跑过回测）
python -m qlab report             # output/report/项目技术报告.docx
```

只想看看流程通不通，不用抓 300 只，30 只就够：

```bash
python scripts/download_data.py --size 30 --rounds 1
python scripts/run_backtest.py --top-n 10 --no-ablation
```

### 用 VS Code 打开

别只打开单个 `.py` 文件，要打开整个目录，不然模块导入会报错。解释器选 `.venv\Scripts\python.exe`。

`.vscode/launch.json` 里配了几个调试入口（跑回测、抓数据、起看板、生成报告等）。不过 `.vscode/`
默认没进仓库，克隆下来是没有的，用上面的命令行一样能跑。

## 3. 命令速查

| 命令 | 作用 |
| --- | --- |
| `python -m qlab info` | 看数据缓存、股票池、产物概况 |
| `python scripts/download_data.py [--size N] [--provider tencent\|sina\|akshare\|baostock] [--force]` | 抓/更新行情缓存 |
| `python scripts/run_backtest.py [--top-n 30] [--factor-k 5] [--weighting icir\|equal] [--no-timing] [--no-ablation]` | 回测主流程 |
| `python -m qlab backtest` | 上面那条的简写入口 |
| `python -m qlab web [--port 8000]` | 启动看板 |
| `python -m qlab report` | 用最近一次回测结果生成 Word 报告 |
| `python -m unittest discover -s tests -v` | 跑单元测试 |

## 4. 目录结构

```
qlab-quant/
├── qlab/
│   ├── config.py              # 路径、回测参数、交易成本、因子池（能调的都在这）
│   ├── cli.py                 # python -m qlab 入口
│   ├── data/                  # 数据层：providers、缓存、质量检查、面板装配
│   ├── factors/               # 计算层：因子库、预处理、IC/ICIR
│   ├── strategy/              # 策略层：选因子、加权、择时、目标权重
│   ├── backtest/              # 回测层：broker（费用/成交）和组合引擎
│   ├── analytics/             # 指标与导出：绩效、分段、CSV/Excel/JSON/Markdown
│   ├── viz/                   # 画图：Pillow 手绘，没装 matplotlib
│   ├── webapp/                # 看板：http.server 后端 + 原生 JS
│   ├── report_builder.py      # Word 报告生成
│   └── logging_utils.py
├── scripts/
│   ├── download_data.py       # 抓行情
│   └── run_backtest.py        # 回测流水线
├── tests/                     # 26 个单元测试
├── data/raw/                  # 行情缓存（不入库）
├── output/{tables,figures,report,logs}/
├── requirements.txt
└── pyproject.toml
```

## 5. 策略是怎么定的

**候选因子**都在 `qlab/config.py` 的 `FACTOR_POOL` 里，一共 12 个：12 个月动量、6 个月动量、
1 个月反转、5 日反转、20/60 日波动率、20 日最大单日涨幅、20 日换手、60 日乖离、14 日 RSI、
20 日振幅、60 日偏度。每个因子的方向也写在那张表里（+1 表示越大越好，-1 表示越小越好）。

**选因子**只用样本内（2019-01-01 到 2022-12-31）的月度调仓日。每个因子算 Rank IC 和 ICIR，
按 |ICIR| 从大到小过一遍，边选边去共线：跟已经选中的因子相关性超过 0.75 就跳过，免得同一个
东西算两遍权重。门槛设的是 |ICIR| ≥ 0.15，最多留 5 个。

实际跑下来只有 3 个过线：`skew_60`、`turn_20`、`bias_60`，而且方向都是反的。样本内 t 值分别是
-1.78 / -1.62 / -1.54，严格说不算显著，这一点报告里也照实写了。

**合成打分**的方向取样本内 IC 的符号，权重按 |ICIR| 归一化（也能切成等权）。每个调仓日先做
2% 双侧缩尾，再横截面 z-score，几个因子加起来。要是某天某个因子缺数据，就按还有效的那部分
权重重新归一，避免停牌股把分数带偏。

**组合**取综合分前 30 只等权，单票上限 8%（30 只本身才 3.3%，这个上限主要是防极端情况）。
月末最后一个交易日出信号，下一个交易日开盘调仓。

**择时**看沪深 300 收盘价在不在 120 日均线上方，在上方满仓，下方降到 30%。它只改总仓位，
不影响选了哪些股票。

**成本**按 A 股实际规则来：佣金万 2.5、单笔最低 5 块；印花税只在卖出收，2023-08-28 之前
千分之一、之后万分之五；单边滑点 5bp；买入 100 股一手向下取整。

## 6. 回测设置和结果

区间默认 2019-01-01 到数据末端，样本内 2019-2022、样本外 2023 年起，两段分开报。
基准用沪深 300 价格指数，跟我们用的不复权个股价格是同一个口径。要是基准缓存没抓到，
会退化成股票池等权净值，报告里也会标明是回落基准。

课程要求的四个指标都有：累计收益、年化、最大回撤、夏普。另外还算了索提诺、卡玛、信息比率、
年化超额、Beta/Alpha、胜率和换手率。年化一律按 252 个交易日，公式在 `qlab/analytics/metrics.py`。

净值曲线两条都从 1.0 起算，图上标了建仓和加减仓的位置，也画了样本内外的分界虚线。

我们这次跑出来的全样本结果（2019-01 到 2026-11）：

| | 策略 | 沪深300 |
| --- | ---: | ---: |
| 累计收益 | 41.5% | 33.2% |
| 年化 | 4.7% | 3.8% |
| 最大回撤 | -15.6% | -45.6% |
| 夏普 | 0.31 | 0.22 |

说实话收益没赢多少，样本内还输给基准（年化超额 -2.3%），但回撤小了一大截，这也是我们留着
择时的理由。不过择时那个 30% 的低仓位设得偏狠：消融实验里关掉择时，年化能到 15.4%，
回撤变成 -23.1%。用收益换回撤换到这个程度，值不值得我们也还在讨论，报告里把两个版本的
数都摆出来了。

## 7. 输出产物

| 路径 | 内容 |
| --- | --- |
| `output/tables/metrics_by_segment.csv` | 全样本 / 样本内 / 样本外指标表 |
| `output/tables/nav_daily.csv` | 逐日净值、回撤、仓位 |
| `output/tables/trades.csv` | 逐笔成交明细（含费用） |
| `output/tables/metrics_long.csv`、`monthly_returns.csv`、`factor_ic.csv`、`factor_selected.csv` | 指标长表、月度收益、因子 IC、入选因子 |
| `output/tables/backtest_result.xlsx` | 上面这些东西的 Excel 汇总，答辩时翻着方便 |
| `output/tables/result_summary.json` | 看板和报告读的结构化结果 |
| `output/tables/ablation.csv` | 参数敏感性实验（关择时、换持仓数、季度调仓） |
| `output/tables/config.json` | 本次运行的全部参数快照 |
| `output/figures/*.png` | 净值、回撤、月度热力图、因子 IC、仓位、架构图 |
| `output/report/项目技术报告.docx` | 自动生成的报告，`python -m qlab report` 出来的 |

## 8. 数据从哪来

行情走腾讯的 K 线接口，不需要 token；股票池列表走新浪的行情中心接口。两个都是公开的，
装完依赖就能用。接口对频繁访问会返回 501，所以抓取脚本默认单线程、每次请求之间停一下，
还支持多轮补抓（`--rounds`）。

另外也写了 akshare / baostock / yfinance 的适配器，`pip install` 对应的库之后用 `--provider`
就能切过去。默认流程走的是腾讯。

抓下来的 CSV 放在 `data/raw/`，加起来 30MB 左右，已经写进 `.gitignore`，所以克隆下来是没有
行情的，得自己跑一遍抓取。

## 9. 关于复现

参数全在 `qlab/config.py`，一部分命令行能覆盖。没有随机采样，同一份数据跑两遍结果一样，
前提是 pandas / numpy 版本一致——`requirements.txt` 里现在写的是下限，换个大版本数字可能会
飘一点。

因子和回测都只用当天及之前的数据：滚动窗口全往回取，T 日收盘出信号、T+1 开盘成交。
`tests/test_engine.py` 里专门有个用例，拿"次日跳空 10 倍"的构造行情去验，要是真按收盘价
成交，净值会很离谱地放大。

样本内外的切分是写死的，样本外那段不参与任何因子选择或参数确定。

## 10. 还有哪些问题

- **股票池有幸存者偏差**。我们按抓取当天的市值取前 300，不是历史成分股，等于天然只留下了
  活到今天的公司，超额收益会被高估。这个是最需要提前说明的。
- **实际只有 150 只进了回测**。请求了 300 只，其中 93 只历史不够长（2017-09 之前没行情），
  另外 57 只被数据质量检查刷掉了——腾讯接口对个别标的有数量级错误（比如 601088、000408），
  一天的涨幅能到 60%，不剔掉整个净值都废了。所以真实股票池比预期小一半。
- **不复权没有分红**。绝对收益大概被低估 2 个点，不过基准也是价格指数，两边口径一致，
  超额收益的比较还是公平的。
- **日频、按开盘价成交**，没模拟涨跌停买不到、复牌跳空这些情况。
- **没有接实盘**，这套东西只用来做研究和课程演示。

## 11. 关于 AI 辅助

课程允许用 AI 编程工具，但要求写清楚用了多少。我们的情况是：工程骨架、数据适配器、回测引擎、
因子库、指标和绘图这几块，是在 AI 助手（DeepSeek Harness）配合下写出来的，之后我们逐段读过、
跑过、改过；策略思路（多因子选股加均线择时这层结构）、因子方向怎么解释、样本内外怎么切、
成本怎么设，是我们自己定的。

哪些是 AI 直接给的、哪些是我们改的，提交记录和 `output/report/项目技术报告.docx` 里能对上。
核心策略和回测逻辑没有调第三方框架，都是自己实现的。

## 12. License

MIT，见 `LICENSE`。
