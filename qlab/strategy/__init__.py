"""策略层：把因子打分转换成可回测的目标权重。"""

from qlab.strategy.multifactor import MultiFactorStrategy
from qlab.strategy.timing import trend_exposure

__all__ = ["MultiFactorStrategy", "trend_exposure"]
