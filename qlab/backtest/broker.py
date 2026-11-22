"""交易成本模型：佣金、印花税、滑点、最小交易单位。"""

from __future__ import annotations

from dataclasses import dataclass

from qlab.config import BacktestConfig


@dataclass
class Fill:
    """一笔成交的费用明细。"""

    notional: float          # 成交金额（不含费用）
    commission: float        # 佣金
    stamp_duty: float        # 印花税（仅卖出）
    slippage_cost: float     # 滑点成本（相对开盘价）

    @property
    def total_fee(self) -> float:
        return self.commission + self.stamp_duty

    @property
    def cash_delta(self) -> float:
        """对现金的影响（买入为负，卖出为正由调用方决定符号）。"""
        return self.notional + self.total_fee


class BrokerCosts:
    """按 A 股实际规则计算交易成本。

    - 佣金：双边，万 2.5，单笔最低 5 元
    - 印花税：仅卖出，2023-08-28 前千 1，之后千 0.5（随时间切换）
    - 滑点：单边 5bp，买入抬价 / 卖出压价
    - 最小交易单位：100 股
    """

    def __init__(self, cfg: BacktestConfig | None = None):
        self.cfg = cfg or BacktestConfig()

    def commission(self, notional: float) -> float:
        return max(self.min_commission, notional * self.cfg.commission_rate)

    @property
    def min_commission(self) -> float:
        return self.cfg.min_commission

    def stamp_duty(self, notional: float, date) -> float:
        return notional * self.cfg.stamp_duty(date)

    def buy_price(self, open_price: float) -> float:
        return open_price * (1.0 + self.cfg.slippage)

    def sell_price(self, open_price: float) -> float:
        return open_price * (1.0 - self.cfg.slippage)

    def fill_buy(self, open_price: float, shares: int, date) -> Fill:
        px = self.buy_price(open_price)
        notional = px * shares
        return Fill(
            notional=notional,
            commission=self.commission(notional),
            stamp_duty=0.0,
            slippage_cost=open_price * self.cfg.slippage * shares,
        )

    def fill_sell(self, open_price: float, shares: int, date) -> Fill:
        px = self.sell_price(open_price)
        notional = px * shares
        return Fill(
            notional=notional,
            commission=self.commission(notional),
            stamp_duty=self.stamp_duty(notional, date),
            slippage_cost=open_price * self.cfg.slippage * shares,
        )

    def round_lot(self, shares: float) -> int:
        """向下取整到 100 股整数倍。"""
        lot = self.cfg.lot_size
        return int(max(0, shares) // lot * lot)
