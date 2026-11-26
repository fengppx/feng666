"""一键抓取行情数据并落盘缓存。

用法（在项目根目录）：
    python scripts/download_data.py                 # 增量抓取（已有缓存跳过）
    python scripts/download_data.py --force         # 全量重抓
    python scripts/download_data.py --size 200      # 股票池改小，快速试跑
    python scripts/download_data.py --rounds 3     # 多轮补抓（应对接口限流）

接口对高频访问会返回 501，因此默认单线程 + 0.45s 间隔；
多轮模式会反复补齐"行数不足 / 起始日过晚"的标的，直到收敛。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qlab.config import DataConfig  # noqa: E402
from qlab.data import DataPortal, cache  # noqa: E402
from qlab.logging_utils import get_logger  # noqa: E402

log = get_logger("qlab.download")


def main() -> int:
    ap = argparse.ArgumentParser(description="抓取 A 股日线数据到本地缓存")
    ap.add_argument("--size", type=int, default=300, help="股票池规模（按市值前 N）")
    ap.add_argument("--start", default="2017-06-01", help="起始日期")
    ap.add_argument("--end", default=None, help="结束日期，默认最近交易日")
    ap.add_argument("--force", action="store_true", help="忽略缓存全量重抓")
    ap.add_argument("--workers", type=int, default=1, help="并发线程数（接口限流，勿大）")
    ap.add_argument("--pause", type=float, default=0.45, help="每次请求后的间隔（秒）")
    ap.add_argument("--rounds", type=int, default=3, help="补抓轮数")
    ap.add_argument("--provider", default="akshare",
                    help="数据源：akshare（默认）/ baostock / tencent / sina")
    args = ap.parse_args()

    cfg = DataConfig(
        universe_size=args.size,
        fetch_start=args.start,
        fetch_end=args.end,
        request_workers=args.workers,
        request_pause=args.pause,
    )
    portal = DataPortal(cfg, provider=args.provider)

    t0 = time.time()
    uni = portal.ensure_universe(force=args.force)
    log.info("股票池：%d 只，市值前五：%s", len(uni),
             ", ".join(f"{r['name']}({r['symbol']})" for _, r in uni.head(5).iterrows()))

    min_rows = 1500          # 约 6 年交易日；低于此值说明只抓到了尾部窗口
    start_before = "2017-09-01"
    for rnd in range(1, args.rounds + 1):
        log.info("=== 第 %d/%d 轮抓取 ===", rnd, args.rounds)
        stats = portal.download(
            force=args.force and rnd == 1,
            min_rows=min_rows,
            start_before=start_before,
        )
        portal.download_benchmarks()
        if not stats:
            log.info("无待补抓标的，提前结束")
            break
        if rnd < args.rounds:
            wait = 20
            log.info("等待 %ds 后进入下一轮（缓解接口限流）", wait)
            time.sleep(wait)

    manifest = cache.read_manifest()
    ok = len(manifest.get("symbols", {}))
    log.info("缓存清单：%d 个标的，总耗时 %.0fs", ok, time.time() - t0)
    return 0 if ok >= args.size * 0.8 else 1


if __name__ == "__main__":
    raise SystemExit(main())
