"""数据源适配层的离线单元测试（不联网）。

覆盖：代码格式解析、指数识别、股票池抽样、收盘日计算、行情清洗。
"""

import unittest

import pandas as pd

from qlab.data.crosscheck import sample_symbols
from qlab.data.loader import last_completed_day
from qlab.data.providers.alt import _is_index, _split, _to_symbol
from qlab.data.providers.base import normalize_frame


class TestSymbolParsing(unittest.TestCase):
    def test_split(self):
        self.assertEqual(_split("sh600519"), ("sh", "600519"))
        self.assertEqual(_split("sz000001"), ("sz", "000001"))
        self.assertEqual(_split("600519"), ("", "600519"))

    def test_index_detection(self):
        self.assertTrue(_is_index("sh000300"))
        self.assertTrue(_is_index("sh000905"))
        self.assertTrue(_is_index("sz399001"))
        self.assertFalse(_is_index("sh600519"))
        self.assertFalse(_is_index("sz000001"))

    def test_to_symbol(self):
        self.assertEqual(_to_symbol("600519"), "sh600519")
        self.assertEqual(_to_symbol("688981"), "sh688981")
        self.assertEqual(_to_symbol("000001"), "sz000001")
        self.assertEqual(_to_symbol("300750"), "sz300750")
        self.assertEqual(_to_symbol("123456"), "")


class TestUniverseSampling(unittest.TestCase):
    def test_deterministic(self):
        symbols = [f"sh{600000 + i}" for i in range(100)]
        self.assertEqual(sample_symbols(symbols, 5), sample_symbols(symbols, 5))

    def test_size(self):
        symbols = [f"s{i}" for i in range(50)]
        self.assertEqual(len(sample_symbols(symbols, 5)), 5)
        # 抽样数超过总数时返回全部
        self.assertEqual(len(sample_symbols(symbols, 500)), 50)


class TestLastCompletedDay(unittest.TestCase):
    def test_format_and_range(self):
        day = last_completed_day()
        self.assertRegex(day, r"^\d{4}-\d{2}-\d{2}$")
        self.assertLessEqual(pd.Timestamp(day), pd.Timestamp.now())


class TestNormalizeFrame(unittest.TestCase):
    def test_drops_invalid_rows(self):
        raw = pd.DataFrame(
            {
                "open": [1.0, 0.0, 2.0],
                "high": [1.1, 0.0, 2.1],
                "low": [0.9, 0.0, 1.9],
                "close": [1.0, 0.0, 2.0],
                "volume": [100, 0, 200],
            },
            index=pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
        )
        out = normalize_frame(raw)
        self.assertEqual(len(out), 2)  # 价格为 0 的脏数据被丢弃
        self.assertEqual(list(out.columns), ["open", "high", "low", "close", "volume"])
        self.assertTrue((out["close"] > 0).all())

    def test_empty_input(self):
        self.assertTrue(normalize_frame(None).empty)


if __name__ == "__main__":
    unittest.main()
