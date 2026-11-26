"""可选数据源：akshare / baostock / yfinance。

课程文件列出的四个免费数据源里，tushare 需要注册 token，yfinance 主要覆盖美股，
所以 A 股日频实际接的是 **akshare** 和 **baostock**，两者都免注册、免费。

默认仍走腾讯（K 线）+ 新浪（股票池），因为速度快且不见限流；需要换源时：

    python scripts/download_data.py --provider akshare
    python scripts/download_data.py --provider baostock
    python -m qlab data --provider baostock --size 100

三个适配器都实现 ``fetch_daily``，返回与腾讯/新浪一致的
``[open, high, low, close, volume]``，``volume`` 统一为「手」。
akshare 额外提供 ``fetch_universe``，可直接按总市值取股票池；
baostock 没有市值接口，股票池仍回退到新浪列表。
"""

from __future__ import annotations

import contextlib
import io
import re
import threading

import pandas as pd

from qlab.data.providers.base import normalize_frame

_SYMBOL_RE = re.compile(r"^(sh|sz|bj)(\d{6})$", re.IGNORECASE)

# akshare 返回的列名 -> 统一列名
_CN_COLUMNS = {
    "日期": "date", "开盘": "open", "最高": "high", "最低": "low",
    "收盘": "close", "成交量": "volume",
}


def _require(module: str, hint: str):
    try:
        return __import__(module)
    except ImportError as exc:  # pragma: no cover - 取决于本地环境
        raise ImportError(f"缺少可选数据源依赖 {module}，请先安装：{hint}") from exc


def _split(symbol: str) -> tuple[str, str]:
    """``sh600519`` -> ``("sh", "600519")``。"""
    m = _SYMBOL_RE.match(str(symbol))
    if not m:
        return "", str(symbol)
    return m.group(1).lower(), m.group(2)


def _is_index(symbol: str) -> bool:
    """沪深指数：sh000xxx（沪深300/中证500）、sz399xxx（深证成指）。"""
    prefix, code = _split(symbol)
    return (prefix == "sh" and code.startswith("000")) or (
        prefix == "sz" and code.startswith("399")
    )


def _first_attr(obj, names: list[str]):
    """按顺序取第一个存在的接口（akshare 近几个版本频繁改名）。"""
    for name in names:
        fn = getattr(obj, name, None)
        if callable(fn):
            return fn
    raise AttributeError(f"{getattr(obj, '__name__', obj)} 缺少接口：{names}")


def _to_symbol(code: str) -> str:
    """6 位代码 -> 带交易所前缀的 8 位代码。"""
    if code.startswith(("600", "601", "603", "605", "688", "689")):
        return "sh" + code
    if code.startswith(("000", "001", "002", "003", "300", "301")):
        return "sz" + code
    if code.startswith(("430", "8", "9")):
        return "bj" + code
    return ""


class AkshareProvider:
    """akshare：A 股日线、指数与股票池，免费无需 token。

    取数有两条通道：东方财富（首选）与新浪（备用）。东财对高频请求会临时限流，
    失败时自动切到新浪，因此单只标的一两次失败不会中断整个抓取。
    """

    name = "akshare"

    def __init__(self, adjust: str = "day", pause: float = 0.2, retry: int = 2, **_):
        # akshare 的 adjust 口径："" 不复权 / "qfq" 前复权 / "hfq" 后复权
        self.adjust = "" if adjust in ("day", "", None) else adjust
        self.pause = pause
        self.retry = retry
        # 最近一次取数实际走的通道（em=东方财富 / sina=新浪），供 manifest 追溯
        self.last_channel: str | None = None

    # ------------------------------------------------------------------
    def fetch_daily(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        ak = _require("akshare", "pip install akshare")
        errors: list[str] = []

        for label, fetch in (("东方财富", self._from_em), ("新浪", self._from_sina)):
            try:
                raw, needs_volume_fix = fetch(ak, symbol, start, end)
            except Exception as exc:
                errors.append(f"{label}：{str(exc).splitlines()[0][:120]}")
                continue
            if raw is None or raw.empty:
                errors.append(f"{label}：空数据")
                continue
            self.last_channel = "em" if label == "东方财富" else "sina"
            return self._prepare(raw, needs_volume_fix, start, end)

        raise RuntimeError(f"akshare 取数失败（{symbol}）：" + "；".join(errors))

    # ------------------------------------------------------------------
    def _from_em(self, ak, symbol: str, start: str, end: str):
        """东方财富通道。成交量单位：手。"""
        s, e = start.replace("-", ""), end.replace("-", "")
        _, code = _split(symbol)
        if _is_index(symbol):
            # 1.20.44 起 index_zh_a_hist -> index_zh_hist_em
            fn = _first_attr(ak, ["index_zh_hist_em", "index_zh_a_hist"])
            return fn(symbol=code, period="daily", start_date=s, end_date=e), False
        # 1.20.46 起 stock_zh_a_hist -> stock_zh_a_hist_em
        fn = _first_attr(ak, ["stock_zh_a_hist_em", "stock_zh_a_hist"])
        return fn(symbol=code, period="daily", start_date=s, end_date=e,
                  adjust=self.adjust), False

    def _from_sina(self, ak, symbol: str, start: str, end: str):
        """新浪通道。返回全历史，成交量单位：股（需除以 100 换成手）。"""
        if _is_index(symbol):
            fn = _first_attr(ak, ["index_zh_daily_sina", "stock_zh_index_daily"])
            return fn(symbol=symbol), True
        fn = _first_attr(ak, ["stock_zh_a_hist_sina", "stock_zh_a_daily"])
        return fn(symbol=symbol), True

    @staticmethod
    def _prepare(df: pd.DataFrame, volume_in_shares: bool, start: str, end: str):
        df = df.rename(columns=_CN_COLUMNS)
        df = df.rename(columns=str.lower)
        if "date" not in df.columns or "close" not in df.columns:
            raise RuntimeError(f"返回列名异常：{list(df.columns)}")
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        if volume_in_shares and "volume" in df.columns:
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce") / 100.0
        out = normalize_frame(df)
        return out.loc[(out.index >= pd.Timestamp(start)) & (out.index <= pd.Timestamp(end))]

    # ------------------------------------------------------------------
    def fetch_universe(self, size: int = 300) -> pd.DataFrame:
        """按总市值降序取前 ``size`` 只 A 股（东财实时快照）。"""
        ak = _require("akshare", "pip install akshare")
        spot = ak.stock_zh_a_spot_em()
        if spot is None or spot.empty:
            raise RuntimeError("akshare 股票池为空")

        need = {"代码", "名称", "总市值"}
        if not need.issubset(spot.columns):
            raise RuntimeError(f"akshare 股票池列名变化：{list(spot.columns)}")

        df = pd.DataFrame({
            "code": spot["代码"].astype(str).str.zfill(6),
            "name": spot["名称"].astype(str),
            # 新浪口径用「万元」，这里统一换算
            "mktcap": pd.to_numeric(spot["总市值"], errors="coerce") / 1e4,
            "nmc": pd.to_numeric(spot.get("流通市值", 0), errors="coerce") / 1e4,
            "turnoverratio": pd.to_numeric(spot.get("换手率", 0), errors="coerce"),
            "trade": pd.to_numeric(spot.get("最新价", 0), errors="coerce"),
        })
        df["symbol"] = df["code"].map(_to_symbol)
        df = df[df["symbol"].str.len() == 8]
        df = df[~df["name"].str.contains("ST|退", na=False)]
        df = df.sort_values("mktcap", ascending=False).head(size)
        df = df.assign(snapshot_date=pd.Timestamp.today().strftime("%Y-%m-%d"))
        return df[["symbol", "code", "name", "mktcap", "nmc",
                   "turnoverratio", "trade", "snapshot_date"]].reset_index(drop=True)


class BaostockProvider:
    """baostock：A 股日线，免注册。成交量原始单位为「股」，这里换算成「手」。"""

    name = "baostock"
    _lock = threading.Lock()  # baostock 的 login/logout 是进程级全局状态

    def __init__(self, adjust: str = "day", **_):
        # adjustflag：1 后复权 / 2 前复权 / 3 不复权
        self.adjustflag = {"hfq": "1", "qfq": "2", "day": "3", "": "3"}.get(adjust, "3")

    def fetch_daily(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        bs = _require("baostock", "pip install baostock")
        prefix, code = _split(symbol)
        if not prefix:
            raise ValueError(f"无法识别的代码格式：{symbol}（应形如 sh600519）")
        bs_code = f"{prefix}.{code}"

        with self._lock, contextlib.redirect_stdout(io.StringIO()):
            login = bs.login()
            if login.error_code != "0":
                raise RuntimeError(f"baostock 登录失败：{login.error_msg}")
            try:
                rs = bs.query_history_k_data_plus(
                    bs_code, "date,open,high,low,close,volume",
                    start_date=start, end_date=end, frequency="d",
                    adjustflag=self.adjustflag,
                )
                if rs.error_code != "0":
                    raise RuntimeError(f"baostock 查询失败（{bs_code}）：{rs.error_msg}")
                rows = []
                while rs.next():
                    rows.append(rs.get_row_data())
            finally:
                bs.logout()

        if not rows:
            return normalize_frame(None)

        df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
        df["date"] = pd.to_datetime(df["date"])
        out = normalize_frame(df.set_index("date"))
        out["volume"] = out["volume"] / 100.0  # 股 -> 手
        return out


class YFinanceProvider:
    """yfinance：美股与全球市场（A 股项目里主要用于跨市场对照）。"""

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
