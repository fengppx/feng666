"""命令行入口：``python -m qlab <子命令>``。

    data       抓取/更新行情缓存
    backtest   跑完整回测流水线（含图表与结果落盘）
    web        启动 Web 看板
    report     生成 Word 技术报告
    info       查看当前数据与配置概况
"""

from __future__ import annotations

import argparse
import sys

from qlab import __version__
from qlab.config import (
    FIGURE_DIR,
    OUTPUT_DIR,
    RAW_DIR,
    TABLE_DIR,
    AppConfig,
    DataConfig,
)
from qlab.logging_utils import get_logger

log = get_logger("qlab.cli")


def _data(args) -> int:
    from qlab.data import DataPortal, cache

    cfg = DataConfig(
        universe_size=args.size,
        fetch_start=args.start,
        request_workers=args.workers,
        request_pause=args.pause,
    )
    portal = DataPortal(cfg, provider=args.provider)
    portal.ensure_universe(force=args.force)
    portal.download(force=args.force, min_rows=1500, start_before="2017-09-01")
    portal.download_benchmarks()
    manifest = cache.read_manifest()
    log.info("缓存标的数：%d", len(manifest.get("symbols", {})))
    return 0


def _backtest(args) -> int:
    import runpy

    from qlab.config import PROJECT_ROOT

    script = PROJECT_ROOT / "scripts" / "run_backtest.py"
    if not script.exists():
        raise FileNotFoundError(f"找不到回测脚本：{script}")
    sys.argv = [str(script)]
    if args.top_n:
        sys.argv += ["--top-n", str(args.top_n)]
    if args.no_timing:
        sys.argv.append("--no-timing")
    if args.no_ablation:
        sys.argv.append("--no-ablation")
    runpy.run_path(str(script), run_name="__main__")
    return 0


def _web(args) -> int:
    from qlab.webapp import serve

    serve(host=args.host, port=args.port)
    return 0


def _report(args) -> int:
    from qlab.report_builder import build_technical_report

    path = build_technical_report(output_dir=args.out)
    print(f"技术报告已生成：{path}")
    return 0


def _info(_args) -> int:
    from qlab.data import cache

    manifest = cache.read_manifest()
    universe = cache.load_universe()
    print(f"QLab v{__version__}")
    print(f"  数据目录：{RAW_DIR}（{len(list(RAW_DIR.glob('*.csv')))} 个 CSV）")
    print(f"  缓存清单：{len(manifest.get('symbols', {}))} 个标的，更新于 {manifest.get('updated_at')}")
    if universe is not None:
        print(f"  股票池：{len(universe)} 只（快照 {universe['snapshot_date'].iloc[0]}）")
    print(f"  输出目录：{OUTPUT_DIR}")
    for d in (TABLE_DIR, FIGURE_DIR):
        files = sorted(p.name for p in d.glob('*'))
        print(f"    {d.name}/: {', '.join(files[:8]) if files else '(空)'}")
    cfg = AppConfig()
    print(f"  默认回测：{cfg.backtest.start} ~ 今天，样本内截至 {cfg.backtest.is_end}，"
          f"Top{cfg.backtest.top_n}，基准 {cfg.data.benchmark_name}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="qlab", description="QLab —— A股多因子量化交易系统")
    ap.add_argument("--version", action="version", version=f"qlab {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("data", help="抓取行情数据")
    p.add_argument("--size", type=int, default=300)
    p.add_argument("--start", default="2017-06-01")
    p.add_argument("--force", action="store_true")
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--pause", type=float, default=0.5)
    p.add_argument("--provider", default="tencent",
                   choices=["tencent", "sina", "akshare", "baostock", "yfinance"],
                   help="行情数据源（默认腾讯；akshare/baostock 需先安装）")
    p.set_defaults(func=_data)

    p = sub.add_parser("backtest", help="跑回测流水线")
    p.add_argument("--top-n", type=int, default=None)
    p.add_argument("--no-timing", action="store_true")
    p.add_argument("--no-ablation", action="store_true")
    p.set_defaults(func=_backtest)

    p = sub.add_parser("web", help="启动 Web 看板")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=_web)

    p = sub.add_parser("report", help="生成 Word 技术报告")
    p.add_argument("--out", default=None)
    p.set_defaults(func=_report)

    p = sub.add_parser("info", help="查看数据与产物概况")
    p.set_defaults(func=_info)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
