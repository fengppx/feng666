"""数据门户：负责「抓取 → 缓存 → 装配面板」的完整流程。

上层（因子/策略/回测）只与 :class:`MarketPanel` 交互：
    panel.open / panel.high / panel.low / panel.close / panel.volume
均为 ``DataFrame(index=交易日, columns=股票代码)``，缺失值用 NaN 表示停牌。
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import pandas as pd

from qlab.config import DataConfig
from qlab.data import cache
from qlab.data.providers import get_provider
from qlab.logging_utils import get_logger

log = get_logger("qlab.data")


@dataclass
class MarketPanel:
    """对齐后的行情面板。"""

    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame
    volume: pd.DataFrame
    benchmark: pd.Series
    benchmark_name: str
    names: dict[str, str]
    extra_benchmarks: dict[str, pd.Series]

    # -- 派生量 ----------------------------------------------------------
    @property
    def dates(self) -> pd.DatetimeIndex:
        return self.close.index

    @property
    def symbols(self) -> list[str]:
        return list(self.close.columns)

    @property
    def returns(self) -> pd.DataFrame:
        return self.close.pct_change(fill_method=None)

    @property
    def amount(self) -> pd.DataFrame:
        """成交额代理（元）：收盘价 × 成交量(手) × 100。"""
        return self.close * self.volume * 100.0

    def slice(self, start: str | None = None, end: str | None = None) -> "MarketPanel":
        """按日期切片（用于样本内/样本外切分）。"""
        idx = self.close.index
        mask = pd.Series(True, index=idx)
        if start is not None:
            mask &= idx >= pd.Timestamp(start)
        if end is not None:
            mask &= idx <= pd.Timestamp(end)
        sub = idx[mask]
        return MarketPanel(
            open=self.open.loc[sub],
            high=self.high.loc[sub],
            low=self.low.loc[sub],
            close=self.close.loc[sub],
            volume=self.volume.loc[sub],
            benchmark=self.benchmark.loc[sub],
            benchmark_name=self.benchmark_name,
            names=self.names,
            extra_benchmarks={k: v.loc[sub] for k, v in self.extra_benchmarks.items()},
        )

    def coverage(self) -> pd.DataFrame:
        """每只标的的首末交易日与有效样本数，用于数据体检。"""
        first = self.close.apply(lambda s: s.first_valid_index())
        last = self.close.apply(lambda s: s.last_valid_index())
        count = self.close.notna().sum()
        return pd.DataFrame({"first_date": first, "last_date": last, "n_obs": count})


class DataPortal:
    """数据抓取与面板装配入口。"""

    def __init__(self, cfg: DataConfig | None = None, provider: str | None = None):
        self.cfg = cfg or DataConfig()
        self.provider_name = provider or self.cfg.provider
        self._provider = get_provider(
            self.provider_name,
            adjust=self.cfg.adjust,
            pause=self.cfg.request_pause,
            retry=self.cfg.request_retry,
        )
        self.universe: pd.DataFrame | None = None

    # ------------------------------------------------------------------
    # 股票池
    # ------------------------------------------------------------------
    def ensure_universe(self, force: bool = False) -> pd.DataFrame:
        """准备股票池。

        优先顺序：本地缓存 → 当前数据源自带的股票池接口（akshare 有）→ 新浪列表。
        baostock 没有市值接口，无法按市值排序，因此自动回退到新浪。
        """
        if not force:
            cached = cache.load_universe()
            if cached is not None and len(cached) >= self.cfg.universe_size * 0.8:
                self.universe = cached
                log.info("使用本地股票池缓存：%d 只", len(cached))
                return cached

        uni = None
        fetcher = getattr(self._provider, "fetch_universe", None)
        if callable(fetcher):
            try:
                uni = fetcher(self.cfg.universe_size)
                log.info("股票池来自数据源 %s：%d 只", self.provider_name, len(uni))
            except Exception as exc:
                log.warning("数据源 %s 的股票池接口不可用（%s），改用新浪列表",
                            self.provider_name, exc)
                uni = None

        if uni is None or len(uni) == 0:
            from qlab.data.providers.sina import fetch_universe

            uni = fetch_universe(self.cfg.universe_size + 40)  # 多抓一些用于过滤
            uni = uni.head(self.cfg.universe_size).reset_index(drop=True)

        cache.save_universe(uni)
        self.universe = uni
        return uni

    # ------------------------------------------------------------------
    # 行情
    # ------------------------------------------------------------------
    def _fetch_one(self, symbol: str, start: str, end: str) -> tuple[str, pd.DataFrame]:
        df = self._provider.fetch_daily(symbol, start, end)
        return symbol, df

    def download(
        self,
        force: bool = False,
        symbols: list[str] | None = None,
        progress_every: int = 25,
        min_rows: int = 0,
        start_before: str | None = None,
    ) -> dict[str, int]:
        """并发抓取全部标的，返回 {symbol: 行数}。

        min_rows / start_before 用于"补抓"：缓存里行数不足、或起始日过晚的标的
        会被重新下载，从而实现多轮增量修复。
        """
        if self.universe is None:
            self.ensure_universe()
        assert self.universe is not None
        uni = self.universe
        syms = symbols or list(uni["symbol"])
        end = self.cfg.fetch_end or pd.Timestamp.today().strftime("%Y-%m-%d")
        start = self.cfg.fetch_start

        def needs_refresh(sym: str) -> bool:
            if force or not cache.has_prices(sym):
                return True
            cached = cache.load_prices(sym)
            if cached is None or cached.empty:
                return True
            if min_rows and len(cached) < min_rows:
                return True
            if start_before and cached.index[0] > pd.Timestamp(start_before):
                return True
            return False

        todo = [s for s in syms if needs_refresh(s)]
        log.info("行情抓取：待抓 %d / 共 %d 只，区间 %s ~ %s",
                 len(todo), len(syms), start, end)
        stats: dict[str, int] = {}
        if not todo:
            return stats

        t0 = time.time()
        done = 0
        with ThreadPoolExecutor(max_workers=self.cfg.request_workers) as pool:
            futures = {pool.submit(self._fetch_one, s, start, end): s for s in todo}
            for fut in as_completed(futures):
                sym = futures[fut]
                try:
                    _, df = fut.result()
                except Exception as exc:  # 单只失败不影响整体
                    log.warning("抓取失败 %s: %s", sym, exc)
                    continue
                if df is None or df.empty:
                    log.warning("空数据 %s（可能已退市/停牌）", sym)
                    continue
                cache.save_prices(sym, df)
                cache.update_manifest(
                    sym,
                    {
                        "rows": int(len(df)),
                        "start": str(df.index.min().date()),
                        "end": str(df.index.max().date()),
                        "source": self.provider_name,
                        "adjust": self.cfg.adjust,
                    },
                )
                stats[sym] = len(df)
                done += 1
                if done % progress_every == 0:
                    log.info("  进度 %d/%d，用时 %.0fs", done, len(todo), time.time() - t0)

        log.info("行情抓取完成：成功 %d 只，用时 %.0fs", len(stats), time.time() - t0)
        return stats

    def download_benchmarks(self, symbols: list[str] | None = None) -> dict[str, int]:
        """抓取基准指数（沪深 300 等），与个股共用缓存格式。"""
        syms = symbols or [self.cfg.benchmark, *list(self.cfg.extra_benchmarks)]
        end = self.cfg.fetch_end or pd.Timestamp.today().strftime("%Y-%m-%d")
        out: dict[str, int] = {}
        for sym in syms:
            try:
                df = self._provider.fetch_daily(sym, self.cfg.fetch_start, end)
            except Exception as exc:  # 基准抓取失败不应中断整体流程
                log.warning("基准 %s 抓取失败：%s", sym, exc)
                continue
            if df is None or df.empty:
                log.warning("基准 %s 抓取为空", sym)
                continue
            cache.save_prices(sym, df)
            cache.update_manifest(
                sym,
                {"rows": int(len(df)), "start": str(df.index.min().date()),
                 "end": str(df.index.max().date()), "source": self.provider_name,
                 "adjust": self.cfg.adjust, "kind": "index"},
            )
            out[sym] = len(df)
            log.info("基准 %s：%d 行（%s ~ %s）", sym, len(df),
                     df.index.min().date(), df.index.max().date())
        return out

    # ------------------------------------------------------------------
    # 面板装配
    # ------------------------------------------------------------------
    def load(
        self,
        start: str | None = None,
        end: str | None = None,
        symbols: list[str] | None = None,
        min_obs: int = 250,
        require_start: str | None = None,
        quality_filter: bool = True,
    ) -> MarketPanel:
        """装配面板。缺失行情前向填充仅用于停牌期估值，收益计算会剔除。

        require_start: 只保留在该日期之前已有行情的标的（剔除数据不完整/新上市
        无法覆盖研究区间的样本，避免"伪 IPO"污染股票池）。
        """
        if self.universe is None:
            self.ensure_universe()
        assert self.universe is not None
        uni = self.universe
        if symbols is None:
            symbols = list(uni["symbol"])
        names = dict(zip(uni["symbol"], uni["name"]))

        frames: dict[str, dict[str, pd.Series]] = {}
        dropped: list[str] = []
        for sym in symbols:
            df = cache.load_prices(sym)
            if df is None or len(df) < min_obs:
                dropped.append(sym)
                continue
            if require_start is not None and df.index[0] > pd.Timestamp(require_start):
                dropped.append(sym)
                continue
            frames[sym] = {c: df[c] for c in ("open", "high", "low", "close", "volume")}
        if dropped:
            log.warning("剔除数据不完整(<%d 行或起始日晚于 %s)的标的 %d 只",
                        min_obs, require_start, len(dropped))

        # 数据质量体检：剔除存在异常跳价（复权错误）的标的
        if quality_filter and frames:
            from qlab.data import quality

            audit = quality.audit({s: pd.DataFrame(f) for s, f in frames.items()})
            bad = set(audit.loc[~audit["ok"], "symbol"])
            if bad:
                for sym in bad:
                    frames.pop(sym, None)
                    dropped.append(sym)
                log.warning("数据质量过滤后剩余 %d 只", len(frames))
        if not frames:
            raise RuntimeError("没有可用的本地行情缓存，请先运行 download_data")

        panel = {}
        for field_name in ("open", "high", "low", "close", "volume"):
            data = {sym: f[field_name] for sym, f in frames.items()}
            panel[field_name] = pd.DataFrame(data).sort_index()

        # 交易日历 = 所有标的有行情的日期并集
        bench_sym = self.cfg.benchmark
        bench = cache.load_prices(bench_sym)
        bench_name = self.cfg.benchmark_name
        if bench is None:
            log.warning("缺少基准 %s 缓存，改用股票池等权净值", bench_sym)
            # 明确标注为回落基准，避免报告里把等权净值当成沪深 300
            bench_name = f"{self.cfg.benchmark_name}（缓存缺失，回落为股票池等权）"
            bench = pd.DataFrame(index=panel["close"].index)

        index = panel["close"].index.union(bench.index)
        panel = {k: v.reindex(index) for k, v in panel.items()}
        # 停牌估值用前收盘，但 open 缺失保持 NaN 以便回测禁止成交
        panel["close"] = panel["close"].ffill()
        panel["high"] = panel["high"].ffill()
        panel["low"] = panel["low"].ffill()

        bench_close = (
            bench["close"].reindex(index).ffill()
            if not bench.empty
            else panel["close"].mean(axis=1)
        )

        extras: dict[str, pd.Series] = {}
        for sym in getattr(self.cfg, "extra_benchmarks", {}):
            eb = cache.load_prices(sym)
            if eb is not None and not eb.empty:
                extras[sym] = eb["close"].reindex(index).ffill()

        market = MarketPanel(
            open=panel["open"],
            high=panel["high"],
            low=panel["low"],
            close=panel["close"],
            volume=panel["volume"],
            benchmark=bench_close,
            benchmark_name=bench_name,
            names={s: names.get(s, s) for s in panel["close"].columns},
            extra_benchmarks=extras,
        )
        if start or end:
            market = market.slice(start, end)
        log.info("面板装配完成：%d 个交易日 × %d 只标的（%s ~ %s）",
                 len(market.dates), len(market.symbols),
                 market.dates[0].date(), market.dates[-1].date())
        return market
