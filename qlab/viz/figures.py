"""展示层其二：把回测结果画成报告/看板用的图。

产出（默认写入 output/figures/）：
    nav_curve.png        归一化净值对比（含建仓/择时买卖点标注）
    drawdown.png         回撤对比
    monthly_heatmap.png  策略月度收益热力图
    factor_ic.png        因子 IC/ICIR 柱状图
    exposure.png         仓位与持仓数量
    architecture.png     系统架构图（报告用）
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from qlab.analytics.metrics import drawdown_series, max_drawdown
from qlab.config import FIGURE_DIR
from qlab.viz.chart import (
    COLORS,
    Chart,
    format_pct,
    nice_ticks,
    year_ticks,
)


def _bench_label(result) -> str:
    return f"基准 {result.benchmark_name}"


# ----------------------------------------------------------------------
def fig_nav_curve(
    result,
    path: str | Path | None = None,
    split_date: str | None = None,
    title: str = "策略与基准归一化净值（初始 = 1.0）",
    max_switch_marks: int = 12,
) -> Path:
    """净值对比图：两条曲线均归一化到 1.0，并标注关键买卖点。"""
    nav = result.nav.dropna()
    bench = result.benchmark_nav.reindex(nav.index).ffill()
    if bench.isna().all() or float(bench.iloc[0]) != 1.0:
        bench = bench / bench.dropna().iloc[0]
    dates = list(nav.index)
    x = np.arange(len(dates))
    y_nav = nav.to_numpy()
    y_bench = bench.to_numpy()

    lo = float(min(np.nanmin(y_nav), np.nanmin(y_bench)))
    hi = float(max(np.nanmax(y_nav), np.nanmax(y_bench)))
    pad = (hi - lo) * 0.10 + 0.02

    ch = Chart(1120, 600)
    ch.set_x(0, max(1, len(dates) - 1))
    ch.set_y(lo - pad, hi + pad)
    ch.title(title, subtitle=f"{dates[0]:%Y-%m-%d} ~ {dates[-1]:%Y-%m-%d}")

    ticks_y = nice_ticks(lo - pad, hi + pad, 7)
    ch.axes(
        x_ticks=year_ticks(dates),
        y_ticks=[(t, f"{t:.2f}") for t in ticks_y],
        x_label="", y_label="累计净值",
    )
    ch.hline(1.0, "#9AA5B1", 1.2, (6, 4))

    # 策略与基准之间的差距填充，直观看出超额收益
    ch.area_between(x, y_nav, y_bench, COLORS["primary"], alpha=28)
    ch.line(x, y_bench, COLORS["secondary"], 2.0)
    ch.line(x, y_nav, COLORS["primary"], 2.6)

    # ---- 关键买卖点：建仓、择时开关 ----
    marks = []
    trades = result.trades
    if trades is not None and not trades.empty:
        first_buy = trades[trades["side"] == "BUY"]
        if not first_buy.empty:
            d0 = pd.Timestamp(first_buy["date"].iloc[0])
            if d0 in nav.index:
                i0 = dates.index(d0)
                marks.append((i0, "建仓", "up"))

    # 目标权重总仓位变化处 = 择时开关
    if not result.targets.empty:
        gross = result.targets.sum(axis=1)
        changes = gross.diff().abs() > 1e-6
        switch_dates = [d for d in gross.index[changes] if d in nav.index]
        if len(switch_dates) > max_switch_marks:  # 太密则只取前 max 个，避免图面拥挤
            step = max(1, len(switch_dates) // max_switch_marks)
            switch_dates = switch_dates[::step]
        for d in switch_dates:
            i = dates.index(d)
            label = "加仓" if gross.loc[d] > gross.shift(1).loc[d] else "降仓"
            marks.append((i, label, "up" if label == "加仓" else "down"))

    # 同一位置只保留一个标记，建仓优先于加减仓
    priority = {"建仓": 0, "加仓": 1, "降仓": 2}
    best: dict[int, tuple] = {}
    for m in marks:
        cur = best.get(m[0])
        if cur is None or priority[m[1]] < priority[cur[1]]:
            best[m[0]] = m

    last_label = -(10 ** 9)
    min_gap = max(8, int(len(dates) * 0.02))  # 标签最小间距，太近就只画标记
    for i in sorted(best):
        _, label, kind = best[i]
        up = kind == "up"
        y0 = float(y_nav[i])
        ch.marker(i, y0, kind, 6.5, COLORS["accent"] if up else COLORS["green"])
        if i - last_label < min_gap:
            continue
        last_label = i
        y_text = y0 + (pad * 0.45 if up else -pad * 0.45)
        if y_text > hi - pad * 0.25:      # 贴近上边界时改到下方
            y_text = y0 - pad * 0.45
        elif y_text < lo + pad * 0.25:    # 贴近下边界时改到上方
            y_text = y0 + pad * 0.45
        anchor = "mb" if up else "mt"
        frac = i / max(1, len(dates) - 1)
        if frac < 0.03:
            anchor = "lb" if up else "lt"
        elif frac > 0.97:
            anchor = "rb" if up else "rt"
        ch.text(i, y_text, label, 11, COLORS["gray"], anchor=anchor)

    # ---- 最大回撤区间 ----
    mdd, peak, trough = max_drawdown(nav)
    if peak is not None and trough is not None and peak in nav.index and trough in nav.index:
        ip, it = dates.index(peak), dates.index(trough)
        ch.vline(ip, "#B0B8C1", 1.2, (3, 5))
        ch.vline(it, "#B0B8C1", 1.2, (3, 5))
        mid = (ip + it) / 2
        ch.text(mid, lo + pad * 0.12, f"最大回撤 {format_pct(mdd, 1)}",
                12, COLORS["secondary"], anchor="mt", boxed=True)

    # ---- 样本内/外分界 ----
    if split_date:
        sd = pd.Timestamp(split_date)
        later = [i for i, d in enumerate(dates) if d > sd]
        if later:
            i = later[0]
            ch.line([i, i], [lo - pad, hi + pad], COLORS["gray"], 1.4, (8, 5))
            ch.text(i + max(1, len(dates) * 0.01), hi + pad * 0.62, "← 样本内 | 样本外 →",
                    12, COLORS["gray"], anchor="lt")

    ch.legend(
        [
            ("策略净值", COLORS["primary"]),
            (_bench_label(result), COLORS["secondary"]),
            ("▲ 建仓/加仓", COLORS["accent"]),
            ("▼ 降仓", COLORS["green"]),
        ],
        loc="upper left",
    )
    ch.save(path or FIGURE_DIR / "nav_curve.png")
    return Path(path or FIGURE_DIR / "nav_curve.png")


# ----------------------------------------------------------------------
def fig_drawdown(result, path: str | Path | None = None) -> Path:
    """回撤对比：策略用面积填充，基准用虚线。"""
    nav = result.nav.dropna()
    bench = result.benchmark_nav.reindex(nav.index).ffill()
    bench = bench / bench.dropna().iloc[0]
    dd_s = drawdown_series(nav).to_numpy()
    dd_b = drawdown_series(bench).to_numpy()
    dates = list(nav.index)
    x = np.arange(len(dates))
    lo = float(min(dd_s.min(), dd_b.min()))
    hi = 0.0
    pad = abs(lo) * 0.12 + 0.005

    ch = Chart(1120, 380, margin=(90, 56, 40, 60))
    ch.set_x(0, max(1, len(dates) - 1))
    ch.set_y(lo - pad, hi + pad * 0.4)
    ch.title("回撤对比",
             subtitle=f"策略 {format_pct(float(dd_s.min()), 1)} ｜ 基准 {format_pct(float(dd_b.min()), 1)}")

    ticks = nice_ticks(lo - pad, 0.0, 5)
    ch.axes(x_ticks=year_ticks(dates),
            y_ticks=[(t, format_pct(t, 0)) for t in ticks],
            y_label="回撤")
    ch.area_to_baseline(x, dd_s, 0.0, COLORS["primary"], 60)
    ch.line(x, dd_s, COLORS["primary"], 2.0)
    ch.line(x, dd_b, COLORS["secondary"], 1.6, (6, 3))
    ch.hline(0.0, "#9AA5B1", 1.0, None)
    ch.legend([("策略回撤", COLORS["primary"]), (f"{_bench_label(result)} 回撤", COLORS["secondary"])],
              loc="lower left")
    out = Path(path or FIGURE_DIR / "drawdown.png")
    ch.save(out)
    return out


# ----------------------------------------------------------------------
def _heat_color(v: float, vmax: float) -> str:
    if vmax <= 0:
        vmax = 1.0
    t = max(-1.0, min(1.0, v / vmax))
    if t >= 0:
        base = (255 - int(120 * (1 - t)), 255 - int(60 * (1 - t)), 235 - int(120 * (1 - t)))
    else:
        base = (255, 240 - int(90 * (1 + t)), 235 - int(120 * (1 + t)))
    return "#%02X%02X%02X" % base


def fig_monthly_heatmap(result, path: str | Path | None = None) -> Path:
    """月度收益热力图（行=年份，列=1-12 月，最后一列为全年）。"""
    m = result.monthly_returns().reindex(columns=range(1, 13))
    yearly = result.nav.resample("YE").last().pct_change(fill_method=None).dropna()
    first_year = result.nav.index[0].year
    first_val = result.nav.loc[str(first_year)].iloc[-1] / result.nav.iloc[0] - 1.0
    yearly = pd.concat([pd.Series({pd.Timestamp(f"{first_year}-12-31"): first_val}), yearly])
    yearly_map = {ts.year: float(v) * 100.0 for ts, v in yearly.items()}

    years = list(m.index)
    rows, cols = len(years), 13
    cell_w, cell_h = 62, 40
    width = 120 + cols * cell_w + 30
    height = 110 + rows * cell_h + 40

    ch = Chart(width, height, margin=(1, 1, 1, 1))
    ch.set_x(0, cols)
    ch.set_y(0, rows + 1)
    vmax = float(np.nanmax(np.abs(m.to_numpy()))) if m.size else 1.0

    # 标题
    ch.pixel_text(20, 24, "月度收益率热力图（%）", 19, COLORS["text"], anchor="lt")
    ch.pixel_text(20, 52, f"绿=正收益，红=负收益；年度列按自然年累计", 12,
                  COLORS["gray"], anchor="lt")

    # 表头
    for j, label in enumerate([f"{i}月" for i in range(1, 13)] + ["全年"]):
        x0 = 120 + j * cell_w
        ch.draw.text(((x0 + cell_w / 2) * ch.scale, (82 + 14) * ch.scale), label,
                     font=ch.font(12), fill=COLORS["gray"], anchor="mm")

    for i, year in enumerate(years):
        y0 = 100 + i * cell_h
        ch.draw.text((100 * ch.scale, (y0 + cell_h / 2) * ch.scale), str(year),
                     font=ch.font(13), fill=COLORS["text"], anchor="rm")
        for j in range(12):
            v = m.iloc[i, j]
            x0 = 120 + j * cell_w
            if pd.isna(v):
                fill = "#F4F6F8"
                text = "-"
                color = "#B0B8C1"
            else:
                fill = _heat_color(float(v), vmax)
                text = f"{v:.1f}"
                color = "#14324F" if abs(float(v)) < vmax * 0.65 else "#FFFFFF"
            ch.draw.rectangle(
                [x0 * ch.scale, y0 * ch.scale, (x0 + cell_w - 3) * ch.scale,
                 (y0 + cell_h - 3) * ch.scale],
                fill=fill, outline="#FFFFFF",
            )
            ch.draw.text(((x0 + cell_w / 2) * ch.scale, (y0 + cell_h / 2) * ch.scale),
                         text, font=ch.font(11), fill=color, anchor="mm")
        # 年度列
        yv = yearly_map.get(year, np.nan)
        x0 = 120 + 12 * cell_w
        fill = _heat_color(float(yv), vmax * 2.2) if not pd.isna(yv) else "#F4F6F8"
        ch.draw.rectangle([x0 * ch.scale, y0 * ch.scale, (x0 + cell_w - 3) * ch.scale,
                           (y0 + cell_h - 3) * ch.scale], fill=fill, outline="#FFFFFF")
        ch.draw.text(((x0 + cell_w / 2) * ch.scale, (y0 + cell_h / 2) * ch.scale),
                     "-" if pd.isna(yv) else f"{yv:.1f}", font=ch.font(11),
                     fill="#14324F", anchor="mm")
    out = Path(path or FIGURE_DIR / "monthly_heatmap.png")
    ch.save(out)
    return out


# ----------------------------------------------------------------------
def fig_factor_ic(ic_table: pd.DataFrame, selected: list[str],
                  path: str | Path | None = None) -> Path:
    """因子 ICIR 横向柱状图，入选因子高亮。"""
    table = ic_table.dropna(subset=["icir"]).sort_values("icir")
    n = len(table)
    row_h = 34
    height = max(320, 120 + n * row_h)
    ch = Chart(980, height, margin=(150, 70, 90, 50))

    values = table["icir"].to_numpy()
    lo, hi = float(min(values.min(), 0)), float(max(values.max(), 0))
    span = max(abs(lo), abs(hi)) * 1.9 or 1.0  # 给数值标签留位置
    ch.set_x(-span, span)
    ch.set_y(0, n)
    ch.title("候选因子信息系数（样本内 ICIR）",
             subtitle="深蓝=入选组合，灰色=未入选；横轴为 ICIR，括号内为 IC 均值")

    ticks = nice_ticks(-span, span, 5)
    ch.axes(x_ticks=[(t, f"{t:+.2f}") for t in ticks], y_ticks=None, x_label="ICIR")
    ch.vline(0.0, "#9AA5B1", 1.2, None)
    for t in ticks:
        ch.vline(t, COLORS["grid"], 1.0, (3, 3))

    for i, (_, row) in enumerate(table.iterrows()):
        y = i + 0.5
        color = COLORS["primary"] if row["factor"] in selected else "#C3CCD6"
        ch.draw.rectangle(
            [ch.px(min(0, row["icir"])), ch.py(y + 0.30),
             ch.px(max(0, row["icir"])), ch.py(y - 0.30)],
            fill=color,
        )
        ch.pixel_text(140, ch.py(y) / ch.scale, f"{row['factor']}",
                      12, COLORS["text"], anchor="rm")
        label_x = ch.px(row["icir"]) / ch.scale + (6 if row["icir"] >= 0 else -6)
        ch.pixel_text(label_x, ch.py(y) / ch.scale,
                      f"{row['icir']:+.2f} (IC {row['mean_ic']:+.3f})",
                      11, COLORS["gray"],
                      anchor="lm" if row["icir"] >= 0 else "rm")
    out = Path(path or FIGURE_DIR / "factor_ic.png")
    ch.save(out)
    return out


# ----------------------------------------------------------------------
def fig_exposure(result, path: str | Path | None = None) -> Path:
    """股票仓位与持仓只数随时间变化。"""
    exp = result.exposure.dropna()
    hold = result.n_holdings.reindex(exp.index).ffill()
    dates = list(exp.index)
    x = np.arange(len(dates))

    ch = Chart(1120, 340, margin=(90, 56, 40, 60))
    ch.set_x(0, max(1, len(dates) - 1))
    ch.set_y(0, 1.05)
    ch.title("股票仓位与持仓数量",
             subtitle=f"平均仓位 {format_pct(float(exp.mean()), 0)}，持仓均值 {float(hold.mean()):.1f} 只")
    ch.axes(x_ticks=year_ticks(dates),
            y_ticks=[(t, format_pct(t, 0)) for t in [0, 0.25, 0.5, 0.75, 1.0]],
            y_label="股票仓位")
    ch.area_to_baseline(x, exp.to_numpy(), 0.0, COLORS["primary"], 55)
    ch.line(x, exp.to_numpy(), COLORS["primary"], 2.0)

    # 持仓只数（右轴，按最大值缩放）
    hmax = max(1.0, float(np.nanmax(hold.to_numpy())))
    scale = 1.0 / hmax
    ch.line(x, hold.to_numpy() * scale, COLORS["accent"], 1.6, (5, 3))
    ch.pixel_text(ch.w - 40, 60, f"右轴：持仓只数（0~{hmax:.0f}）", 11, COLORS["gray"], anchor="rt")
    ch.legend([("股票仓位(左轴)", COLORS["primary"]), ("持仓只数(右轴)", COLORS["accent"])],
              loc="lower left")
    out = Path(path or FIGURE_DIR / "exposure.png")
    ch.save(out)
    return out


# ----------------------------------------------------------------------
def fig_architecture(path: str | Path | None = None) -> Path:
    """系统架构图：分层 + 关键文件，报告插图用。"""
    ch = Chart(1120, 620, margin=(1, 1, 1, 1))
    ch.set_x(0, 1120)
    ch.set_y(0, 620)

    ch.pixel_text(30, 26, "QLab 系统架构（分层与数据流）", 20, COLORS["text"], anchor="lt")

    layers = [
        ("数据层  qlab.data",
         ["providers akshare / baostock", "cache + manifest（腾讯/新浪备用）", "loader → MarketPanel"],
         "#1F4E79"),
        ("计算层  qlab.factors",
         ["12 个候选因子（动量/反转/波动/流动性）", "去极值 → z-score", "IC / ICIR / 分层收益"],
         "#2E6DA4"),
        ("策略层  qlab.strategy",
         ["样本内 ICIR 选因子 + 加权合成", "横截面 Top-N 等权", "沪深300 均线择时 → 目标权重"],
         "#3C8DBC"),
        ("回测层  qlab.backtest",
         ["t 日收盘信号 → t+1 开盘撮合", "佣金/印花税/滑点/整手", "日频市值结算 → 净值曲线"],
         "#E8A33D"),
        ("输出层  analytics · viz · webapp",
         ["绩效指标 / 样本内外对比", "Pillow 图表 + Excel / CSV", "HTTP API + 前端看板"],
         "#C0504D"),
    ]
    top, box_h, gap = 86, 78, 14
    for i, (name, items, color) in enumerate(layers):
        y0 = top + i * (box_h + gap)
        ch.draw.rounded_rectangle(
            [40 * ch.scale, y0 * ch.scale, 1080 * ch.scale, (y0 + box_h) * ch.scale],
            radius=10 * ch.scale, fill="#FFFFFF", outline=color, width=2 * ch.scale,
        )
        ch.draw.rectangle([40 * ch.scale, y0 * ch.scale, 300 * ch.scale,
                           (y0 + box_h) * ch.scale], fill=color)
        ch.pixel_text(58, y0 + box_h / 2, name, 14, "#FFFFFF", anchor="lm")
        for j, item in enumerate(items):
            ch.pixel_text(320 + j * 252, y0 + box_h / 2, item, 11.5,
                          COLORS["text"], anchor="lm")
        if i < len(layers) - 1:
            cx = 560
            ch.draw.line([(cx * ch.scale, (y0 + box_h) * ch.scale),
                          (cx * ch.scale, (y0 + box_h + gap) * ch.scale)],
                         fill="#9AA5B1", width=2 * ch.scale)
    ch.pixel_text(40, 580, "依赖方向单向向下；每层可独立测试，替换数据源或策略不影响回测层。",
                  11.5, COLORS["gray"], anchor="lt")
    out = Path(path or FIGURE_DIR / "architecture.png")
    ch.save(out)
    return out


def build_all(result, ic_table: pd.DataFrame, selected: list[str],
              split_date: str | None = None) -> dict[str, Path]:
    """一次性生成全部图表。"""
    return {
        "nav": fig_nav_curve(result, split_date=split_date),
        "drawdown": fig_drawdown(result),
        "heatmap": fig_monthly_heatmap(result),
        "factor_ic": fig_factor_ic(ic_table, selected),
        "exposure": fig_exposure(result),
        "architecture": fig_architecture(),
    }
