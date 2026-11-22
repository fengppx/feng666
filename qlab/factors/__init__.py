"""计算层：因子库、横截面预处理、IC 分析。"""

from qlab.factors.analysis import (
    forward_returns,
    ic_series,
    ic_summary,
    quantile_returns,
    select_factors,
)
from qlab.factors.library import FACTOR_FUNCS, compute_factor, compute_factors
from qlab.factors.preprocessing import rank_cs, winsorize_cs, zscore_cs

__all__ = [
    "FACTOR_FUNCS",
    "compute_factor",
    "compute_factors",
    "winsorize_cs",
    "zscore_cs",
    "rank_cs",
    "forward_returns",
    "ic_series",
    "ic_summary",
    "quantile_returns",
    "select_factors",
]
