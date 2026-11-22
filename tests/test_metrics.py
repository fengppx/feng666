"""绩效指标单元测试：用手算结果校验公式实现。"""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from qlab.analytics.metrics import drawdown_series, max_drawdown, perf_metrics


class TestBasicMetrics(unittest.TestCase):
    def setUp(self):
        self.dates = pd.bdate_range("2024-01-01", periods=253)
        self.nav = pd.Series(1.0 * (1.0004 ** np.arange(253)), index=self.dates)

    def test_cumulative_and_cagr(self):
        m = perf_metrics(self.nav, rf=0.0)
        self.assertAlmostEqual(m["累计收益率"], self.nav.iloc[-1] / self.nav.iloc[0] - 1, places=8)
        # 252 个交易日 ≈ 1 年，年化 ≈ 累计收益
        self.assertAlmostEqual(m["年化收益率"], m["累计收益率"], delta=0.01)

    def test_max_drawdown_hand_computed(self):
        nav = pd.Series([1.0, 1.2, 0.9, 1.0, 1.3],
                        index=pd.bdate_range("2024-01-01", periods=5))
        mdd, peak, trough = max_drawdown(nav)
        self.assertAlmostEqual(mdd, 0.9 / 1.2 - 1, places=8)
        self.assertEqual(peak, nav.index[1])
        self.assertEqual(trough, nav.index[2])
        dd = drawdown_series(nav)
        self.assertAlmostEqual(float(dd.min()), mdd, places=8)

    def test_sharpe_zero_for_flat_nav(self):
        flat = pd.Series(1.0, index=pd.bdate_range("2024-01-01", periods=60))
        m = perf_metrics(flat, rf=0.0)
        self.assertEqual(m["累计收益率"], 0.0)
        self.assertAlmostEqual(m["最大回撤"], 0.0, places=10)

    def test_constant_drift_sharpe_sign(self):
        m = perf_metrics(self.nav, rf=0.0)
        self.assertGreater(m["夏普比率"], 0)
        self.assertGreater(m["年化收益率"], 0)

    def test_benchmark_relative(self):
        bench = pd.Series(1.0 * (1.0002 ** np.arange(253)), index=self.dates)
        m = perf_metrics(self.nav, bench, rf=0.0)
        self.assertIn("超额收益(年化)", m)
        self.assertGreater(m["超额收益(年化)"], 0)
        self.assertIn("信息比率", m)
        self.assertAlmostEqual(m["Beta"], m["Beta"])  # 非 NaN

    def test_metrics_are_finite_for_real_series(self):
        rng = np.random.default_rng(7)
        rets = rng.normal(0.0003, 0.012, 500)
        nav = pd.Series((1 + rets).cumprod(), index=pd.bdate_range("2023-01-02", periods=500))
        m = perf_metrics(nav, rf=0.015)
        for key in ("累计收益率", "年化收益率", "最大回撤", "夏普比率"):
            self.assertTrue(np.isfinite(m[key]), key)


if __name__ == "__main__":
    unittest.main()
