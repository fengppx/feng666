"""数据源适配层：统一协议 + 工厂。

    from qlab.data.providers import get_provider
    p = get_provider("tencent", adjust="qfq")
    df = p.fetch_daily("sh600519", "2019-01-01", "2024-12-31")
"""

from __future__ import annotations

from qlab.data.providers.base import PRICE_COLUMNS, PriceProvider, normalize_frame
from qlab.data.providers.sina import SinaProvider
from qlab.data.providers.tencent import TencentProvider

__all__ = [
    "PRICE_COLUMNS",
    "PriceProvider",
    "SinaProvider",
    "TencentProvider",
    "normalize_frame",
    "get_provider",
]


def get_provider(name: str = "akshare", **kwargs) -> PriceProvider:
    """按名称返回数据源实例（可选源按需惰性导入）。"""
    name = name.lower()
    if name in ("sina", "sina_kline"):
        return SinaProvider(**kwargs)
    if name in ("tencent", "tx", "gtimg"):
        return TencentProvider(**kwargs)
    if name in ("akshare", "ak"):
        from qlab.data.providers.alt import AkshareProvider

        return AkshareProvider(**kwargs)
    if name in ("baostock", "bs"):
        from qlab.data.providers.alt import BaostockProvider

        return BaostockProvider(**kwargs)
    if name in ("yfinance", "yf"):
        from qlab.data.providers.alt import YFinanceProvider

        return YFinanceProvider(**kwargs)
    raise ValueError(f"未知数据源: {name}")
