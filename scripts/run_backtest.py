"""一键跑通「数据 → 因子 → 策略 → 回测 → 指标 → 图表 → 结果落盘」。

用法（项目根目录）：
    python scripts/run_backtest.py                 # 完整流程 + 参数敏感性实验
    python scripts/run_backtest.py --no-ablation   # 只跑主策略
    python scripts/run_backtest.py --top-n 20      # 改参数
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from qlab.analytics.metrics import perf_metrics  # noqa: E402
from qlab.analytics.report import ResultExporter  # noqa: E402
from qlab.backtest import PortfolioBacktester  # noqa: E402
from qlab.config import (  # noqa: E402
    BacktestConfig,
    DataConfig,
    StrategyConfig,
)
from qlab.data import DataPortal  # noqa: E402
from qlab.logging_utils import get_logger  # noqa: E402
from qlab.strategy import MultiFactorStrategy  # noqa: E402
from qlab.viz.figures import build_all  # noqa: E402

log = get_logger("qlab.pipeline")


# ----------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="QLab 回测主流程")
    ap.add_argument("--start", default="2019-01-01", help="回测起点")
    ap.add_argument("--end", default=None, help="回测终点（默认数据末端）")
    ap.add_argument("--is-end", default="2022-12-31", help="样本内结束日")
    ap.add_argument("--top-n", type=int, default=30, help="持仓只数")
    ap.add_argument("--factor-k", type=int, default=5, help="入选因子个数")
    ap.add_argument("--weighting", default="icir", choices=["icir", "equal"])
    ap.add_argument("--no-timing", action="store_true", help="关闭择时叠加")
    ap.add_argument("--require-start", default="2017-09-01",
                    help="只保留该日期前已有行情的标的")
    ap.add_argument("--no-ablation", action="store_true", help="跳过参数敏感性实验")
    ap.add_argument("--capital", type=float, default=1_000_000.0)
    return ap.parse_args()


# ----------------------------------------------------------------------
def build_configs(args) -> tuple[DataConfig, BacktestConfig, StrategyConfig]:
    data = DataConfig()
    bt = BacktestConfig(
        start=args.start, end=args.end, is_end=args.is_end,
        top_n=args.top_n, initial_cash=args.capital,
    )
    strat = StrategyConfig(
        factor_select_k=args.factor_k,
        weighting=args.weighting,
        timing_enabled=not args.no_timing,
    )
    return data, bt, strat


def run_once(panel, bt_panel, strategy: MultiFactorStrategy, bt_cfg: BacktestConfig):
    """执行一次「生成目标权重 → 回测」。"""
    targets = strategy.generate_targets(panel, start=bt_cfg.start)
    targets = targets[targets.index >= bt_panel.dates[0]]
    engine = PortfolioBacktester(bt_cfg)
    return engine.run(bt_panel, targets)


# ----------------------------------------------------------------------
def main() -> int:
    args = parse_args()
    t0 = time.time()
    data_cfg, bt_cfg, strat_cfg = build_configs(args)

    # 0) 配置快照：把本次运行的全部参数落盘，便于复现与答辩追溯
    snapshot = {
        "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "data": dataclasses.asdict(data_cfg),
        "backtest": dataclasses.asdict(bt_cfg),
        "strategy": dataclasses.asdict(strat_cfg),
        "cli_args": vars(args),
    }
    snapshot_path = ROOT / "output" / "tables" / "config.json"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    # 1) 数据
    portal = DataPortal(data_cfg)
    portal.ensure_universe()
    panel = portal.load(require_start=args.require_start)
    bt_panel = panel.slice(bt_cfg.start, bt_cfg.end)
    panel_is = panel.slice(None, bt_cfg.is_end)

    log.info("回测区间 %s ~ %s；样本内结束 %s；股票池 %d 只",
             bt_panel.dates[0].date(), bt_panel.dates[-1].date(),
             bt_cfg.is_end, len(panel.symbols))

    # 2) 策略（只用样本内数据确定因子与权重）
    strategy = MultiFactorStrategy(strat_cfg, bt_cfg)
    strategy.fit(panel_is)

    # 3) 回测
    result = run_once(panel, bt_panel, strategy, bt_cfg)

    # 4) 指标与结果落盘
    exporter = ResultExporter()
    metrics = exporter.save_metrics(result, rf=bt_cfg.risk_free_rate)
    exporter.save_series(result)
    exporter.save_trades(result)
    exporter.save_monthly(result)
    exporter.save_factor_table(strategy)
    exporter.save_excel(result, strategy, rf=bt_cfg.risk_free_rate)
    exporter.save_markdown(result, strategy, metrics)
    exporter.save_summary_json(
        result, strategy,
        panel_meta={
            "universe_size": len(panel.symbols),
            "universe_requested": int(data_cfg.universe_size),
            "data_start": str(panel.dates[0].date()),
            "data_end": str(panel.dates[-1].date()),
            "benchmark": panel.benchmark_name,
            "is_end": bt_cfg.is_end,
            "top_n": bt_cfg.top_n,
            "factor_k": strat_cfg.factor_select_k,
            "timing_ma": strat_cfg.timing_ma_window,
            "timing_low": strat_cfg.timing_low_exposure,
            "provider": data_cfg.provider,
            "adjust": data_cfg.adjust,
            "names": panel.names,
        },
        rf=bt_cfg.risk_free_rate,
    )

    # 5) 图表
    figs = build_all(result, strategy.ic_table, [s.name for s in strategy.specs],
                     split_date=bt_cfg.is_end)
    log.info("图表输出：%s", ", ".join(p.name for p in figs.values()))

    # 6) 控制台摘要
    _print_summary(metrics)

    # 7) 参数敏感性 / 消融实验
    if not args.no_ablation:
        _run_ablation(panel, bt_panel, strategy, bt_cfg, strat_cfg)

    log.info("全流程完成，用时 %.1fs", time.time() - t0)
    return 0


# ----------------------------------------------------------------------
def _print_summary(metrics: pd.DataFrame) -> None:
    print("\n" + "=" * 78)
    print("回测指标（策略）".center(70))
    print("=" * 78)
    cols = [c for c in metrics.columns]
    show = ["累计收益率", "年化收益率", "年化波动率", "最大回撤", "夏普比率",
            "索提诺比率", "卡玛比率", "超额收益(年化)", "信息比率", "Beta"]
    print(f"{'指标':<14}" + "".join(f"{c:>14}" for c in cols))
    for m in show:
        if m not in metrics.index:
            continue
        row = "".join(f"{_fmt(metrics.loc[m, c], m):>14}" for c in cols)
        print(f"{m:<14}{row}")
    print("=" * 78 + "\n")


def _fmt(v, metric: str) -> str:
    if pd.isna(v):
        return "—"
    if metric in ("夏普比率", "索提诺比率", "卡玛比率", "信息比率", "Beta"):
        return f"{v:.2f}"
    return f"{v * 100:.2f}%"


# ----------------------------------------------------------------------
def _run_ablation(panel, bt_panel, strategy, bt_cfg, strat_cfg) -> None:
    """参数敏感性实验：逐项改变配置，观察指标变化。"""
    log.info("开始参数敏感性实验……")
    rows = []

    def record(name: str, result, note: str = ""):
        m = perf_metrics(result.nav, result.benchmark_nav,
                         rf=bt_cfg.risk_free_rate, trades=result.trades,
                         avg_equity=float(result.equity.mean()))
        rows.append({
            "配置": name,
            "年化收益率": m.get("年化收益率"),
            "最大回撤": m.get("最大回撤"),
            "夏普比率": m.get("夏普比率"),
            "卡玛比率": m.get("卡玛比率"),
            "年化超额": m.get("超额收益(年化)"),
            "年化换手率": m.get("年化换手率"),
            "备注": note,
        })

    # 基准对照：买入持有沪深 300
    # 主策略
    main_result = run_once(panel, bt_panel, strategy, bt_cfg)
    record("主策略（Top30 + ICIR加权 + 择时）", main_result)

    # 关闭择时（复用同一套因子权重，避免重新拟合引入噪声）
    cfg_nt = dataclasses.replace(strat_cfg, timing_enabled=False)
    strat_nt = dataclasses.replace(strategy, cfg=cfg_nt)
    record("关闭择时", run_once(panel, bt_panel, strat_nt, bt_cfg))

    # 因子等权（需要重新按等权生成目标，但保留样本内选出的因子与方向）
    cfg_eq = dataclasses.replace(strat_cfg, weighting="equal")
    strat_eq = MultiFactorStrategy(cfg_eq, bt_cfg).fit(panel.slice(None, bt_cfg.is_end))
    record("因子等权（重新选因子）", run_once(panel, bt_panel, strat_eq, bt_cfg))

    # 持仓只数（top_n 由策略决定目标权重，因此必须连策略配置一起替换）
    for n in (20, 50):
        cfg_n = dataclasses.replace(bt_cfg, top_n=n)
        strat_n = dataclasses.replace(strategy, bt_cfg=cfg_n)
        record(f"持仓 {n} 只", run_once(panel, bt_panel, strat_n, cfg_n))

    # 调仓频率：季度（复用同一套因子权重，只改调仓日历）
    strat_q = dataclasses.replace(strategy, cfg=strat_cfg)
    strat_q.rebalance_dates = lambda index, start=None: _quarterly_dates(index, start)
    record("季度调仓", run_once(panel, bt_panel, strat_q, bt_cfg))

    # 仅多头等权持有全部样本（对照：不做选股）
    df = pd.DataFrame(rows).set_index("配置")
    for c in ("年化收益率", "最大回撤", "年化超额", "年化换手率"):
        df[c] = (df[c] * 100).round(2)
    df[["夏普比率", "卡玛比率"]] = df[["夏普比率", "卡玛比率"]].round(2)
    path = ROOT / "output" / "tables" / "ablation.csv"
    df.to_csv(path, encoding="utf-8-sig")
    print("\n参数敏感性实验（全样本）")
    print(df.to_string())
    log.info("实验表写入 %s", path)


def _quarterly_dates(index, start=None) -> list:
    """季度调仓日：每季度最后一个交易日。"""
    s = pd.Series(index, index=index)
    q = s.groupby([index.year, index.quarter]).max()
    dates = [pd.Timestamp(d) for d in sorted(q.to_numpy())]
    if start is not None:
        first = index[index >= pd.Timestamp(start)]
        dates = [d for d in dates if d >= pd.Timestamp(start)]
        if len(first) and (not dates or dates[0] > first[0]):
            dates = [first[0]] + dates
    elif len(index) and (not dates or dates[0] > index[0]):
        dates = [index[0]] + dates
    return [d for d in dates if d in set(index)]


if __name__ == "__main__":
    raise SystemExit(main())
