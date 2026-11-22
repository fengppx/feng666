"""因子库。

每个因子都是「时间序列算子 + 横截面变换」，输入为行情面板，
输出为 ``DataFrame(index=交易日, columns=股票)`` 的因子值矩阵。

严格遵循"只用当日及之前信息"的原则：所有 rolling / shift 都是向后的，
不使用任何未来数据（回测引擎另有单元测试验证无前视）。
"""

from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

from qlab.config import FACTOR_POOL
from qlab.data.loader import MarketPanel
from qlab.logging_utils import get_logger

log = get_logger("qlab.factors")

ANN = 252


# ----------------------------------------------------------------------
# 原子因子
# ----------------------------------------------------------------------
def _mom(close: pd.DataFrame, lookback: int, skip: int) -> pd.DataFrame:
    """动量：(P_{t-skip} / P_{t-skip-lookback}) - 1。"""
    end = close.shift(skip)
    start = close.shift(skip + lookback)
    return end / start - 1.0


def _vol(close: pd.DataFrame, lookback: int) -> pd.DataFrame:
    """已实现波动率（年化）。"""
    ret = close.pct_change(fill_method=None)
    return ret.rolling(lookback, min_periods=max(5, lookback // 2)).std() * np.sqrt(ANN)


def _reversal(close: pd.DataFrame, lookback: int) -> pd.DataFrame:
    return close / close.shift(lookback) - 1.0


def _max_ret(close: pd.DataFrame, lookback: int) -> pd.DataFrame:
    ret = close.pct_change(fill_method=None)
    return ret.rolling(lookback, min_periods=lookback // 2).max()


def _turnover(volume: pd.DataFrame, lookback: int) -> pd.DataFrame:
    """成交拥挤度：近 lookback 日平均成交量 / 近 250 日平均成交量。"""
    short = volume.rolling(lookback, min_periods=lookback // 2).mean()
    long = volume.rolling(250, min_periods=120).mean()
    return short / long


def _bias(close: pd.DataFrame, lookback: int) -> pd.DataFrame:
    ma = close.rolling(lookback, min_periods=lookback // 2).mean()
    return close / ma - 1.0


def _rsi(close: pd.DataFrame, lookback: int) -> pd.DataFrame:
    delta = close.diff()
    up = delta.clip(lower=0).rolling(lookback, min_periods=lookback // 2).mean()
    down = (-delta.clip(upper=0)).rolling(lookback, min_periods=lookback // 2).mean()
    rs = up / down.replace(0.0, np.nan)
    return 100.0 - 100.0 / (1.0 + rs)


def _hl_range(high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame,
              lookback: int) -> pd.DataFrame:
    rng = (high - low) / close
    return rng.rolling(lookback, min_periods=lookback // 2).mean()


def _skew(close: pd.DataFrame, lookback: int) -> pd.DataFrame:
    ret = close.pct_change(fill_method=None)
    return ret.rolling(lookback, min_periods=lookback // 2).skew()


# ----------------------------------------------------------------------
# 注册表：因子名 -> 计算函数(panel, params) -> DataFrame
# ----------------------------------------------------------------------
FACTOR_FUNCS: dict[str, Callable[[MarketPanel, dict], pd.DataFrame]] = {
    "mom_12_1": lambda p, a: _mom(p.close, a["lookback"], a["skip"]),
    "mom_6_1": lambda p, a: _mom(p.close, a["lookback"], a["skip"]),
    "mom_1": lambda p, a: _mom(p.close, a["lookback"], a["skip"]),
    "rev_5": lambda p, a: _reversal(p.close, a["lookback"]),
    "vol_20": lambda p, a: _vol(p.close, a["lookback"]),
    "vol_60": lambda p, a: _vol(p.close, a["lookback"]),
    "max_ret_20": lambda p, a: _max_ret(p.close, a["lookback"]),
    "turn_20": lambda p, a: _turnover(p.volume, a["lookback"]),
    "bias_60": lambda p, a: _bias(p.close, a["lookback"]),
    "rsi_14": lambda p, a: _rsi(p.close, a["lookback"]),
    "hl_range_20": lambda p, a: _hl_range(p.high, p.low, p.close, a["lookback"]),
    "skew_60": lambda p, a: _skew(p.close, a["lookback"]),
}


def compute_factor(panel: MarketPanel, name: str) -> pd.DataFrame:
    """计算单个因子。"""
    if name not in FACTOR_FUNCS:
        raise KeyError(f"未注册的因子: {name}（可选：{sorted(FACTOR_FUNCS)}）")
    params = FACTOR_POOL[name]
    out = FACTOR_FUNCS[name](panel, params)
    return out.replace([np.inf, -np.inf], np.nan)


def compute_factors(
    panel: MarketPanel, names: list[str] | None = None
) -> dict[str, pd.DataFrame]:
    """批量计算因子。"""
    names = names or list(FACTOR_FUNCS)
    result: dict[str, pd.DataFrame] = {}
    for name in names:
        result[name] = compute_factor(panel, name)
        log.debug("因子 %s 计算完成，覆盖率 %.1f%%",
                  name, 100.0 * result[name].notna().mean().mean())
    return result
