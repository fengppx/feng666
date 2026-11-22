"""全局配置：路径、回测参数、交易成本、因子候选池。

所有可调参数集中在此，命令行参数可覆盖，便于复现实验。
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# 路径
# --------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
OUTPUT_DIR = PROJECT_ROOT / "output"
FIGURE_DIR = OUTPUT_DIR / "figures"
TABLE_DIR = OUTPUT_DIR / "tables"
REPORT_DIR = OUTPUT_DIR / "report"
LOG_DIR = OUTPUT_DIR / "logs"

for _p in (RAW_DIR, PROCESSED_DIR, FIGURE_DIR, TABLE_DIR, REPORT_DIR, LOG_DIR):
    _p.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# 数据抓取参数
# --------------------------------------------------------------------------
@dataclass
class DataConfig:
    """数据层配置。"""

    # 抓取区间：因子需要 12 个月预热，故比回测起点早 1.5 年
    fetch_start: str = "2017-06-01"
    fetch_end: str | None = None  # None 表示抓取到最近交易日
    # 股票池：按总市值从大到小取前 N 只 A 股（近似沪深 300 大市值样本）
    universe_size: int = 300
    # 基准指数（腾讯代码）
    benchmark: str = "sh000300"       # 沪深 300
    benchmark_name: str = "沪深300"
    extra_benchmarks: dict[str, str] = field(
        default_factory=lambda: {"sh000905": "中证500"}
    )
    # 价格口径：day 不复权（默认，与沪深300价格指数同口径）
    # / hfq 后复权 / qfq 前复权（仅腾讯源支持）
    adjust: str = "day"
    provider: str = "sina"            # 默认数据源：sina（一次请求返回全历史）
    request_workers: int = 6          # 并发线程数（对数据站友好，勿过大）
    request_retry: int = 3
    request_pause: float = 0.12       # 每次请求后的强制间隔（秒）


# --------------------------------------------------------------------------
# 回测参数
# --------------------------------------------------------------------------
@dataclass
class BacktestConfig:
    """回测层配置。"""

    start: str = "2019-01-01"
    end: str | None = None            # None 表示数据末端
    is_end: str = "2022-12-31"        # 样本内结束日；之后为样本外

    initial_cash: float = 1_000_000.0
    # 组合构建
    top_n: int = 30                   # 持仓只数（等权）
    rebalance: str = "ME"             # 月末调仓（ME = month end）
    # 交易成本（A 股实际规则）
    commission_rate: float = 0.00025  # 佣金 万 2.5（双边）
    min_commission: float = 5.0       # 单笔最低 5 元
    slippage: float = 0.0005          # 单边滑点 5bp
    lot_size: int = 100               # 一手 100 股
    # 印花税：2023-08-28 起由千 1 降至千 0.5（仅卖出收取）
    stamp_duty_schedule: tuple[tuple[str, float], ...] = (
        ("1990-01-01", 0.001),
        ("2023-08-28", 0.0005),
    )
    # 无风险利率（年化），用于夏普/索提诺
    risk_free_rate: float = 0.015
    trading_days: int = 252

    def stamp_duty(self, date) -> float:
        """按日期返回当期卖出印花税率。"""
        rate = 0.0
        d = str(date)[:10]
        for start, r in self.stamp_duty_schedule:
            if d >= start:
                rate = r
        return rate


# --------------------------------------------------------------------------
# 因子与策略参数
# --------------------------------------------------------------------------
# 候选因子池：名称 -> (构造参数, 方向)
#   方向 +1 表示因子值越大越好，-1 表示越小越好（用于 z-score 取符号）
FACTOR_POOL: dict[str, dict[str, Any]] = {
    "mom_12_1": {"family": "momentum", "lookback": 252, "skip": 21, "direction": 1,
                 "desc": "12 个月动量（跳过最近 1 个月）"},
    "mom_6_1": {"family": "momentum", "lookback": 126, "skip": 21, "direction": 1,
                "desc": "6 个月动量（跳过最近 1 个月）"},
    "mom_1": {"family": "momentum", "lookback": 21, "skip": 0, "direction": -1,
              "desc": "1 个月反转"},
    "rev_5": {"family": "reversal", "lookback": 5, "skip": 0, "direction": -1,
              "desc": "5 日短期反转"},
    "vol_20": {"family": "volatility", "lookback": 20, "direction": -1,
               "desc": "20 日已实现波动率（低波异象）"},
    "vol_60": {"family": "volatility", "lookback": 60, "direction": -1,
               "desc": "60 日已实现波动率"},
    "max_ret_20": {"family": "lottery", "lookback": 20, "direction": -1,
                   "desc": "20 日最大单日涨幅（彩票偏好）"},
    "turn_20": {"family": "liquidity", "lookback": 20, "direction": -1,
                "desc": "20 日平均换手（成交额代理，流动性/拥挤度）"},
    "bias_60": {"family": "trend", "lookback": 60, "direction": -1,
                "desc": "收盘价相对 60 日均线偏离度"},
    "rsi_14": {"family": "trend", "lookback": 14, "direction": -1,
               "desc": "14 日 RSI（超买反转）"},
    "hl_range_20": {"family": "volatility", "lookback": 20, "direction": -1,
                    "desc": "20 日平均振幅（高低价差/收盘）"},
    "skew_60": {"family": "higher_moment", "lookback": 60, "direction": -1,
                "desc": "60 日收益偏度"},
}


@dataclass
class StrategyConfig:
    """策略层配置：因子选择、合成方式、择时规则。"""

    # 样本内 IC 筛选：保留 |ICIR| 最大的前 k 个因子
    factor_select_k: int = 5
    min_abs_icir: float = 0.15
    max_factor_corr: float = 0.75     # 因子间最大允许相关（去共线）
    # 因子权重：equal = 等权 z-score；icir = 按样本内 ICIR 加权
    weighting: str = "equal"
    # 单因子极值处理
    winsorize_quantile: float = 0.02  # 双侧各截断 2%
    # 组合约束
    max_weight: float = 0.08          # 单票权重上限（1/30≈3.3%，上限用于约束）
    # 择时：指数收盘价 vs N 日均线
    timing_enabled: bool = True
    timing_ma_window: int = 120
    timing_low_exposure: float = 0.30  # 指数在均线下方时的仓位系数
    timing_high_exposure: float = 1.00

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class AppConfig:
    data: DataConfig = field(default_factory=DataConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)

    def to_json(self, path: Path) -> None:
        payload = {
            "data": dataclasses.asdict(self.data),
            "backtest": dataclasses.asdict(self.backtest),
            "strategy": dataclasses.asdict(self.strategy),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


DEFAULT_CONFIG = AppConfig()
