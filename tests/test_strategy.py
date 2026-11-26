"""策略层单元测试：目标权重的硬约束与调仓日逻辑。

这里守住两条底线：
1. 单票权重不得超过 ``max_weight``（曾经被"按比例再分配"的代码绕过）；
2. 有效标的不足的调仓日必须被丢弃，而不是生成全 0 权重（会被引擎误读为清仓）。
"""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from qlab.config import BacktestConfig, StrategyConfig
from qlab.data.loader import MarketPanel
from qlab.strategy.multifactor import FactorSpec, MultiFactorStrategy


def make_panel(n_days: int = 320, n_stocks: int = 10, seed: int = 7) -> MarketPanel:
    """构造一个可复现的随机价格面板（含基准）。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2018-01-01", periods=n_days)
    rets = rng.normal(0.0002, 0.01, size=(n_days, n_stocks))
    close = pd.DataFrame(
        100.0 * np.cumprod(1.0 + rets, axis=0),
        index=dates,
        columns=[f"S{i:02d}" for i in range(n_stocks)],
    )
    vol = pd.DataFrame(1000.0, index=dates, columns=close.columns)
    bench = close.mean(axis=1)
    return MarketPanel(
        open=close.copy(),
        high=close * 1.01,
        low=close * 0.99,
        close=close,
        volume=vol,
        benchmark=bench,
        benchmark_name="等权基准",
        names={c: c for c in close.columns},
        extra_benchmarks={},
    )


def make_strategy(top_n: int = 5, max_weight: float = 0.08, start: str = "2019-01-01"):
    cfg = StrategyConfig(timing_enabled=False, max_weight=max_weight)
    bt = BacktestConfig(start=start, top_n=top_n)
    strat = MultiFactorStrategy(cfg, bt)
    # 跳过 fit，直接给一个确定性的因子，专测组合构建逻辑
    strat.specs = [FactorSpec(name="mom_1", ic_mean=0.02, icir=0.3, sign=1, weight=1.0)]
    return strat, bt


class TestTargetWeights(unittest.TestCase):
    def test_single_weight_never_exceeds_cap(self):
        panel = make_panel()
        strat, bt = make_strategy(top_n=5, max_weight=0.08)
        targets = strat.generate_targets(panel, start=bt.start)

        self.assertFalse(targets.empty)
        max_w = float(targets.to_numpy().max())
        self.assertLessEqual(max_w, 0.08 + 1e-9,
                             f"单票权重 {max_w:.4f} 超过上限 8%")
        # 5 只 × 8% 上限 → 总仓位最多 40%，其余留现金
        self.assertLessEqual(float(targets.sum(axis=1).max()), 0.40 + 1e-9)

    def test_rebalance_rows_are_not_all_zero(self):
        panel = make_panel()
        strat, bt = make_strategy(top_n=3, max_weight=0.08)
        targets = strat.generate_targets(panel, start=bt.start)

        self.assertTrue((targets.sum(axis=1) > 0).all(), "存在全 0 权重的调仓日")

    def test_missing_data_dates_are_dropped(self):
        panel = make_panel(n_days=60, n_stocks=10)
        strat, bt = make_strategy(top_n=5)
        # 面板太短，因子基本全为 NaN → 不应产生任何目标，更不应抛异常
        targets = strat.generate_targets(panel, start=bt.start)
        self.assertTrue(targets.empty or (targets.sum(axis=1) > 0).any())

    def test_zero_exposure_day_is_kept_as_clear_signal(self):
        """仓位系数为 0 的调仓日必须保留为“清仓”，不能被当成“无信号”丢掉。"""
        n = 160
        dates = pd.bdate_range("2018-01-01", periods=n)
        trend = pd.Series(np.linspace(200.0, 100.0, n), index=dates)
        close = pd.DataFrame(
            {f"S{i:02d}": trend * (1 + 0.01 * i) for i in range(10)}, index=dates
        )
        vol = pd.DataFrame(1000.0, index=dates, columns=close.columns)
        panel = MarketPanel(
            open=close.copy(), high=close * 1.01, low=close * 0.99, close=close,
            volume=vol, benchmark=trend, benchmark_name="下行基准",
            names={c: c for c in close.columns}, extra_benchmarks={},
        )
        cfg = StrategyConfig(timing_enabled=True, timing_ma_window=20,
                             timing_low_exposure=0.0)
        bt = BacktestConfig(start="2018-06-01", top_n=5)
        strat = MultiFactorStrategy(cfg, bt)
        strat.specs = [FactorSpec(name="mom_1", ic_mean=0.02, icir=0.3, sign=1, weight=1.0)]
        targets = strat.generate_targets(panel, start=bt.start)
        self.assertFalse(targets.empty)
        self.assertTrue((targets.sum(axis=1) == 0).any(), "空仓信号被丢弃")


if __name__ == "__main__":
    unittest.main()
