"""组合回测引擎。

设计：单账户、多标的、按目标权重调仓的日频回测。
    · 信号日 t 收盘 → 撮合日 t+1 开盘（严格防止用当天收盘价成交）
    · 先卖后买，现金不足时按比例缩减买单
    · 停牌（当日无开盘价）标的跳过调仓，保留原持仓
    · 每日按收盘价市值结算，得到净值曲线
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from qlab.backtest.broker import BrokerCosts, Fill
from qlab.config import BacktestConfig
from qlab.data.loader import MarketPanel
from qlab.logging_utils import get_logger

log = get_logger("qlab.backtest")


@dataclass
class BacktestResult:
    """回测输出。"""

    equity: pd.Series                 # 组合总市值（元）
    nav: pd.Series                    # 归一化净值（首日 = 1.0）
    benchmark_nav: pd.Series          # 基准归一化净值（首日 = 1.0）
    benchmark_name: str
    exposure: pd.Series               # 股票仓位占比
    n_holdings: pd.Series             # 持仓只数
    cash: pd.Series                   # 现金
    trades: pd.DataFrame              # 成交流水
    targets: pd.DataFrame             # 目标权重（信号日）
    rebalance_dates: list[pd.Timestamp] = field(default_factory=list)
    extra_benchmarks: dict[str, pd.Series] = field(default_factory=dict)
    config: dict = field(default_factory=dict)

    # -- 派生 ------------------------------------------------------------
    @property
    def returns(self) -> pd.Series:
        return self.nav.pct_change(fill_method=None).fillna(0.0)

    @property
    def benchmark_returns(self) -> pd.Series:
        return self.benchmark_nav.pct_change(fill_method=None).fillna(0.0)

    @property
    def drawdown(self) -> pd.Series:
        return self.nav / self.nav.cummax() - 1.0

    @property
    def total_cost(self) -> float:
        if self.trades.empty:
            return 0.0
        return float(self.trades["total_fee"].sum() + self.trades["slippage_cost"].sum())

    def segment(self, start: str | None = None, end: str | None = None) -> "BacktestResult":
        """切出子区间（净值重新以区间首日为 1.0 归一）。"""
        idx = self.nav.index
        mask = pd.Series(True, index=idx)
        if start:
            mask &= idx >= pd.Timestamp(start)
        if end:
            mask &= idx <= pd.Timestamp(end)
        sub = idx[mask]
        if len(sub) == 0:
            raise ValueError("子区间为空")
        nav = self.nav.loc[sub] / self.nav.loc[sub].iloc[0]
        bench = self.benchmark_nav.loc[sub] / self.benchmark_nav.loc[sub].iloc[0]
        trades = (
            self.trades[
                (pd.to_datetime(self.trades["date"]) >= sub[0])
                & (pd.to_datetime(self.trades["date"]) <= sub[-1])
            ]
            if not self.trades.empty
            else self.trades
        )
        return BacktestResult(
            equity=self.equity.loc[sub],
            nav=nav,
            benchmark_nav=bench,
            benchmark_name=self.benchmark_name,
            exposure=self.exposure.loc[sub],
            n_holdings=self.n_holdings.loc[sub],
            cash=self.cash.loc[sub],
            trades=trades.reset_index(drop=True),
            targets=self.targets.loc[
                self.targets.index[(self.targets.index >= sub[0]) & (self.targets.index <= sub[-1])]
            ] if not self.targets.empty else self.targets,
            rebalance_dates=[d for d in self.rebalance_dates if sub[0] <= d <= sub[-1]],
            extra_benchmarks={
                k: (v.loc[sub] / v.loc[sub].iloc[0]) for k, v in self.extra_benchmarks.items()
            },
            config=self.config,
        )

    def monthly_returns(self) -> pd.DataFrame:
        """年 × 月的收益率矩阵（%）。"""
        nav = self.nav
        me = nav.resample("ME").last()
        first = pd.Series([nav.iloc[0]], index=[nav.index[0] - pd.offsets.MonthEnd(0)])
        combined = pd.concat([first, me])
        rets = combined.pct_change(fill_method=None).dropna()
        out = pd.DataFrame(
            {"year": rets.index.year, "month": rets.index.month, "ret": rets.to_numpy()}
        )
        table = out.pivot_table(index="year", columns="month", values="ret", aggfunc="last")
        return (table * 100.0).round(2)


class PortfolioBacktester:
    """日频组合回测引擎。"""

    def __init__(self, cfg: BacktestConfig | None = None):
        self.cfg = cfg or BacktestConfig()
        self.costs = BrokerCosts(self.cfg)

    # ------------------------------------------------------------------
    def _execution_map(self, dates: pd.DatetimeIndex,
                       rebalance_dates: list[pd.Timestamp]) -> dict[pd.Timestamp, pd.Timestamp]:
        """信号日 → 下一个交易日（撮合日）。"""
        mapping: dict[pd.Timestamp, pd.Timestamp] = {}
        pos = {d: i for i, d in enumerate(dates)}
        for sig in rebalance_dates:
            i = pos.get(sig)
            if i is None:
                # 信号日不是交易日（理论上不会发生），取其后第一个交易日
                later = dates[dates > sig]
                if len(later) == 0:
                    continue
                mapping[sig] = later[0]
            elif i + 1 < len(dates):
                mapping[sig] = dates[i + 1]
        return mapping

    # ------------------------------------------------------------------
    def run(self, panel: MarketPanel, targets: pd.DataFrame) -> BacktestResult:
        """执行回测。

        参数
        ----
        panel : MarketPanel   行情面板（已按回测区间切片）
        targets : DataFrame   index=信号日, columns=股票, values=目标权重（0 表示不持有）
        """
        dates = panel.dates
        if len(dates) < 3:
            raise ValueError("回测区间太短")
        open_px = panel.open
        close_px = panel.close
        names = panel.names

        rebalance_dates = [d for d in targets.index if d in set(dates)]
        exec_map = self._execution_map(dates, rebalance_dates)
        # 反向索引：撮合日 -> 信号日
        exec_days = {v: k for k, v in exec_map.items()}

        cash = float(self.cfg.initial_cash)
        shares: dict[str, int] = {}
        last_price: dict[str, float] = {}

        equity_curve, cash_curve, exposure_curve, holdings_curve = [], [], [], []
        trades: list[dict] = []
        turnover_curve: list[float] = []
        skipped = 0

        symbols = list(targets.columns) if not targets.empty else []

        for day in dates:
            # ---------------- 撮合（开盘） ----------------
            if day in exec_days:
                signal_day = exec_days[day]
                weights = targets.loc[signal_day]
                cash, shares, day_trades, day_turnover, n_skip = self._rebalance(
                    day, cash, shares, weights, open_px.loc[day], close_px.loc[day],
                    last_price, names,
                )
                trades.extend(day_trades)
                turnover_curve.append(day_turnover)
                skipped += n_skip

            # ---------------- 更新最新价（收盘） ----------------
            px_today = close_px.loc[day]
            for sym in list(shares):
                v = px_today.get(sym, np.nan)
                if isinstance(v, float) and not np.isnan(v):
                    last_price[sym] = float(v)
                elif sym in last_price:
                    pass  # 停牌：沿用最近有效收盘价

            # ---------------- 结算 ----------------
            pos_value = 0.0
            for sym, qty in shares.items():
                if qty <= 0:
                    continue
                pos_value += qty * last_price.get(sym, 0.0)
            equity = cash + pos_value
            equity_curve.append(equity)
            cash_curve.append(cash)
            exposure_curve.append(pos_value / equity if equity > 0 else 0.0)
            holdings_curve.append(sum(1 for q in shares.values() if q > 0))

        equity = pd.Series(equity_curve, index=dates, name="equity")
        nav = equity / equity.iloc[0]
        bench = panel.benchmark.reindex(dates).ffill()
        bench_nav = bench / bench.iloc[0]

        trades_df = pd.DataFrame(trades)
        if not trades_df.empty:
            # 同一天先卖后买，成交明细按真实执行顺序排列
            side_order = trades_df["side"].map({"SELL": 0, "BUY": 1}).fillna(2)
            trades_df = (
                trades_df.assign(_order=side_order)
                .sort_values(["date", "_order"])
                .drop(columns="_order")
                .reset_index(drop=True)
            )

        if skipped:
            log.info("调仓期间因停牌跳过 %d 笔标的调整", skipped)
        log.info("回测完成：%s ~ %s，净值 %.4f，交易 %d 笔，费用合计 %.0f 元",
                 dates[0].date(), dates[-1].date(), nav.iloc[-1],
                 len(trades_df), trades_df["total_fee"].sum() if not trades_df.empty else 0.0)

        return BacktestResult(
            equity=equity,
            nav=nav,
            benchmark_nav=bench_nav,
            benchmark_name=panel.benchmark_name,
            exposure=pd.Series(exposure_curve, index=dates),
            n_holdings=pd.Series(holdings_curve, index=dates),
            cash=pd.Series(cash_curve, index=dates),
            trades=trades_df,
            targets=targets,
            rebalance_dates=rebalance_dates,
            extra_benchmarks={
                k: (v.reindex(dates).ffill() / v.reindex(dates).ffill().iloc[0])
                for k, v in panel.extra_benchmarks.items()
            },
            config={
                "initial_cash": self.cfg.initial_cash,
                "start": str(dates[0].date()),
                "end": str(dates[-1].date()),
                "commission_rate": self.cfg.commission_rate,
                "min_commission": self.cfg.min_commission,
                "slippage": self.cfg.slippage,
                "lot_size": self.cfg.lot_size,
                "stamp_duty_schedule": self.cfg.stamp_duty_schedule,
                "n_symbols": len(symbols),
            },
        )

    # ------------------------------------------------------------------
    def _rebalance(
        self,
        day: pd.Timestamp,
        cash: float,
        shares: dict[str, int],
        weights: pd.Series,
        open_px: pd.Series,
        close_px: pd.Series,
        last_price: dict[str, float],
        names: dict[str, str],
    ) -> tuple[float, dict[str, int], list[dict], float, int]:
        """单次调仓：把持仓调整为 ``weights`` 指定的目标权重。"""
        # 1) 估值口径：优先开盘价，停牌用最近收盘价
        price_now: dict[str, float] = {}
        for sym in set(list(shares) + list(weights.index)):
            p = open_px.get(sym, np.nan)
            if isinstance(p, (int, float, np.floating)) and not np.isnan(p) and p > 0:
                price_now[sym] = float(p)
            else:
                p2 = last_price.get(sym, close_px.get(sym, np.nan))
                if isinstance(p2, (int, float, np.floating)) and not np.isnan(p2) and p2 > 0:
                    price_now[sym] = float(p2)

        pos_value = sum(q * price_now.get(s, 0.0) for s, q in shares.items())
        equity = cash + pos_value
        if equity <= 0:
            return cash, shares, [], 0.0, 0

        # 2) 目标股数（买入向下取整到整手）
        tradable: dict[str, float] = {}
        target_shares: dict[str, int] = {}
        for sym, w in weights.items():
            p = price_now.get(sym, np.nan)
            if not (isinstance(p, (int, float, np.floating)) and p > 0):
                continue
            tradable[sym] = float(p)
            w = float(w)
            if w <= 0:
                target_shares[sym] = 0
            else:
                budget = equity * w
                target_shares[sym] = self.costs.round_lot(budget / p)

        # 3) 卖出（先释放现金）
        trade_list: list[dict] = []
        total_turnover_value = 0.0
        for sym in list(shares):
            want = target_shares.get(sym, 0)  # 不在目标组合中则清零
            cur = shares[sym]
            if cur <= want or sym not in tradable:
                continue
            qty = cur - want
            fill = self.costs.fill_sell(tradable[sym], qty, day)
            cash += fill.notional - fill.total_fee
            shares[sym] = cur - qty
            if shares[sym] == 0:
                shares.pop(sym, None)
            total_turnover_value += fill.notional
            trade_list.append(
                self._trade_row(day, sym, names, "SELL", qty, tradable[sym],
                                self.costs.sell_price(tradable[sym]), fill, cash)
            )

        # 4) 买入（按可用现金等比缩减）
        buys = []
        for sym, want in target_shares.items():
            cur = shares.get(sym, 0)
            if want > cur and sym in tradable:
                buys.append((sym, want - cur))
        if buys:
            def est_cost(sym: str, qty: int) -> float:
                p = self.costs.buy_price(tradable[sym])
                notional = p * qty
                return notional + self.costs.commission(notional)

            need = sum(est_cost(s, q) for s, q in buys)
            avail = cash * 0.999  # 留一点余量吸收费用误差
            scale = min(1.0, avail / need) if need > 0 else 1.0
            for sym, qty in buys:
                adj = self.costs.round_lot(qty * scale)
                while adj > 0 and est_cost(sym, adj) > cash:
                    adj -= self.cfg.lot_size
                if adj <= 0:
                    continue
                fill = self.costs.fill_buy(tradable[sym], adj, day)
                cash -= fill.notional + fill.total_fee
                shares[sym] = shares.get(sym, 0) + adj
                total_turnover_value += fill.notional
                trade_list.append(
                    self._trade_row(day, sym, names, "BUY", adj, tradable[sym],
                                    self.costs.buy_price(tradable[sym]), fill, cash)
                )

        # 5) 清理空仓、记录换手
        shares = {s: q for s, q in shares.items() if q > 0}
        turnover = total_turnover_value / equity if equity > 0 else 0.0
        return cash, shares, trade_list, turnover, 0

    # ------------------------------------------------------------------
    @staticmethod
    def _trade_row(day, sym, names, side, qty, open_px, exec_px, fill: Fill, cash_after) -> dict:
        return {
            "date": day,
            "symbol": sym,
            "name": names.get(sym, sym),
            "side": side,
            "shares": int(qty),
            "open_price": float(open_px),
            "price": float(exec_px),
            "notional": float(fill.notional),
            "commission": float(fill.commission),
            "stamp_duty": float(fill.stamp_duty),
            "total_fee": float(fill.total_fee),
            "slippage_cost": float(fill.slippage_cost),
            "cash_after": float(cash_after),
        }
