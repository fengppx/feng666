"""择时模块：用宽基指数的趋势状态控制总仓位。

逻辑：指数收盘价在 N 日均线上方 → 满仓；下方 → 降仓（默认 30%）。
这属于课程中的「择时」方法：用单一趋势过滤器降低系统性回撤，
而不是预测涨跌。参数（N、低仓位比例）在样本内确定后即冻结。
"""

from __future__ import annotations

import pandas as pd


def trend_exposure(
    benchmark: pd.Series,
    ma_window: int = 120,
    low: float = 0.30,
    high: float = 1.00,
) -> pd.Series:
    """返回逐日仓位系数（0~1）。

    信号只用当日及之前的收盘价，调仓发生在下一交易日开盘，
    因此不产生前视偏差。
    """
    ma = benchmark.rolling(ma_window, min_periods=max(20, ma_window // 2)).mean()
    above = benchmark > ma
    exposure = pd.Series(high, index=benchmark.index, dtype=float)
    exposure[~above] = low
    exposure[ma.isna()] = low  # 预热期无均线，保守降仓
    return exposure


def exposure_stats(exposure: pd.Series) -> dict:
    """仓位状态统计，用于报告「满仓/降仓」时间占比。"""
    full = float((exposure >= 0.99).mean())
    return {
        "full_position_ratio": full,
        "reduced_ratio": float(1.0 - full),
        "avg_exposure": float(exposure.mean()),
        "n_switches": int((exposure.diff().abs() > 1e-9).sum()),
    }
