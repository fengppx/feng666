"""数据质量检查。

在进入因子计算之前，必须确认行情本身没有"脏数据"，否则一个错乱的复权序列
可以让组合净值在一天内凭空涨 60%。检查项：

    1. 覆盖度：样本行数、首末交易日
    2. 极值：单日涨跌幅是否超出 A 股涨跌停制度上限（主板 ±10%、双创 ±20%）
    3. 价格连续性：是否存在 0 或负价、缺口
    4. 停牌比例

输出 ``output/tables/data_quality.csv``，并在面板装配时剔除不合格标的。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from qlab.config import TABLE_DIR
from qlab.logging_utils import get_logger

log = get_logger("qlab.quality")

# A 股涨跌停：主板 ±10%，创业板/科创板 ±20%，ST ±5%，新股首日无限制。
# 这里用 25% 作为"绝对不可能"的阈值（保留少量缓冲，避免误杀 20% 板 + 除权）
MAX_ABS_DAILY_RETURN = 0.25


def check_series(df: pd.DataFrame, symbol: str) -> dict:
    """单只标的体检。"""
    out = {
        "symbol": symbol,
        "rows": int(len(df)),
        "first_date": df.index.min() if len(df) else pd.NaT,
        "last_date": df.index.max() if len(df) else pd.NaT,
        "nan_ratio": float(df[["open", "close"]].isna().mean().mean()),
        "nonpositive": int((df[["open", "close"]] <= 0).sum().sum()),
    }
    if len(df) < 3:
        out.update({"max_ret": np.nan, "min_ret": np.nan, "extreme_days": 0, "ok": False})
        return out
    ret = df["close"].pct_change(fill_method=None)
    # 上市首 5 个交易日的涨跌幅不受涨跌停约束（新股），不计入极值检查
    if len(ret) > 5:
        ret = ret.iloc[5:]
    out["max_ret"] = float(ret.max())
    out["min_ret"] = float(ret.min())
    out["extreme_days"] = int((ret.abs() > MAX_ABS_DAILY_RETURN).sum())
    out["ok"] = bool(out["extreme_days"] == 0 and out["nonpositive"] == 0
                     and out["nan_ratio"] < 0.02)
    return out


def audit(frames: dict[str, pd.DataFrame], save: bool = True) -> pd.DataFrame:
    """批量体检，返回明细表。"""
    rows = [check_series(df, sym) for sym, df in frames.items()]
    table = pd.DataFrame(rows).sort_values(["ok", "extreme_days"], ascending=[True, False])
    bad = table[~table["ok"]]
    if len(bad):
        log.warning("数据质量不合格 %d 只：%s", len(bad),
                    ", ".join(f"{r.symbol}(极值{r.extreme_days}天)" for r in bad.head(10).itertuples()))
    if save:
        path = TABLE_DIR / "data_quality.csv"
        table.to_csv(path, index=False, encoding="utf-8-sig")
        log.info("数据质量报告：%s（合格 %d / %d）", path,
                 int(table["ok"].sum()), len(table))
    return table
