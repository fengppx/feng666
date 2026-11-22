"""横截面预处理。

多因子模型的标准流程：
    原始因子 → 去极值（winsorize） → 标准化（z-score / rank） → 合成打分
所有操作都是「逐日横截面」的，不允许跨日期使用统计量。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def winsorize_cs(df: pd.DataFrame, quantile: float = 0.02) -> pd.DataFrame:
    """逐日按分位数截断极值（双侧各 quantile）。"""
    if quantile <= 0:
        return df
    lower = df.quantile(quantile, axis=1)
    upper = df.quantile(1.0 - quantile, axis=1)
    return df.clip(lower=lower, upper=upper, axis=0)


def zscore_cs(df: pd.DataFrame, clip: float = 3.0) -> pd.DataFrame:
    """逐日 z-score 标准化，并截断到 ±clip。"""
    mu = df.mean(axis=1)
    sd = df.std(axis=1, ddof=0).replace(0.0, np.nan)
    z = df.sub(mu, axis=0).div(sd, axis=0)
    return z.clip(-clip, clip)


def rank_cs(df: pd.DataFrame) -> pd.DataFrame:
    """逐日截面百分位排名，映射到 [-0.5, 0.5]，对异常值更稳健。"""
    return df.rank(axis=1, pct=True) - 0.5


def neutralize_size(factor: pd.DataFrame, size: pd.DataFrame) -> pd.DataFrame:
    """对市值（对数成交额代理）做横截面回归取残差，剔除规模暴露。

    可选步骤：本项目股票池已是清一色大市值标的，规模暴露天然较弱，
    故默认关闭，保留接口以便扩展到全市场时使用。
    """
    res = factor.copy()
    for date in factor.index:
        y = factor.loc[date]
        x = size.loc[date]
        mask = y.notna() & x.notna()
        if mask.sum() < 10:
            continue
        xv = x[mask].to_numpy(dtype=float)
        yv = y[mask].to_numpy(dtype=float)
        xv = (xv - xv.mean()) / (xv.std() or 1.0)
        beta = float(np.dot(xv - xv.mean(), yv - yv.mean()) / (np.dot(xv - xv.mean(), xv - xv.mean()) or 1.0))
        alpha = yv.mean() - beta * xv.mean()
        res.loc[date, mask] = yv - (alpha + beta * xv)
    return res
