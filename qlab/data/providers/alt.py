"""可选数据源：akshare / baostock / yfinance。

这些库依赖较重，本项目默认不安装；需要时 ``pip install akshare`` 即可切换。
适配器保持与 ``PriceProvider`` 相同的返回格式，上层无需改动。
"""

from __future__ import annotations

import pandas as pd

from qlab.data.providers.base import normalize_frame


def _require(module: str, hint: str):
    try:
        return __import__(module)
    except ImportError as exc:  # pragma: no cover - 取决于本地环境
        raise ImportError(
            f"缺少可选数据源依赖 {module}，请先安装：{hint}"
        ) from exc


class AkshareProvider:
    """akshare：覆盖 A 股行情、宏观与基本面，免费。"""

    name = "akshare"

    def __init__(self, adjust: str = "qfq", **_):
        self.adjust = "" if adjust == "day" else adjust

    def fetch_daily(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        ak = _require("akshare", "pip install akshare")
        code = symbol.split(".")[-1]
        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
            adjust=self.adjust,
        )
        if df is None or df.empty:
            return normalize_frame(None)
        df = df.rename(
            columns={
                "日期": "date", "开盘": "open", "收盘": "close", "最高": "high",
                "最低": "low", "成交量": "volume",
            }
        )
        df["date"] = pd.to_datetime(df["date"])
        return normalize_frame(df.set_index("date"))


class BaostockProvider:
    """baostock：免费 A 股日频/分钟频数据，无需 token。"""

    name = "baostock"

    def __init__(self, adjust: str = "qfq", **_):
        self.adjust = {"qfq": "2", "hfq": "1", "day": "3"}.get(adjust, "2")

    def fetch_daily(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        bs = _require("baostock", "pip install baostock")
        bs.login()
        try:
            rs = bs.query_history_k_data_plus(
                symbol if "." in symbol else f"sh.{symbol}",
                "date,open,high,low,close,volume",
                start_date=start,
                end_date=end,
                frequency="d",
                adjustflag=self.adjust,
            )
            rows = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())
        finally:
            bs.logout()
        if not rows:
            return normalize_frame(None)
        df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
        df["date"] = pd.to_datetime(df["date"])
        return normalize_frame(df.set_index("date"))


class YFinanceProvider:
    """yfinance：美股与全球市场（在 A 股项目里主要用于跨市场对照）。"""

    name = "yfinance"

    def __init__(self, **_):
        pass

    def fetch_daily(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        yf = _require("yfinance", "pip install yfinance")
        df = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=True)
        if df is None or df.empty:
            return normalize_frame(None)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.rename(columns=str.lower)
        return normalize_frame(df)
