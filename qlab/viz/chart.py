"""展示层其一：基于 Pillow 的轻量绘图引擎。

为什么不用 matplotlib？
    本项目的运行环境要求「零第三方绘图依赖即可出图」。直接用 Pillow 画折线/面积/
    热力图，先按 2 倍分辨率绘制再 LANCZOS 下采样，即可得到接近矢量图的观感，
    同时保证在评审机器上 ``pip install -r requirements.txt`` 后立刻能出图。
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ----------------------------------------------------------------------
# 主题
# ----------------------------------------------------------------------
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\Deng.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]

COLORS = {
    "primary": "#1F4E79",     # 策略主线（深蓝）
    "secondary": "#C0504D",   # 基准（砖红）
    "accent": "#E8A33D",      # 强调（金）
    "green": "#2E7D32",
    "gray": "#7F7F7F",
    "light": "#E9EEF4",
    "grid": "#DDE3EA",
    "text": "#1A1A1A",
    "bg": "#FFFFFF",
}


def _find_font() -> str | None:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return path
    return None


_FONT_PATH = _find_font()


class Chart:
    """简易图表画布：坐标系 + 基本图元。"""

    def __init__(
        self,
        width: int = 1100,
        height: int = 620,
        scale: int = 2,
        bg: str = COLORS["bg"],
        margin: tuple[int, int, int, int] = (90, 60, 40, 70),
    ):
        self.w, self.h, self.scale = width, height, scale
        self.img = Image.new("RGB", (width * scale, height * scale), bg)
        self.draw = ImageDraw.Draw(self.img)
        self.margin = margin  # left, top, right, bottom（逻辑像素）
        self._xr = (0.0, 1.0)
        self._yr = (0.0, 1.0)
        self._font_cache: dict[int, ImageFont.FreeTypeFont] = {}

    # ---------------- 坐标系 ----------------
    def font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        key = size
        if key not in self._font_cache:
            px = int(size * self.scale)
            if _FONT_PATH:
                self._font_cache[key] = ImageFont.truetype(_FONT_PATH, px)
            else:  # 极端情况：无中文字体，退回位图字体
                self._font_cache[key] = ImageFont.load_default()
        return self._font_cache[key]

    @property
    def plot_box(self) -> tuple[float, float, float, float]:
        l, t, r, b = self.margin
        return (l, t, self.w - r, self.h - b)

    def set_x(self, x0: float, x1: float) -> None:
        self._xr = (x0, x1 if x1 != x0 else x0 + 1)

    def set_y(self, y0: float, y1: float) -> None:
        if y1 == y0:
            y1 = y0 + 1
        self._yr = (y0, y1)

    def px(self, x: float) -> float:
        l, _, r, _ = self.plot_box
        x0, x1 = self._xr
        return (l + (x - x0) / (x1 - x0) * (r - l)) * self.scale

    def py(self, y: float) -> float:
        _, t, _, b = self.plot_box
        y0, y1 = self._yr
        return (b - (y - y0) / (y1 - y0) * (b - t)) * self.scale

    # ---------------- 图元 ----------------
    def line(self, xs, ys, color: str = COLORS["primary"], width: float = 2.0,
             dash: tuple[float, float] | None = None) -> None:
        pts = [(self.px(x), self.py(y)) for x, y in zip(xs, ys)]
        if len(pts) < 2:
            return
        w = max(1, int(round(width * self.scale)))
        if dash is None:
            self.draw.line(pts, fill=color, width=w, joint="curve")
            return
        # 虚线：按逻辑像素长度切段
        on, off = dash
        on_px, off_px = on * self.scale, off * self.scale
        for (x1, y1), (x2, y2) in zip(pts[:-1], pts[1:]):
            seg = math.hypot(x2 - x1, y2 - y1)
            if seg == 0:
                continue
            dx, dy = (x2 - x1) / seg, (y2 - y1) / seg
            pos = 0.0
            while pos < seg:
                end = min(pos + on_px, seg)
                self.draw.line(
                    [(x1 + dx * pos, y1 + dy * pos), (x1 + dx * end, y1 + dy * end)],
                    fill=color, width=w,
                )
                pos = end + off_px

    def area_between(self, xs, ys_top, ys_bottom, color: str, alpha: int = 60) -> None:
        """两条曲线之间的半透明填充。"""
        top = [(self.px(x), self.py(y)) for x, y in zip(xs, ys_top)]
        bottom = [(self.px(x), self.py(y)) for x, y in zip(reversed(xs), reversed(ys_bottom))]
        overlay = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        rgb = tuple(int(color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
        od.polygon(top + bottom, fill=rgb + (alpha,))
        self.img = Image.alpha_composite(self.img.convert("RGBA"), overlay).convert("RGB")
        self.draw = ImageDraw.Draw(self.img)

    def area_to_baseline(self, xs, ys, base: float, color: str, alpha: int = 50) -> None:
        self.area_between(xs, ys, [base] * len(xs), color, alpha)

    def rect(self, x0, y0, x1, y1, fill: str, outline: str | None = None,
             width: float = 1.0) -> None:
        box = [self.px(x0), self.py(y0), self.px(x1), self.py(y1)]
        box = [min(box[0], box[2]), min(box[1], box[3]), max(box[0], box[2]), max(box[1], box[3])]
        self.draw.rectangle(box, fill=fill, outline=outline,
                            width=max(1, int(width * self.scale)))

    def hline(self, y: float, color: str = COLORS["grid"], width: float = 1.0,
              dash: tuple[float, float] | None = (4, 4)) -> None:
        l, _, r, _ = self.plot_box
        self.line([l / self.scale, r / self.scale], [y, y], color, width, dash)

    def vline(self, x: float, color: str = COLORS["grid"], width: float = 1.0,
              dash: tuple[float, float] | None = (4, 4)) -> None:
        _, t, _, b = self.plot_box
        self.line([x, x], [b / self.scale, t / self.scale], color, width, dash)

    def marker(self, x: float, y: float, kind: str = "up", size: float = 6.0,
               color: str = COLORS["accent"]) -> None:
        """三角标记（up=买入, down=卖出）。"""
        cx, cy = self.px(x), self.py(y)
        s = size * self.scale
        if kind == "up":
            pts = [(cx, cy - s), (cx - s * 0.9, cy + s * 0.7), (cx + s * 0.9, cy + s * 0.7)]
        elif kind == "down":
            pts = [(cx, cy + s), (cx - s * 0.9, cy - s * 0.7), (cx + s * 0.9, cy - s * 0.7)]
        else:
            pts = [(cx - s, cy - s), (cx + s, cy - s), (cx + s, cy + s), (cx - s, cy + s)]
        self.draw.polygon(pts, fill=color)

    def text(self, x: float, y: float, s: str, size: int = 13, color: str = COLORS["text"],
             anchor: str = "lt", boxed: bool = False) -> None:
        f = self.font(size)
        px, py = self.px(x), self.py(y)
        if boxed:
            bbox = self.draw.textbbox((px, py), s, font=f, anchor=anchor)
            pad = 3 * self.scale
            self.draw.rectangle(
                [bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad],
                fill="#FFFFFF", outline=COLORS["grid"],
            )
        self.draw.text((px, py), s, font=f, fill=color, anchor=anchor)

    def pixel_text(self, px: float, py: float, s: str, size: int = 13,
                   color: str = COLORS["text"], anchor: str = "lt") -> None:
        self.draw.text((px * self.scale, py * self.scale), s, font=self.font(size),
                       fill=color, anchor=anchor)

    # ---------------- 坐标轴 ----------------
    def axes(self, x_ticks: list[tuple[float, str]] | None = None,
             y_ticks: list[tuple[float, str]] | None = None,
             x_label: str = "", y_label: str = "",
             y_grid: bool = True, x_grid: bool = False,
             tick_size: int = 12) -> None:
        l, t, r, b = self.plot_box
        # 边框
        self.draw.rectangle(
            [l * self.scale, t * self.scale, r * self.scale, b * self.scale],
            outline="#BFC8D2", width=max(1, self.scale),
        )
        if y_ticks:
            for val, label in y_ticks:
                y = self.py(val) / self.scale
                if y_grid and t <= y <= b:
                    self.hline(val, COLORS["grid"], 1.0, (3, 3))
                self.pixel_text(l - 8, self.py(val) / self.scale, label, tick_size,
                                COLORS["gray"], anchor="rm")
        if x_ticks:
            for val, label in x_ticks:
                x = self.px(val) / self.scale
                if x_grid and l <= x <= r:
                    self.vline(val, COLORS["grid"], 1.0, (3, 3))
                self.pixel_text(x, b + 8, label, tick_size, COLORS["gray"], anchor="mt")
        if x_label:
            self.pixel_text((l + r) / 2, b + 38, x_label, tick_size + 1,
                            COLORS["text"], anchor="mt")
        if y_label:
            self.pixel_text(l - 58, (t + b) / 2, y_label, tick_size + 1,
                            COLORS["text"], anchor="mm")

    def title(self, s: str, subtitle: str | None = None) -> None:
        l, t, r, _ = self.plot_box
        self.pixel_text(l, t - 44, s, 20, COLORS["text"], anchor="lt")
        if subtitle:
            self.pixel_text(r, t - 20, subtitle, 12, COLORS["gray"], anchor="rt")

    def legend(self, entries: list[tuple[str, str]], loc: str = "upper left",
               y: float = 0.0, line_width: float = 3.0) -> None:
        """图例：entries = [(标签, 颜色), ...]；loc 支持 upper/lower + left/right。"""
        l, t, r, b = self.plot_box
        size = 12
        widths = [self.draw.textlength(s, font=self.font(size)) / self.scale for s, _ in entries]
        box_w = sum(widths) + 60 * len(entries) + 16
        x0 = l + 12 if "left" in loc else r - box_w - 12
        y0 = (t + 12 + y) if "upper" in loc else (b - 34 + y)
        for i, (label, color) in enumerate(entries):
            x = x0 + 10 + sum(widths[:i]) + 60 * i
            yy = y0 + 10
            self.draw.line([(x * self.scale, yy * self.scale),
                            ((x + 26) * self.scale, yy * self.scale)],
                           fill=color, width=int(line_width * self.scale))
            self.pixel_text(x + 32, yy, label, size, COLORS["text"], anchor="lm")

    # ---------------- 输出 ----------------
    def save(self, path: str | Path, quality: int = 95) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        out = self.img.resize((self.w, self.h), Image.LANCZOS)
        out.save(path, quality=quality, dpi=(200, 200))
        return path


# ----------------------------------------------------------------------
# 刻度工具
# ----------------------------------------------------------------------
def nice_ticks(lo: float, hi: float, n: int = 6) -> list[float]:
    """生成「好看」的刻度值。"""
    if not math.isfinite(lo) or not math.isfinite(hi) or hi <= lo:
        return [lo, hi]
    span = hi - lo
    raw = span / max(1, n - 1)
    mag = 10 ** math.floor(math.log10(raw))
    for mult in (1, 2, 2.5, 5, 10):
        step = mult * mag
        if raw <= step:
            break
    start = math.ceil(lo / step) * step
    ticks = []
    v = start
    while v <= hi + step * 1e-9:
        ticks.append(round(v, 10))
        v += step
    return ticks


def format_pct(v: float, digits: int = 0) -> str:
    return f"{v * 100:.{digits}f}%"


def year_ticks(dates, step_years: int = 1) -> list[tuple[float, str]]:
    """时间轴刻度：每年 1 月第一个交易日。"""
    ticks = []
    prev_year = None
    for i, d in enumerate(dates):
        if d.year != prev_year and d.month <= 2:
            if prev_year is None or (d.year - prev_year) >= step_years:
                ticks.append((i, str(d.year)))
                prev_year = d.year
            else:
                prev_year = d.year
    return ticks


def month_ticks(dates) -> list[tuple[float, str]]:
    ticks = []
    prev = None
    for i, d in enumerate(dates):
        key = (d.year, d.month)
        if key != prev:
            if d.month in (1, 4, 7, 10):
                ticks.append((i, f"{d.year % 100:02d}/{d.month:02d}"))
            prev = key
    return ticks
