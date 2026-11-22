"""因子有效性分析：IC / ICIR / 分层收益。

IC（信息系数）= 当期因子值与下一期收益的横截面 Spearman 秩相关。
由于环境里没有 scipy，秩相关用「先截面取排名，再逐日求 Pearson」实现，
与 ``scipy.stats.spearmanr`` 数学等价。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from qlab.config import FACTOR_POOL
from qlab.factors.library import compute_factors
from qlab.factors.preprocessing import winsorize_cs, zscore_cs
from qlab.logging_utils import get_logger

log = get_logger("qlab.factors.ic")

ANN = 252


def forward_returns(close: pd.DataFrame, horizon: int = 21) -> pd.DataFrame:
    """未来 horizon 个交易日的收益（t → t+h），仅用于因子评价，不进入交易信号。"""
    return close.shift(-horizon) / close - 1.0


def _row_rank_corr(a: pd.DataFrame, b: pd.DataFrame, min_obs: int = 10) -> pd.Series:
    """逐日横截面秩相关。"""
    ra = a.rank(axis=1)
    rb = b.rank(axis=1)
    n = ra.notna() & rb.notna()
    out = pd.Series(np.nan, index=a.index, dtype=float)
    for date in a.index:
        mask = n.loc[date]
        if mask.sum() < min_obs:
            continue
        x = ra.loc[date, mask]
        y = rb.loc[date, mask]
        sx, sy = x.std(ddof=0), y.std(ddof=0)
        if sx == 0 or sy == 0 or np.isnan(sx) or np.isnan(sy):
            continue
        out.loc[date] = float(((x - x.mean()) * (y - y.mean())).mean() / (sx * sy))
    return out


def ic_series(
    factor: pd.DataFrame,
    fwd: pd.DataFrame,
    dates: pd.DatetimeIndex | None = None,
) -> pd.Series:
    """计算因子在指定日期上的 IC 序列。"""
    if dates is not None:
        dates = pd.DatetimeIndex(dates).intersection(factor.index).intersection(fwd.index)
        factor = factor.loc[dates]
        fwd = fwd.loc[dates]
    return _row_rank_corr(factor, fwd)


@dataclass
class ICSummary:
    """单因子 IC 统计量。"""

    name: str
    mean_ic: float
    std_ic: float
    icir: float
    t_stat: float
    pos_ratio: float
    n_periods: int

    def as_dict(self) -> dict:
        return {
            "factor": self.name,
            "mean_ic": self.mean_ic,
            "std_ic": self.std_ic,
            "icir": self.icir,
            "t_stat": self.t_stat,
            "pos_ratio": self.pos_ratio,
            "n_periods": self.n_periods,
        }


def ic_summary(name: str, ic: pd.Series) -> ICSummary:
    """汇总 IC 序列：均值、标准差、ICIR、t 值、正比例。"""
    s = ic.dropna()
    n = len(s)
    if n == 0:
        return ICSummary(name, np.nan, np.nan, np.nan, np.nan, np.nan, 0)
    mean = float(s.mean())
    std = float(s.std(ddof=1)) if n > 1 else np.nan
    icir = mean / std if std and not np.isnan(std) else np.nan
    t_stat = icir * np.sqrt(n) if icir and not np.isnan(icir) else np.nan
    return ICSummary(
        name=name,
        mean_ic=mean,
        std_ic=std,
        icir=icir,
        t_stat=t_stat,
        pos_ratio=float((s > 0).mean()),
        n_periods=n,
    )


def quantile_returns(
    factor: pd.DataFrame,
    fwd: pd.DataFrame,
    n_quantiles: int = 5,
    dates: pd.DatetimeIndex | None = None,
) -> pd.DataFrame:
    """分层收益：每日按因子值分 N 组，等权持有并统计各组的平均前瞻收益。"""
    if dates is not None:
        dates = pd.DatetimeIndex(dates).intersection(factor.index).intersection(fwd.index)
    else:
        dates = factor.index.intersection(fwd.index)
    rows = {}
    for date in dates:
        f = factor.loc[date]
        r = fwd.loc[date]
        mask = f.notna() & r.notna()
        if mask.sum() < n_quantiles * 3:
            continue
        fv, rv = f[mask], r[mask]
        try:
            labels = pd.qcut(fv.rank(method="first"), n_quantiles, labels=False)
        except ValueError:
            continue
        for q in range(n_quantiles):
            rows.setdefault(q, []).append(float(rv[labels == q].mean()))
    out = {f"Q{q + 1}": pd.Series(v) for q, v in rows.items()}
    return pd.DataFrame(out)


def evaluate_factors(
    panel,
    dates: pd.DatetimeIndex,
    horizon: int = 21,
    pool: list[str] | None = None,
    factors: dict[str, pd.DataFrame] | None = None,
) -> tuple[pd.DataFrame, dict[str, pd.Series]]:
    """对候选因子池做统一评测。

    返回
    ----
    table : DataFrame  每个因子的 IC / ICIR / t 值等（按 |ICIR| 降序）
    ics   : dict       因子名 -> IC 序列
    """
    names = pool or list(FACTOR_POOL)
    factors = factors or compute_factors(panel, names)
    fwd = forward_returns(panel.close, horizon)
    summaries, ics = [], {}
    for name in names:
        raw = factors[name]
        fac = zscore_cs(winsorize_cs(raw, 0.02))
        ic = ic_series(fac, fwd, dates)
        ics[name] = ic
        summaries.append(ic_summary(name, ic).as_dict())
    table = pd.DataFrame(summaries).sort_values("icir", key=lambda s: s.abs(), ascending=False)
    table = table.reset_index(drop=True)
    table["direction"] = table["factor"].map(lambda n: FACTOR_POOL[n]["direction"])
    table["desc"] = table["factor"].map(lambda n: FACTOR_POOL[n]["desc"])
    return table, ics


def select_factors(
    table: pd.DataFrame,
    k: int = 5,
    min_abs_icir: float = 0.15,
) -> list[str]:
    """按样本内 |ICIR| 选出前 k 个因子（并剔除明显无效者）。"""
    valid = table[table["icir"].abs() >= min_abs_icir]
    chosen = valid.head(k)["factor"].tolist()
    if len(chosen) < 2:  # 保护：至少保留两个因子，避免单因子偶然性
        chosen = table.head(max(2, k))["factor"].tolist()
    log.info("样本内因子筛选：%s", ", ".join(chosen))
    return chosen


def pooled_factor_corr(
    factors: dict[str, pd.DataFrame],
    dates: pd.DatetimeIndex,
) -> pd.DataFrame:
    """因子间「混合截面秩相关」矩阵，用于剔除高度共线的重复因子。

    做法：在每个采样日把各因子做截面排名，再把所有日期的排名向量首尾拼接，
    计算因子两两之间的 Pearson 相关。相比逐日相关再平均，这样更稳健。
    """
    stacked: dict[str, list[np.ndarray]] = {name: [] for name in factors}
    for date in dates:
        for name, frame in factors.items():
            if date not in frame.index:
                stacked[name].append(np.full(0, np.nan))
                continue
            row = frame.loc[date].rank(pct=True)
            stacked[name].append(row.to_numpy(dtype=float))
    data = {}
    for name, chunks in stacked.items():
        if not chunks:
            continue
        data[name] = np.concatenate(chunks) if chunks else np.array([])
    matrix = pd.DataFrame(data)
    return matrix.corr(method="pearson")
