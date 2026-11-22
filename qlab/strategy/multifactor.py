"""多因子选股 + 趋势择时策略。

流程（全部只用当日及之前数据）：
    1. 样本内（2019-2022）用月度采样计算候选因子 IC/ICIR             [fit]
    2. 按 |ICIR| 选前 k 个因子，权重 ∝ |ICIR|（或等权），方向取 IC 符号  [fit]
    3. 每个调仓日：因子去极值 → z-score → 加权合成综合得分            [transform]
    4. 取综合得分前 top_n 只等权持有，再乘以择时仓位系数               [transform]
    5. 目标权重交给回测引擎，t+1 开盘撮合

“参数只在样本内确定”是本策略防过拟合的核心约束，
样本外区间（2023 起）完全不参与任何选择。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from qlab.config import FACTOR_POOL, BacktestConfig, StrategyConfig
from qlab.data.loader import MarketPanel
from qlab.factors.analysis import evaluate_factors, pooled_factor_corr
from qlab.factors.library import compute_factors
from qlab.factors.preprocessing import winsorize_cs, zscore_cs
from qlab.logging_utils import get_logger
from qlab.strategy.timing import trend_exposure

log = get_logger("qlab.strategy")


def monthly_rebalance_dates(index: pd.DatetimeIndex) -> list[pd.Timestamp]:
    """每月最后一个交易日。"""
    s = pd.Series(index, index=index)
    grouped = s.groupby([index.year, index.month]).max()
    return [pd.Timestamp(d) for d in sorted(grouped.to_numpy())]


@dataclass
class FactorSpec:
    """入选因子的方向与权重。"""

    name: str
    ic_mean: float
    icir: float
    sign: int
    weight: float

    def as_dict(self) -> dict:
        return {
            "factor": self.name,
            "ic_mean": round(self.ic_mean, 4),
            "icir": round(self.icir, 3),
            "sign": self.sign,
            "weight": round(self.weight, 3),
            "desc": FACTOR_POOL[self.name]["desc"],
        }


@dataclass
class MultiFactorStrategy:
    """多因子打分策略。"""

    cfg: StrategyConfig = field(default_factory=StrategyConfig)
    bt_cfg: BacktestConfig = field(default_factory=BacktestConfig)
    # fit 之后填充
    specs: list[FactorSpec] = field(default_factory=list)
    ic_table: pd.DataFrame = field(default_factory=pd.DataFrame)
    diagnostics: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    def rebalance_dates(self, index: pd.DatetimeIndex,
                        start: str | None = None) -> list[pd.Timestamp]:
        """调仓日 = 每月最后一个交易日；首个交易日额外建仓一次。"""
        dates = monthly_rebalance_dates(index)
        if start is not None:
            first = index[index >= pd.Timestamp(start)]
            dates = [d for d in dates if d >= pd.Timestamp(start)]
            if len(first) and (not dates or dates[0] > first[0]):
                dates = [first[0]] + dates
        elif len(index) and (not dates or dates[0] > index[0]):
            dates = [index[0]] + dates
        return [d for d in dates if d in set(index)]

    # ------------------------------------------------------------------
    def fit(self, panel_is: MarketPanel) -> "MultiFactorStrategy":
        """在**样本内**数据上挑选因子、确定方向与权重。

        两步筛选：
            1. 按 |ICIR| 降序，剔除 |ICIR| < min_abs_icir 的弱因子；
            2. 贪心加入因子，若与已选因子截面相关性 >= max_factor_corr
               则跳过（避免 vol_60 与 hl_range_20 这类同质因子重复计权重）。
        """
        # 用 2023-01-01 之前的全部可得数据估计 IC（含 2017-06 起的因子预热段），
        # 样本外数据完全不参与；panel_is 已在调用方按 is_end 切好。
        reb_dates = pd.DatetimeIndex(self.rebalance_dates(panel_is.dates))
        pool = list(FACTOR_POOL)
        factors = compute_factors(panel_is, pool)
        table, ics = evaluate_factors(
            panel_is, reb_dates, horizon=21, pool=pool, factors=factors
        )
        self.ic_table = table
        corr = pooled_factor_corr(factors, reb_dates)

        chosen: list[str] = []
        for _, row in table.iterrows():
            name = row["factor"]
            if abs(row["icir"]) < self.cfg.min_abs_icir:
                continue
            if any(abs(corr.loc[name, c]) >= self.cfg.max_factor_corr for c in chosen):
                continue
            chosen.append(name)
            if len(chosen) >= self.cfg.factor_select_k:
                break
        if len(chosen) < 2:  # 保护：相关性过滤过严时退化为纯 ICIR 排序
            chosen = table.head(max(2, self.cfg.factor_select_k))["factor"].tolist()
        chosen_rows = table[table["factor"].isin(chosen)].set_index("factor").loc[chosen]

        if self.cfg.weighting == "icir":
            raw_w = chosen_rows["icir"].abs().to_numpy()
            w = raw_w / raw_w.sum() if raw_w.sum() > 0 else np.ones(len(chosen)) / len(chosen)
        else:
            w = np.ones(len(chosen)) / len(chosen)

        self.specs = [
            FactorSpec(
                name=name,
                ic_mean=float(row["mean_ic"]),
                icir=float(row["icir"]),
                sign=int(np.sign(row["icir"]) or 1),
                weight=float(wi),
            )
            for (name, row), wi in zip(chosen_rows.iterrows(), w)
        ]
        self.diagnostics = {
            "weighting": self.cfg.weighting,
            "selected": [s.name for s in self.specs],
            "ic_table": table.to_dict(orient="records"),
            "factor_corr": corr.round(3).to_dict(),
            "n_rebalance_dates": int(len(reb_dates)),
        }
        log.info("样本内筛选：%d 个候选 → %d 个因子 %s", len(table), len(chosen),
                 ", ".join(f"{s.name}({s.icir:+.2f})" for s in self.specs))
        log.info("因子权重：%s", ", ".join(f"{s.name}:{s.weight:.2f}({s.sign:+d})" for s in self.specs))
        return self

    # ------------------------------------------------------------------
    def composite_score(self, panel: MarketPanel) -> pd.DataFrame:
        """综合得分矩阵（index=交易日, columns=股票）。"""
        if not self.specs:
            raise RuntimeError("策略尚未 fit，请先调用 fit()")
        names = [s.name for s in self.specs]
        factors = compute_factors(panel, names)

        num = None
        den = None
        for spec in self.specs:
            z = zscore_cs(winsorize_cs(factors[spec.name], self.cfg.winsorize_quantile))
            contrib = z * (spec.sign * spec.weight)
            if num is None:
                num = contrib.fillna(0.0)
                den = z.notna().astype(float) * spec.weight
            else:
                num = num + contrib.fillna(0.0)
                den = den + z.notna().astype(float) * spec.weight
        # 用「当日有效因子权重和」归一，避免缺失因子导致得分偏小
        score = num / den.replace(0.0, np.nan)
        return score

    # ------------------------------------------------------------------
    def generate_targets(self, panel: MarketPanel, start: str | None = None) -> pd.DataFrame:
        """生成调仓日的目标权重矩阵。

        参数
        ----
        panel : 需包含足够的预热数据（至少前 1.5 年）用于计算因子
        start : 回测起始日；早于该日的调仓日会被丢弃，并在起始日建仓
        """
        score = self.composite_score(panel)
        reb_dates = [d for d in self.rebalance_dates(panel.dates, start) if d in score.index]
        symbols = list(panel.close.columns)

        if self.cfg.timing_enabled:
            exposure = trend_exposure(
                panel.benchmark,
                ma_window=self.cfg.timing_ma_window,
                low=self.cfg.timing_low_exposure,
                high=self.cfg.timing_high_exposure,
            )
        else:
            exposure = pd.Series(1.0, index=panel.dates)

        rows = pd.DataFrame(0.0, index=pd.DatetimeIndex(reb_dates, name="date"), columns=symbols)
        score_log = {}
        capped_days = 0
        for d in reb_dates:
            s = score.loc[d]
            # 只投资当日有有效价格、且因子值非空的标的
            valid = s.notna() & panel.close.loc[d].notna()
            if valid.sum() < 5:
                continue
            picks = s[valid].nlargest(min(self.bt_cfg.top_n, int(valid.sum()))).index
            score_log[d] = s[valid].nlargest(10).to_dict()
            exposure_d = float(exposure.loc[d])
            raw_weight = exposure_d / len(picks)
            # 单票上限硬约束：超过上限时宁可留现金，也不越限加仓
            weight = min(raw_weight, self.cfg.max_weight)
            if raw_weight > self.cfg.max_weight + 1e-12:
                capped_days += 1
            rows.loc[d, picks] = weight

        # 去掉因有效标的不足而没有生成目标的调仓日（避免被引擎误读为清仓）
        rows = rows.loc[rows.sum(axis=1) > 0]

        self.diagnostics["capped_days"] = capped_days

        self.diagnostics["exposure"] = {str(k.date()): float(v) for k, v in exposure.items()}
        self.diagnostics["score_log"] = {
            str(k.date()): {sym: round(float(v), 4) for sym, v in vals.items()}
            for k, vals in score_log.items()
        }
        avg_exposure = float(rows.sum(axis=1).mean()) if len(rows) else 0.0
        log.info("生成 %d 个调仓日目标权重，平均股票仓位 %.0f%%%s",
                 len(rows), 100.0 * avg_exposure,
                 f"，{capped_days} 个调仓日触及单票上限" if capped_days else "")
        return rows
