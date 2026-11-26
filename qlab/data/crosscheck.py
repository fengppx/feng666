"""多源数据交叉校验。

系统以 akshare 为主源，baostock 作为独立校验源：抽样取若干标的，把本地缓存
（主源抓下来的）与校验源逐日比对收盘价，量化两者是否一致。

结果写入 ``output/tables/source_check.csv``，一条命令即可在答辩时演示：

    python -m qlab check --sample 15
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from qlab.config import TABLE_DIR
from qlab.data import cache
from qlab.data.providers import get_provider
from qlab.logging_utils import get_logger

log = get_logger("qlab.crosscheck")


def sample_symbols(symbols: list[str], n: int) -> list[str]:
    """从股票池中均匀抽样，保证可复现。"""
    if n >= len(symbols):
        return list(symbols)
    step = len(symbols) / n
    idx = [int(i * step) for i in range(n)]
    return [symbols[i] for i in idx]


def compare(
    symbols: list[str] | None = None,
    n: int = 15,
    provider: str = "baostock",
    tol: float = 0.001,
    save: bool = True,
) -> tuple[pd.DataFrame, dict]:
    """抽样比对本地缓存与指定数据源的收盘价。

    参数
    ----
    symbols : 指定标的；默认取缓存清单里全部标的
    n       : 抽样数量
    provider: 校验源（默认 baostock）
    tol     : 判定一致的最大相对偏差（默认 0.1%）

    返回 ``(明细表, 汇总)``。
    """
    manifest = cache.read_manifest()
    universe = symbols or sorted(manifest.get("symbols", {}))
    picks = sample_symbols(universe, n)
    check = get_provider(provider, adjust="day", pause=0.0, retry=1)

    rows = []
    for sym in picks:
        local = cache.load_prices(sym)
        if local is None or local.empty:
            rows.append({"symbol": sym, "common_days": 0, "max_rel_diff": np.nan,
                         "status": "无本地缓存"})
            continue
        start = str(local.index.min().date())
        end = str(local.index.max().date())
        try:
            remote = check.fetch_daily(sym, start, end)
        except Exception as exc:
            log.warning("校验源取数失败 %s：%s", sym, exc)
            rows.append({"symbol": sym, "common_days": 0, "max_rel_diff": np.nan,
                         "status": f"取数失败：{type(exc).__name__}"})
            continue

        both = pd.concat(
            [local["close"].rename("local"), remote["close"].rename("remote")],
            axis=1, join="inner",
        ).dropna()
        if both.empty:
            rows.append({"symbol": sym, "common_days": 0, "max_rel_diff": np.nan,
                         "status": "无重叠交易日"})
            continue

        rel = (both["local"] - both["remote"]).abs() / both["remote"].replace(0, np.nan)
        abs_diff = (both["local"] - both["remote"]).abs()
        max_rel = float(rel.max())
        max_abs = float(abs_diff.max())
        # 低价股一分钱的取整差异会放大成 0.1% 以上，因此同时看绝对差
        consistent = (max_rel <= tol) or (max_abs <= 0.011)
        rows.append({
            "symbol": sym,
            "common_days": int(len(both)),
            "max_rel_diff": max_rel,
            "max_abs_diff": max_abs,
            "first_date": str(both.index[0].date()),
            "last_date": str(both.index[-1].date()),
            "status": "一致" if consistent else "偏差超阈值",
        })

    table = pd.DataFrame(rows)
    ok = int((table["status"] == "一致").sum())
    valid = table.dropna(subset=["max_rel_diff"])
    summary = {
        "provider": provider,
        "checked": len(table),
        "consistent": ok,
        "failed": len(table) - ok,
        "max_rel_diff": float(valid["max_rel_diff"].max()) if len(valid) else float("nan"),
        "max_abs_diff": float(valid["max_abs_diff"].max()) if len(valid) else float("nan"),
        "median_rel_diff": float(valid["max_rel_diff"].median()) if len(valid) else float("nan"),
        "tol": tol,
    }

    if save and not table.empty:
        TABLE_DIR.mkdir(parents=True, exist_ok=True)
        table.to_csv(TABLE_DIR / "source_check.csv", index=False, encoding="utf-8-sig")
        log.info("交叉校验结果写入 %s", TABLE_DIR / "source_check.csv")

    log.info("交叉校验（%s）：抽查 %d 只，一致 %d 只，最大相对偏差 %.4f%%",
             provider, len(table), ok, summary["max_rel_diff"] * 100)
    return table, summary
