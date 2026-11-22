"""因子与预处理单元测试。"""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from qlab.data.loader import MarketPanel
from qlab.factors.analysis import forward_returns, ic_series, ic_summary, pooled_factor_corr
from qlab.factors.library import compute_factor
from qlab.factors.preprocessing import winsorize_cs, zscore_cs


def make_panel(closes: dict[str, list[float]]) -> MarketPanel:
    n = len(next(iter(closes.values())))
    dates = pd.bdate_range("2022-01-03", periods=n)
    close = pd.DataFrame(closes, index=dates, dtype=float)
    vol = pd.DataFrame(1000.0, index=dates, columns=close.columns)
    return MarketPanel(
        open=close.copy(), high=close * 1.005, low=close * 0.995, close=close, volume=vol,
        benchmark=close.mean(axis=1), benchmark_name="等权", names={}, extra_benchmarks={},
    )


class TestFactorMath(unittest.TestCase):
    def test_momentum_exact_value(self):
        # 逐日上涨 1%：P_{t-21} / P_{t-273} - 1 = 1.01^252 - 1
        prices = [100.0 * (1.01 ** i) for i in range(400)]
        panel = make_panel({"AAA": prices})
        mom = compute_factor(panel, "mom_12_1")
        expected = (1.01 ** 252) - 1
        self.assertAlmostEqual(float(mom["AAA"].iloc[-1]), expected, places=6)

    def test_vol_positive_and_zero_for_flat(self):
        flat = make_panel({"AAA": [10.0] * 120})
        vol = compute_factor(flat, "vol_20")
        self.assertTrue(np.allclose(vol["AAA"].dropna().to_numpy(), 0.0))

    def test_reversal_sign(self):
        prices = list(np.linspace(10, 12, 60)) + [11.0]
        panel = make_panel({"AAA": prices})
        rev = compute_factor(panel, "rev_5")
        self.assertLess(float(rev["AAA"].iloc[-1]), 0)  # 近期下跌 → 反转因子为负


class TestPreprocessing(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(3)
        self.mat = pd.DataFrame(rng.normal(size=(5, 40)))

    def test_zscore_properties(self):
        z = zscore_cs(self.mat)
        self.assertTrue(np.allclose(z.mean(axis=1).to_numpy(), 0, atol=1e-9))
        self.assertTrue(np.allclose(z.std(axis=1, ddof=0).to_numpy(), 1, atol=1e-9))

    def test_winsorize_caps_outliers(self):
        mat = self.mat.copy()
        mat.iloc[0, 0] = 1e6
        w = winsorize_cs(mat, 0.02)
        self.assertLess(w.iloc[0, 0], 1e6)
        self.assertEqual(w.iloc[1, 1], mat.iloc[1, 1])


class TestIC(unittest.TestCase):
    def test_perfect_rank_ic_equals_one(self):
        rng = np.random.default_rng(11)
        factor = pd.DataFrame(rng.normal(size=(30, 50)))
        # 未来收益是因子值的正向单调变换 → 秩相关恒为 1
        fwd = factor * 2.0 + 5.0
        ic = ic_series(factor, fwd)
        self.assertTrue(np.allclose(ic.dropna().to_numpy(), 1.0, atol=1e-9))

    def test_ic_summary(self):
        ic = pd.Series([0.05, -0.02, 0.08, 0.01, 0.03])
        s = ic_summary("x", ic)
        self.assertAlmostEqual(s.mean_ic, ic.mean())
        self.assertAlmostEqual(s.pos_ratio, 0.8)
        self.assertGreater(s.t_stat, 0)

    def test_forward_returns_no_lookahead(self):
        close = pd.DataFrame({"A": [1.0, 2.0, 4.0, 8.0]})
        fwd = forward_returns(close, 1)
        self.assertAlmostEqual(float(fwd["A"].iloc[0]), 1.0)  # 2/1-1
        self.assertTrue(pd.isna(fwd["A"].iloc[-1]))

    def test_pooled_corr_detects_duplicates(self):
        rng = np.random.default_rng(5)
        base = pd.DataFrame(rng.normal(size=(10, 30)))
        dates = pd.bdate_range("2023-01-02", periods=10)
        base.index = dates
        scaled = base * 3 + 1
        corr = pooled_factor_corr({"a": base, "b": scaled}, dates)
        self.assertAlmostEqual(corr.loc["a", "b"], 1.0, places=6)


if __name__ == "__main__":
    unittest.main()
