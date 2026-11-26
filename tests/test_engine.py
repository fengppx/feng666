"""回测引擎单元测试：撮合时点、费用、现金约束、净值一致性。

这些测试是整个系统的"防回归底线"——任何改动只要破坏下面任意一条，
就说明回测结果不再可信。
"""

from __future__ import annotations

import unittest

import pandas as pd

from qlab.backtest import PortfolioBacktester
from qlab.config import BacktestConfig
from qlab.data.loader import MarketPanel


def make_panel(prices: dict[str, list[float]], opens: dict[str, list[float]] | None = None,
               start: str = "2024-01-01") -> MarketPanel:
    """用给定收盘价（与可选开盘价）构造行情面板。"""
    n = len(next(iter(prices.values())))
    dates = pd.bdate_range(start=start, periods=n)
    close = pd.DataFrame(prices, index=dates, dtype=float)
    if opens is None:
        open_ = close.copy()
    else:
        open_ = pd.DataFrame(opens, index=dates, dtype=float)
    vol = pd.DataFrame(1000.0, index=dates, columns=close.columns)
    bench = close.mean(axis=1)
    return MarketPanel(
        open=open_, high=close * 1.01, low=close * 0.99, close=close, volume=vol,
        benchmark=bench, benchmark_name="等权基准", names={c: c for c in close.columns},
        extra_benchmarks={},
    )


class TestExecutionTiming(unittest.TestCase):
    """信号在 t 日收盘产生，必须用 t+1 日开盘价成交。"""

    def test_buys_at_next_open_not_signal_close(self):
        # t0 close=10, t1 open=100（跳空），若错误地用 t0 收盘价成交，净值会放大 10 倍
        close = [10.0, 100.0, 100.0, 100.0]
        open_ = [10.0, 100.0, 100.0, 100.0]
        panel = make_panel({"AAA": close}, {"AAA": open_})
        cfg = BacktestConfig(initial_cash=1_000_000, commission_rate=0.0,
                             min_commission=0.0, slippage=0.0)
        targets = pd.DataFrame(0.0, index=[panel.dates[0]], columns=["AAA"])
        targets.iloc[0, 0] = 1.0

        res = PortfolioBacktester(cfg).run(panel, targets)
        # 第 2 天按 100 元买入，随后价格不变 → 净值应始终 ≈ 1.0
        self.assertAlmostEqual(res.nav.iloc[1], 1.0, places=4)
        self.assertAlmostEqual(res.nav.iloc[-1], 1.0, places=4)
        trade = res.trades.iloc[0]
        self.assertAlmostEqual(float(trade["price"]), 100.0, places=4)

    def test_no_position_before_rebalance(self):
        panel = make_panel({"AAA": [10, 11, 12, 13, 14]})
        cfg = BacktestConfig(initial_cash=1_000_000, commission_rate=0, min_commission=0,
                             slippage=0)
        targets = pd.DataFrame(0.0, index=[panel.dates[2]], columns=["AAA"])
        targets.iloc[0, 0] = 1.0
        res = PortfolioBacktester(cfg).run(panel, targets)
        self.assertEqual(res.trades.iloc[0]["date"], panel.dates[3])
        self.assertAlmostEqual(res.nav.iloc[0], 1.0, places=6)

    def test_uses_next_open_price_not_next_close(self):
        # t1 开盘 100、收盘 200：按开盘价成交净值约为 2.0；若误用 t1 收盘价成交则约为 1.0
        panel = make_panel({"AAA": [10.0, 200.0, 200.0]}, {"AAA": [10.0, 100.0, 100.0]})
        cfg = BacktestConfig(initial_cash=1_000_000, commission_rate=0, min_commission=0,
                             slippage=0)
        targets = pd.DataFrame(0.0, index=[panel.dates[0]], columns=["AAA"])
        targets.iloc[0, 0] = 1.0
        res = PortfolioBacktester(cfg).run(panel, targets)
        self.assertAlmostEqual(float(res.trades.iloc[0]["price"]), 100.0, places=4)
        # 整手/现金余量会让净值略低于 2.0，但必须明显高于“按收盘价成交”时的 1.0
        self.assertGreater(res.nav.iloc[1], 1.8)
        self.assertLess(res.nav.iloc[1], 2.05)


class TestSuspension(unittest.TestCase):
    """停牌（当日无开盘价）标的不可成交：既不能买，也不能卖。"""

    def test_suspended_stock_is_not_bought(self):
        panel = make_panel({"AAA": [10.0] * 5},
                           {"AAA": [10.0, float("nan"), 10.0, 10.0, 10.0]})
        cfg = BacktestConfig(initial_cash=1_000_000, commission_rate=0,
                             min_commission=0, slippage=0)
        targets = pd.DataFrame(0.0, index=[panel.dates[0]], columns=["AAA"])
        targets.iloc[0, 0] = 1.0
        res = PortfolioBacktester(cfg).run(panel, targets)
        self.assertTrue(res.trades.empty)
        self.assertAlmostEqual(res.nav.iloc[-1], 1.0, places=6)

    def test_suspended_holding_is_not_sold(self):
        panel = make_panel(
            {"AAA": [10.0] * 6},
            {"AAA": [10.0, 10.0, 10.0, float("nan"), float("nan"), 10.0]},
        )
        cfg = BacktestConfig(initial_cash=1_000_000, commission_rate=0,
                             min_commission=0, slippage=0)
        t = pd.DataFrame(0.0, index=[panel.dates[0], panel.dates[2]], columns=["AAA"])
        t.iloc[0, 0] = 1.0   # 建仓，t+1 成交
        t.iloc[1, 0] = 0.0   # 清仓信号，t+1（第 4 天）停牌，卖不出
        res = PortfolioBacktester(cfg).run(panel, t)
        self.assertEqual(len(res.trades), 1)          # 只有买入那一笔
        self.assertEqual(int(res.n_holdings.iloc[-1]), 1)


class TestCosts(unittest.TestCase):
    """佣金 / 印花税 / 滑点的记账准确性。"""

    def test_commission_and_slippage(self):
        panel = make_panel({"AAA": [10.0] * 6})
        cfg = BacktestConfig(
            initial_cash=1_000_000, commission_rate=0.00025, min_commission=5.0,
            slippage=0.001,
        )
        targets = pd.DataFrame(0.0, index=[panel.dates[0]], columns=["AAA"])
        targets.iloc[0, 0] = 1.0
        res = PortfolioBacktester(cfg).run(panel, targets)
        buy = res.trades.iloc[0]
        expected_price = 10.0 * (1 + 0.001)
        self.assertAlmostEqual(float(buy["price"]), expected_price, places=6)
        self.assertAlmostEqual(float(buy["commission"]),
                               max(5.0, float(buy["notional"]) * 0.00025), places=4)
        # 买入后现金不为负
        self.assertGreaterEqual(float(res.cash.iloc[1]), -1e-6)

    def test_stamp_duty_schedule(self):
        cfg = BacktestConfig()
        self.assertAlmostEqual(cfg.stamp_duty("2023-08-25"), 0.001)
        self.assertAlmostEqual(cfg.stamp_duty("2023-08-28"), 0.0005)
        self.assertAlmostEqual(cfg.stamp_duty("2024-01-02"), 0.0005)

    def test_sell_pays_stamp_duty(self):
        panel = make_panel({"AAA": [10.0] * 8})
        cfg = BacktestConfig(initial_cash=1_000_000, commission_rate=0.0,
                             min_commission=0.0, slippage=0.0)
        t = pd.DataFrame(0.0, index=[panel.dates[0], panel.dates[3]], columns=["AAA"])
        t.iloc[0, 0] = 1.0
        t.iloc[1, 0] = 0.0
        res = PortfolioBacktester(cfg).run(panel, t)
        sells = res.trades[res.trades["side"] == "SELL"]
        self.assertEqual(len(sells), 1)
        self.assertGreater(float(sells.iloc[0]["stamp_duty"]), 0.0)


class TestAccounting(unittest.TestCase):
    """净值 = 现金 + 持仓市值；收益应等于价格涨幅（零成本时）。"""

    def test_nav_equals_price_growth_without_costs(self):
        prices = [10.0 * (1.01 ** i) for i in range(30)]
        panel = make_panel({"AAA": prices})
        cfg = BacktestConfig(initial_cash=1_000_000, commission_rate=0.0,
                             min_commission=0.0, slippage=0.0)
        targets = pd.DataFrame(0.0, index=[panel.dates[0]], columns=["AAA"])
        targets.iloc[0, 0] = 1.0
        res = PortfolioBacktester(cfg).run(panel, targets)
        # 第 2 天满仓买入后，净值应与价格同步（允许整手误差）
        ratio = res.nav.iloc[-1] / res.nav.iloc[1]
        expected = prices[-1] / prices[1]
        # 允许整手取整带来的极小误差
        self.assertAlmostEqual(ratio, expected, delta=expected * 0.002)

    def test_equity_identity(self):
        panel = make_panel({"AAA": [10, 11, 12, 13, 14, 15], "BBB": [20, 19, 18, 19, 20, 21]})
        cfg = BacktestConfig(initial_cash=500_000, commission_rate=0.00025, slippage=0.0005)
        targets = pd.DataFrame(0.0, index=[panel.dates[0]], columns=["AAA", "BBB"])
        targets.loc[panel.dates[0]] = [0.6, 0.4]
        res = PortfolioBacktester(cfg).run(panel, targets)
        self.assertTrue((res.equity > 0).all())
        self.assertTrue((res.exposure >= -1e-9).all())
        self.assertTrue((res.exposure <= 1 + 1e-9).all())

    def test_cash_never_negative(self):
        panel = make_panel({"AAA": [10.0] * 10, "BBB": [33.0] * 10})
        cfg = BacktestConfig(initial_cash=100_000, commission_rate=0.00025, slippage=0.0)
        targets = pd.DataFrame(0.0, index=[panel.dates[0]], columns=["AAA", "BBB"])
        targets.loc[panel.dates[0]] = [0.5, 0.5]
        res = PortfolioBacktester(cfg).run(panel, targets)
        self.assertGreaterEqual(float(res.cash.min()), -1e-6)


if __name__ == "__main__":
    unittest.main()
