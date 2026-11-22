"""回测层：组合记账、撮合、交易成本。

关键约定（防止前视偏差）：
    t 日收盘生成目标权重 → t+1 日开盘价撮合。
引擎不含任何「用当天收盘价成交」的捷径，因此净值是可直接对外汇报的。
"""

from qlab.backtest.engine import BacktestResult, PortfolioBacktester
from qlab.backtest.broker import BrokerCosts

__all__ = ["PortfolioBacktester", "BacktestResult", "BrokerCosts"]
