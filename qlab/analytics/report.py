"""结果输出：CSV / Excel / JSON / Markdown。

一次回测产出四类文件，分别服务于：报告写作（csv/md）、答辩展示（xlsx/json）、
Web 看板（json）。
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from qlab.analytics.metrics import DEFAULT_SEGMENTS, KEY_METRICS, perf_metrics
from qlab.config import TABLE_DIR
from qlab.logging_utils import get_logger

log = get_logger("qlab.report")


def _jsonable(obj):
    """把 numpy / pandas 类型转成原生类型，便于写 JSON。"""
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return None if np.isnan(obj) else float(obj)
    if isinstance(obj, (pd.Timestamp,)):
        return obj.strftime("%Y-%m-%d")
    if isinstance(obj, float) and np.isnan(obj):
        return None
    return obj


class ResultExporter:
    """把一次回测的全部产物落盘。"""

    def __init__(self, out_dir: str | Path = TABLE_DIR):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    def metrics_table(self, result, rf: float = 0.015) -> pd.DataFrame:
        """全样本 / 样本内 / 样本外的完整指标矩阵（列=区间）。"""
        rows = {}
        for seg in DEFAULT_SEGMENTS:
            try:
                sub = result.segment(seg.start, seg.end)
            except ValueError:
                continue
            rows[seg.label] = perf_metrics(
                sub.nav, sub.benchmark_nav, rf=rf, trades=sub.trades,
                avg_equity=float(sub.equity.mean()),
            )
        table = pd.DataFrame(rows)
        return table

    # ------------------------------------------------------------------
    def save_metrics(self, result, rf: float = 0.015) -> pd.DataFrame:
        table = self.metrics_table(result, rf)
        table.to_csv(self.out_dir / "metrics_by_segment.csv", encoding="utf-8-sig")

        # 样本内/外对比（策略 vs 基准），供报告正文引用
        compare_rows = []
        for metric in KEY_METRICS:
            for label in table.columns:
                compare_rows.append({"区间": label, "指标": metric, "策略": table.loc[metric, label]})
        pd.DataFrame(compare_rows).to_csv(
            self.out_dir / "metrics_long.csv", index=False, encoding="utf-8-sig"
        )
        return table

    # ------------------------------------------------------------------
    def save_series(self, result) -> pd.DataFrame:
        df = pd.DataFrame({
            "nav": result.nav,
            "benchmark_nav": result.benchmark_nav,
            "drawdown": result.nav / result.nav.cummax() - 1.0,
            "exposure": result.exposure,
            "n_holdings": result.n_holdings,
            "equity": result.equity,
            "cash": result.cash,
        })
        df.index.name = "date"
        df.round(6).to_csv(self.out_dir / "nav_daily.csv", encoding="utf-8-sig")
        return df

    # ------------------------------------------------------------------
    def save_trades(self, result) -> pd.DataFrame:
        trades = result.trades.copy()
        if not trades.empty:
            trades["date"] = pd.to_datetime(trades["date"]).dt.strftime("%Y-%m-%d")
            for c in ("notional", "commission", "stamp_duty", "total_fee", "slippage_cost"):
                trades[c] = trades[c].round(2)
            trades.to_csv(self.out_dir / "trades.csv", index=False, encoding="utf-8-sig")
        return trades

    # ------------------------------------------------------------------
    def save_monthly(self, result) -> pd.DataFrame:
        monthly = result.monthly_returns()
        monthly.to_csv(self.out_dir / "monthly_returns.csv", encoding="utf-8-sig")
        return monthly

    # ------------------------------------------------------------------
    def save_factor_table(self, strategy) -> pd.DataFrame:
        table = strategy.ic_table.copy()
        if table.empty:
            return table
        table.to_csv(self.out_dir / "factor_ic.csv", index=False, encoding="utf-8-sig")
        specs = pd.DataFrame([s.as_dict() for s in strategy.specs])
        specs.to_csv(self.out_dir / "factor_selected.csv", index=False, encoding="utf-8-sig")
        return table

    # ------------------------------------------------------------------
    def save_excel(self, result, strategy, rf: float = 0.015) -> Path:
        """多 sheet 工作簿：指标 / 净值 / 成交 / 月度 / 因子。"""
        path = self.out_dir / "backtest_result.xlsx"
        metrics = self.metrics_table(result, rf).round(4)
        nav = pd.DataFrame({
            "策略净值": result.nav, "基准净值": result.benchmark_nav,
            "策略回撤": result.nav / result.nav.cummax() - 1.0,
            "仓位": result.exposure, "持仓数": result.n_holdings,
        })
        nav.index = nav.index.strftime("%Y-%m-%d")
        specs = pd.DataFrame([s.as_dict() for s in strategy.specs])
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            metrics.to_excel(writer, sheet_name="指标")
            nav.round(4).to_excel(writer, sheet_name="净值")
            (result.trades if not result.trades.empty else pd.DataFrame()).to_excel(
                writer, sheet_name="成交", index=False
            )
            result.monthly_returns().to_excel(writer, sheet_name="月度收益")
            strategy.ic_table.round(4).to_excel(writer, sheet_name="因子IC", index=False)
            specs.to_excel(writer, sheet_name="入选因子", index=False)
        return path

    # ------------------------------------------------------------------
    def save_summary_json(self, result, strategy, panel_meta: dict,
                          rf: float = 0.015) -> Path:
        """Web 看板与报告共用的结构化结果。"""
        metrics = {}
        for seg in DEFAULT_SEGMENTS:
            try:
                sub = result.segment(seg.start, seg.end)
            except ValueError:
                continue
            metrics[seg.label] = _jsonable(
                perf_metrics(sub.nav, sub.benchmark_nav, rf=rf, trades=sub.trades,
                             avg_equity=float(sub.equity.mean()))
            )

        nav = result.nav
        bench = result.benchmark_nav.reindex(nav.index).ffill()
        bench = bench / bench.dropna().iloc[0]
        step = max(1, len(nav) // 1200)
        idx = nav.index[::step]

        monthly = result.monthly_returns()
        trades = result.trades
        recent_trades = (
            trades.tail(120).assign(date=lambda d: pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d"))
            if not trades.empty else pd.DataFrame()
        )

        # 最新持仓：用最后一期目标权重 + 名称
        holdings = []
        if not result.targets.empty:
            last = result.targets.iloc[-1]
            picks = last[last > 0].sort_values(ascending=False)
            holdings = [
                {"symbol": s, "name": (panel_meta.get("names") or {}).get(s, s),
                 "weight": float(w)}
                for s, w in picks.items()
            ]

        payload = {
            "meta": _jsonable(panel_meta),
            "config": _jsonable(result.config),
            "metrics": metrics,
            "series": {
                "dates": [d.strftime("%Y-%m-%d") for d in idx],
                "nav": [float(v) for v in nav.loc[idx]],
                "benchmark": [float(v) for v in bench.loc[idx]],
                "drawdown": [float(v) for v in (nav.loc[idx] / nav.loc[idx].cummax() - 1.0)],
                "exposure": [float(v) for v in result.exposure.loc[idx]],
            },
            "monthly": {
                "years": [int(y) for y in monthly.index],
                "matrix": [[None if pd.isna(v) else float(v) for v in row]
                           for row in monthly.to_numpy()],
            },
            "factors": _jsonable(strategy.ic_table.to_dict(orient="records")),
            "selected": [s.as_dict() for s in strategy.specs],
            "trades": _jsonable(recent_trades.to_dict(orient="records")),
            "holdings": holdings,
            "rebalance_dates": [d.strftime("%Y-%m-%d") for d in result.rebalance_dates],
        }
        path = self.out_dir / "result_summary.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        log.info("结构化结果写入 %s（%.0f KB）", path, path.stat().st_size / 1024)
        return path

    # ------------------------------------------------------------------
    def save_markdown(self, result, strategy, metrics: pd.DataFrame) -> Path:
        """生成 Markdown 摘要，方便直接贴进 README / 报告。"""
        def pct(v, d=2):
            if isinstance(v, str):
                return v
            return "—" if pd.isna(v) else f"{v * 100:.{d}f}%"

        lines = ["# 回测结果摘要", ""]
        lines.append(f"回测区间：{result.nav.index[0]:%Y-%m-%d} ~ {result.nav.index[-1]:%Y-%m-%d}，"
                     f"基准：{result.benchmark_name}")
        lines.append("")
        lines.append("## 分区间指标")
        lines.append("")
        header = "| 指标 | " + " | ".join(metrics.columns) + " |"
        lines.append(header)
        lines.append("|" + "---|" * (len(metrics.columns) + 1))
        ratio_like = {
            "夏普比率", "索提诺比率", "卡玛比率", "信息比率", "基准夏普比率",
            "Beta", "上行捕获", "下行捕获", "年化换手率", "相关系数",
            "日收益偏度", "日收益峰度",
        }

        def fmt(metric: str, v) -> str:
            if isinstance(v, str):
                return v
            if pd.isna(v):
                return "—"
            if metric == "累计成交额":
                return f"{v / 1e8:,.2f} 亿元"
            if metric in ("交易日数", "回撤持续天数"):
                return f"{int(round(v))}"
            if metric == "年数":
                return f"{v:.2f}"
            if metric in ratio_like:
                return f"{v:.3f}"
            return f"{v * 100:.2f}%"

        for metric in metrics.index:
            vals = [fmt(metric, metrics.loc[metric, label]) for label in metrics.columns]
            lines.append(f"| {metric} | " + " | ".join(vals) + " |")
        lines.append("")
        lines.append("## 入选因子")
        lines.append("")
        lines.append("| 因子 | 说明 | IC 均值 | ICIR | 方向 | 权重 |")
        lines.append("|---|---|---|---|---|---|")
        for s in strategy.specs:
            lines.append(
                f"| {s.name} | {s.as_dict()['desc']} | {s.ic_mean:+.4f} | {s.icir:+.3f} | "
                f"{'正向' if s.sign > 0 else '反向'} | {s.weight:.3f} |"
            )
        lines.append("")
        path = self.out_dir / "summary.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        return path
