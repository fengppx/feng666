"""检查各行情数据源是否可用。

对每只标的取一小段日线，报告行数、最新收盘价，并与本地缓存比对。
默认只测已安装的数据源；缺依赖的会自动跳过。

用法（项目根目录）：

    python scripts/check_sources.py                  # 测全部
    python scripts/check_sources.py --providers baostock akshare
    python scripts/check_sources.py --start 2024-01-02 --end 2024-01-31

课程文件列出的四个源里，tushare 需要 token、yfinance 覆盖美股，
本项目实际使用 akshare（主源）与 baostock（校验源），腾讯/新浪保留为历史兼容与回退。
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qlab.data import cache  # noqa: E402
from qlab.data.providers import get_provider  # noqa: E402

# 数据源 -> 依赖包名（None 表示内置，无需额外安装）
PROVIDERS = {
    "tencent": None,
    "sina": None,
    "akshare": "akshare",
    "baostock": "baostock",
    "yfinance": "yfinance",
}

STOCK = "sh600519"      # 贵州茅台
INDEX = "sh000300"      # 沪深 300


def installed(pkg: str | None) -> bool:
    return pkg is None or importlib.util.find_spec(pkg) is not None


def check(provider_name: str, symbol: str, start: str, end: str) -> None:
    label = "指数" if symbol.startswith(("sh000", "sz399")) else "个股"
    try:
        provider = get_provider(provider_name, adjust="day", pause=0.0, retry=1)
        df = provider.fetch_daily(symbol, start, end)
    except Exception as exc:
        print(f"  {provider_name:<8} {symbol} {label}  ✗ {type(exc).__name__}: {exc}")
        return

    if df is None or df.empty:
        print(f"  {provider_name:<8} {symbol} {label}  ✗ 空数据")
        return

    last_close = float(df["close"].iloc[-1])
    line = (f"  {provider_name:<8} {symbol} {label}  ✓ {len(df):>3} 行  "
            f"{df.index[0]:%Y-%m-%d} ~ {df.index[-1]:%Y-%m-%d}  收盘 {last_close:.2f}")

    cached = cache.load_prices(symbol)
    if cached is not None and not cached.empty:
        base = cached.loc[cached.index <= df.index[-1]]
        if not base.empty and base.index[-1] == df.index[-1]:
            diff = abs(float(base["close"].iloc[-1]) - last_close)
            line += f"  (本地缓存 {float(base['close'].iloc[-1]):.2f}，差 {diff:.2f})"
    print(line)


def main() -> int:
    ap = argparse.ArgumentParser(description="检查行情数据源可用性")
    ap.add_argument("--providers", nargs="*", default=list(PROVIDERS),
                    help="要检查的数据源，默认全部")
    ap.add_argument("--start", default="2024-01-02")
    ap.add_argument("--end", default="2024-01-31")
    args = ap.parse_args()

    print(f"检查区间 {args.start} ~ {args.end}\n")
    ok = 0
    for name in args.providers:
        if name not in PROVIDERS:
            print(f"  未知数据源：{name}")
            continue
        pkg = PROVIDERS[name]
        if not installed(pkg):
            print(f"  {name:<8} 未安装（pip install {pkg}），跳过")
            continue
        for symbol in (STOCK, INDEX):
            check(name, symbol, args.start, args.end)
        ok += 1

    print(f"\n可用的数据源：{ok} 个")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
