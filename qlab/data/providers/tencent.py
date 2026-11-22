"""腾讯行情数据源（免费、无需 token）。

接口：``https://web.ifzq.gtimg.cn/appstock/app/fqkline/get``
    param = <symbol>,day,<start>,<end>,<count>,<qfq|hfq|day>
返回 JSON，``data.<symbol>.hfqday`` 为 K 线数组，每行：
    [日期, 开盘, 收盘, 最高, 最低, 成交量(手), ...]

复权口径的选择（重要）
----------------------
* ``qfq``（前复权）：历史价格会随每次新分红整体重算 → 回测存在**隐性前视偏差**，
  且实测发现该接口对部分标的（如 sh601088、sz000408）给出的前复权序列自身就有
  数量级错乱（单日涨幅可达 60 倍），不能用于回测。
* ``hfq``（后复权）：历史值固定不变、含分红再投资，是回测的正确口径。

本项目采用 **hfq + 锚定缩放**：把整条 hfq 序列乘以一个常数 k，
使最后一个交易日的价格等于真实市价。k 只由回测截止日的复权因子决定，
之后不再随新分红变动，因此既保留总收益信息，又保持价格量级真实（便于整手撮合）。
"""

from __future__ import annotations

import datetime as dt
import time

import pandas as pd

from qlab.data.http import fetch_json
from qlab.data.providers.base import normalize_frame
from qlab.logging_utils import get_logger

log = get_logger("qlab.provider.tencent")

BASE_URLS = (
    "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get",
    "https://ifzq.gtimg.cn/appstock/app/fqkline/get",
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
)
MAX_BARS_PER_CALL = 640
MAX_LOOPS = 40
INDEX_PREFIXES = ("sh000", "sz399")


def is_index(symbol: str) -> bool:
    return symbol.startswith(INDEX_PREFIXES)


class TencentProvider:
    """腾讯 K 线数据源。"""

    name = "tencent"

    def __init__(self, adjust: str = "hfq", pause: float = 0.12, retry: int = 3,
                 anchor: bool = True):
        self.adjust = adjust
        self.pause = pause
        self.retry = retry
        self.anchor = anchor

    # ------------------------------------------------------------------
    def _request(self, symbol: str, start: str, end: str, fq: str) -> list[list]:
        """请求 K 线；主域名被限流时自动切换备用域名。

        注意：绝不能把 ``day``（不复权）数据当成 ``hfq`` 的兜底，否则复权序列里
        会混入原始价，制造出 -85% 的假跳空。这里严格控制返回字段。
        """
        key = "day" if fq == "day" else f"{fq}day"
        last_exc: Exception | None = None
        for base in BASE_URLS:
            url = f"{base}?param={symbol},day,{start},{end},{MAX_BARS_PER_CALL},{fq}"
            try:
                payload = fetch_json(url, retry=self.retry, timeout=25)
            except Exception as exc:  # 501/超时 → 换域名
                last_exc = exc
                log.debug("%s 请求失败(%s)：%s", symbol, base.split('/')[2], exc)
                continue
            block = (payload.get("data") or {}).get(symbol) or {}
            rows = block.get(key) or []
            if rows:
                return rows
        if last_exc is not None:
            raise last_exc
        return []

    # ------------------------------------------------------------------
    def _fetch_series(self, symbol: str, start: str, end: str, fq: str) -> pd.DataFrame:
        """**向前回溯**分页抓取：每次取"以 end 为终点"的最近 640 根，直到覆盖 start。

        为什么不用"从 start 往后推"？因为接口在请求区间超过 640 根时只返回**尾部**，
        向前推会出现"窗口被截断、历史丢失"的坑；而从末尾往回推，每一次返回都是
        确定可用的最后 640 根，天然没有歧义，且对三个域名行为一致。
        """
        start_ts = pd.Timestamp(start)
        chunks: list[pd.DataFrame] = []
        cursor = pd.Timestamp(end)
        for _ in range(MAX_LOOPS):
            rows = self._request(symbol, start, cursor.strftime("%Y-%m-%d"), fq)
            if not rows:
                break
            df = pd.DataFrame(
                [r[:6] for r in rows],
                columns=["date", "open", "close", "high", "low", "volume"],
            )
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index()
            chunks.append(df)
            first = df.index.min()
            if first <= start_ts or len(df) < 100:
                break
            cursor = first - pd.Timedelta(days=1)
            time.sleep(self.pause)

        if not chunks:
            return normalize_frame(None)
        out = pd.concat(chunks)
        out = out[~out.index.duplicated(keep="last")].sort_index()
        out = out.loc[(out.index >= start_ts) & (out.index <= pd.Timestamp(end))]
        return normalize_frame(out)

    # ------------------------------------------------------------------
    def _anchor_factor(self, symbol: str, end: str) -> float:
        """用最近 25 个自然日的不复权收盘价，计算 hfq → 实际价位的缩放系数。"""
        start = (dt.date.fromisoformat(end) - dt.timedelta(days=25)).isoformat()
        rows = self._request(symbol, start, end, "day")
        if not rows:
            return 1.0
        return float(rows[-1][2])  # [date, open, close, ...]

    # ------------------------------------------------------------------
    def fetch_daily(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        """抓取 [start, end] 区间日线（自动分段、自动复权与锚定）。"""
        if is_index(symbol):
            return self._fetch_series(symbol, start, end, "day")

        fq = self.adjust if self.adjust in ("hfq", "qfq", "day") else "hfq"
        df = self._fetch_series(symbol, start, end, fq)
        if df.empty or fq == "day" or not self.anchor:
            return df

        raw_last = self._anchor_factor(symbol, end)
        price_last = float(df["close"].iloc[-1])
        if raw_last > 0 and price_last > 0:
            k = raw_last / price_last
            if abs(k - 1.0) > 1e-6:
                # 只缩放价格列，成交量保持不变
                for col in ("open", "high", "low", "close"):
                    df[col] = df[col] * k
                log.debug("%s 复权锚定系数 %.6f", symbol, k)
        return df

    # ------------------------------------------------------------------
    def fetch_index(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        return self._fetch_series(symbol, start, end, "day")
