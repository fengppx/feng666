"""数据源公共基类与数据清洗，避免 provider 与包 __init__ 循环导入。"""

from __future__ import annotations

from typing import Protocol

import pandas as pd

PRICE_COLUMNS = ["open", "high", "low", "close", "volume"]


class PriceProvider(Protocol):
    """行情数据源协议。"""

    name: str

    def fetch_daily(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        """返回 [open, high, low, close, volume]，DatetimeIndex 升序。"""
        ...


def normalize_frame(df: pd.DataFrame | None) -> pd.DataFrame:
    """统一清洗：数值化、去重、升序、丢弃非正值行。"""
    if df is None or df.empty:
        return pd.DataFrame(columns=PRICE_COLUMNS, index=pd.DatetimeIndex([], name="date"))
    out = df.copy()
    out.index = pd.to_datetime(out.index)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    for c in PRICE_COLUMNS:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out = out.dropna(subset=["open", "close"])
    out = out[(out[["open", "close"]] > 0).all(axis=1)]
    out = out[out["volume"].fillna(0) >= 0]
    out.index.name = "date"
    return out[PRICE_COLUMNS].astype(float)
