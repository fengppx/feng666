"""绩效指标体系。

全部基于**日频净值**计算，年化统一按 252 个交易日：
    · 累计收益率  total_return
    · 年化收益率  cagr
    · 最大回撤    max_drawdown
    · 夏普比率    sharpe（超额收益 / 年化波动，无风险利率可配）
另附索提诺、卡玛、信息比率、跟踪误差、Beta/Alpha、上行/下行捕获等。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

ANN = 252


# ----------------------------------------------------------------------
# 基础工具
# ----------------------------------------------------------------------
def drawdown_series(nav: pd.Series) -> pd.Series:
    """回撤序列（≤0）。"""
    return nav / nav.cummax() - 1.0


def max_drawdown(nav: pd.Series) -> tuple[float, pd.Timestamp | None, pd.Timestamp | None]:
    """最大回撤及其区间（峰、谷日期）。"""
    dd = drawdown_series(nav)
    trough = dd.idxmin()
    mdd = float(dd.loc[trough])
    peak = nav.loc[:trough].idxmax() if len(nav.loc[:trough]) else None
    return mdd, peak, trough


def _cagr(nav: pd.Series, periods: int = ANN) -> float:
    n = len(nav)
    if n < 2:
        return np.nan
    total = nav.iloc[-1] / nav.iloc[0]
    if total <= 0:
        return np.nan
    return float(total ** (periods / (n - 1)) - 1.0)


def _ann_vol(returns: pd.Series, periods: int = ANN) -> float:
    return float(returns.std(ddof=1) * np.sqrt(periods)) if len(returns) > 2 else np.nan


def _downside_vol(returns: pd.Series, target: float = 0.0, periods: int = ANN) -> float:
    downside = returns[returns < target] - target
    if len(downside) < 2:
        return np.nan
    return float(np.sqrt((downside**2).mean()) * np.sqrt(periods))


# ----------------------------------------------------------------------
# 主函数
# ----------------------------------------------------------------------
def perf_metrics(
    nav: pd.Series,
    benchmark: pd.Series | None = None,
    rf: float = 0.015,
    periods: int = ANN,
    trades: pd.DataFrame | None = None,
    avg_equity: float | None = None,
) -> dict:
    """计算一组完整绩效指标。"""
    nav = nav.dropna()
    if len(nav) < 3:
        return {}
    rets = nav.pct_change(fill_method=None).dropna()
    n = len(rets)
    years = n / periods

    total_return = float(nav.iloc[-1] / nav.iloc[0] - 1.0)
    cagr = _cagr(nav, periods)
    vol = _ann_vol(rets, periods)
    rf_daily = (1.0 + rf) ** (1.0 / periods) - 1.0
    excess_daily = rets - rf_daily
    sharpe = float(excess_daily.mean() / rets.std(ddof=1) * np.sqrt(periods)) if rets.std(ddof=1) > 0 else np.nan
    downside = _downside_vol(rets, rf_daily, periods)
    sortino = float(excess_daily.mean() * periods / downside) if downside and downside > 0 else np.nan
    mdd, peak, trough = max_drawdown(nav)
    calmar = float(cagr / abs(mdd)) if mdd < 0 else np.nan

    # 月度收益
    monthly = nav.resample("ME").last().pct_change(fill_method=None).dropna()
    out = {
        "累计收益率": total_return,
        "年化收益率": cagr,
        "年化波动率": vol,
        "最大回撤": mdd,
        "夏普比率": sharpe,
        "索提诺比率": sortino,
        "卡玛比率": calmar,
        "日胜率": float((rets > 0).mean()),
        "月胜率": float((monthly > 0).mean()) if len(monthly) else np.nan,
        "日收益偏度": float(rets.skew()),
        "日收益峰度": float(rets.kurt()),
        "最好单日": float(rets.max()),
        "最差单日": float(rets.min()),
        "VaR95(日)": float(rets.quantile(0.05)),
        "CVaR95(日)": float(rets[rets <= rets.quantile(0.05)].mean()) if (rets <= rets.quantile(0.05)).any() else np.nan,
        "年化换手率": np.nan,
        "回撤持续天数": np.nan,
        "最大回撤开始": str(peak.date()) if peak is not None else "",
        "最大回撤结束": str(trough.date()) if trough is not None else "",
        "交易日数": n,
        "年数": years,
    }

    # 回撤持续（峰到谷的日历天数）
    if peak is not None and trough is not None:
        out["回撤持续天数"] = float((trough - peak).days)

    # 换手率：双边成交额 / 平均组合市值 / 年数
    if trades is not None and not trades.empty:
        traded = float(trades["notional"].sum())
        base = float(avg_equity) if avg_equity else 1.0
        out["年化换手率"] = traded / base / years if years > 0 else np.nan
        out["累计成交额"] = traded

    # 相对基准
    if benchmark is not None and len(benchmark.dropna()) > 3:
        bench = benchmark.reindex(nav.index).ffill().dropna()
        if len(bench) > 3:
            b_rets = bench.pct_change(fill_method=None).dropna()
            b_total = float(bench.iloc[-1] / bench.iloc[0] - 1.0)
            b_cagr = _cagr(bench, periods)
            b_vol = _ann_vol(b_rets, periods)
            b_mdd, _, _ = max_drawdown(bench)
            b_sharpe = float((b_rets - rf_daily).mean() / b_rets.std(ddof=1) * np.sqrt(periods)) if b_rets.std(ddof=1) > 0 else np.nan
            active = rets - b_rets.reindex(rets.index).fillna(0.0)
            te = float(active.std(ddof=1) * np.sqrt(periods)) if len(active) > 2 else np.nan
            beta = float(np.cov(rets, b_rets.reindex(rets.index).fillna(0.0))[0, 1] / np.var(b_rets.reindex(rets.index).fillna(0.0), ddof=1)) if b_rets.var(ddof=1) > 0 else np.nan
            alpha = float((cagr - rf) - beta * (b_cagr - rf)) if not (np.isnan(beta) or np.isnan(b_cagr)) else np.nan
            up = rets[b_rets.reindex(rets.index) > 0]
            down = rets[b_rets.reindex(rets.index) < 0]
            b_up_mean = b_rets[b_rets > 0].mean()
            b_down_mean = b_rets[b_rets < 0].mean()
            out.update({
                "基准累计收益率": b_total,
                "基准年化收益率": b_cagr,
                "基准年化波动率": b_vol,
                "基准最大回撤": b_mdd,
                "基准夏普比率": b_sharpe,
                "超额收益(累计)": total_return - b_total,
                "超额收益(年化)": cagr - b_cagr if not np.isnan(cagr) and not np.isnan(b_cagr) else np.nan,
                "跟踪误差": te,
                "信息比率": float(active.mean() * periods / te) if te and te > 0 else np.nan,
                "Beta": beta,
                "Alpha(年化)": alpha,
                "相关系数": float(rets.corr(b_rets.reindex(rets.index).fillna(0.0))),
                "上行捕获": float(up.mean() / b_up_mean) if len(up) and b_up_mean and not np.isnan(b_up_mean) and b_up_mean != 0 else np.nan,
                "下行捕获": float(down.mean() / b_down_mean) if len(down) and b_down_mean and not np.isnan(b_down_mean) and b_down_mean != 0 else np.nan,
            })
    return out


# ----------------------------------------------------------------------
# 表格化输出
# ----------------------------------------------------------------------
@dataclass
class SegmentSpec:
    label: str
    start: str | None
    end: str | None


DEFAULT_SEGMENTS = [
    SegmentSpec("全样本", None, None),
    SegmentSpec("样本内", "2019-01-01", "2022-12-31"),
    SegmentSpec("样本外", "2023-01-01", None),
]

# 报告主表展示的指标（顺序即表格顺序）
KEY_METRICS = [
    "累计收益率", "年化收益率", "年化波动率", "最大回撤", "夏普比率",
    "索提诺比率", "卡玛比率", "月胜率", "年化换手率",
]


def segment_table(result, rf: float = 0.015, segments=None) -> pd.DataFrame:
    """策略 vs 基准：全样本 / 样本内 / 样本外 指标对比。"""
    segments = segments or DEFAULT_SEGMENTS
    rows = {}
    for seg in segments:
        try:
            sub = result.segment(seg.start, seg.end)
        except ValueError:
            continue
        m = perf_metrics(
            sub.nav, sub.benchmark_nav, rf=rf, trades=sub.trades,
            avg_equity=float(sub.equity.mean()),
        )
        rows[seg.label] = m
    if not rows:
        return pd.DataFrame()
    table = pd.DataFrame(rows)

    # 汇总成「指标 | 策略值 | 基准值」的对比表
    records = []
    for metric in KEY_METRICS + ["超额收益(年化)", "信息比率", "Beta", "上行捕获", "下行捕获"]:
        rec = {"指标": metric}
        for label in table.columns:
            rec[label] = table.loc[metric, label] if metric in table.index else np.nan
        records.append(rec)
    return pd.DataFrame(records)
