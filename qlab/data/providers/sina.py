"""新浪财经数据源：股票池 + 日线行情。

股票池接口（返回 JSON 数组，每页最多 100 条）：
    Market_Center.getHQNodeData?page=<p>&num=100&sort=mktcap&asc=0&node=hs_a

日线接口（一次请求即可返回全部历史，无需分页）：
    CN_MarketData.getKLineData?symbol=sh600519&scale=240&ma=no&datalen=3000
    scale=240 表示日线；datalen 为返回的最大根数（实测 5000 也可）。

价格口径：**不复权**。原因见 README「数据口径」：
    1) 前复权序列会随新分红整体重算，回测存在隐性前视偏差；
       且实测部分标的的前复权序列本身存在数量级错乱；
    2) 本轮基准选用沪深 300 **价格指数**（同样不含分红），
       策略与基准同为价格收益口径，比较是公平的。
"""

from __future__ import annotations

import json

import pandas as pd

from qlab.data.http import fetch_text
from qlab.data.providers.base import normalize_frame
from qlab.logging_utils import get_logger

log = get_logger("qlab.provider.sina")

UNIVERSE_URL = (
    "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData?page={page}&num=100&sort=mktcap&asc=0&node=hs_a"
)
KLINE_URL = (
    "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "CN_MarketData.getKLineData?symbol={sym}&scale=240&ma=no&datalen={n}"
)
PAGE_SIZE = 100


class SinaProvider:
    """新浪日线行情源（不复权，一次请求返回全历史）。"""

    name = "sina"

    def __init__(self, adjust: str = "day", pause: float = 0.12, retry: int = 3,
                 datalen: int = 3000):
        self.adjust = adjust
        self.pause = pause
        self.retry = retry
        self.datalen = datalen

    def fetch_daily(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        text = fetch_text(KLINE_URL.format(sym=symbol, n=self.datalen),
                          retry=self.retry, timeout=25)
        text = text.strip()
        if not text or text in ("null", "[]"):
            return normalize_frame(None)
        rows = json.loads(text)
        if not rows:
            return normalize_frame(None)
        df = pd.DataFrame(rows)
        df = df.rename(columns={"day": "date", "open": "open", "high": "high",
                                "low": "low", "close": "close", "volume": "volume"})
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        out = normalize_frame(df)
        return out.loc[(out.index >= pd.Timestamp(start)) & (out.index <= pd.Timestamp(end))]

    def fetch_index(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        return self.fetch_daily(symbol, start, end)


def fetch_universe(size: int = 300, retry: int = 3) -> pd.DataFrame:
    """按总市值降序抓取前 ``size`` 只 A 股。"""
    import json

    rows: list[dict] = []
    page = 1
    while len(rows) < size and page <= 20:
        text = fetch_text(UNIVERSE_URL.format(page=page), retry=retry, timeout=25)
        text = text.strip()
        if not text or text in ("[]", "null"):
            break
        batch = json.loads(text)
        if not batch:
            break
        rows.extend(batch)
        page += 1

    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError("新浪股票池为空，可能是接口变更或网络异常")
    df = df[["symbol", "code", "name", "mktcap", "nmc", "turnoverratio", "trade"]].copy()
    df["mktcap"] = pd.to_numeric(df["mktcap"], errors="coerce")
    df = df.sort_values("mktcap", ascending=False).head(size)
    # 过滤：只保留沪深 A 股、剔除 ST / 退市 / 停牌无价
    df = df[df["symbol"].str.match(r"^(sh6|sz0|sz3|sh68)")]
    df = df[~df["name"].str.contains("ST|退|N", na=False)]
    df = df.assign(snapshot_date=pd.Timestamp.today().strftime("%Y-%m-%d"))
    df = df.reset_index(drop=True)
    log.info("股票池抓取完成：%d 只（市值区间 %.1f ~ %.1f 亿元）",
             len(df), df["mktcap"].iloc[-1] / 1e4, df["mktcap"].iloc[0] / 1e4)
    return df
