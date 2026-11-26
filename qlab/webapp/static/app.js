/* QLab 看板前端：原生 JS + Canvas，无任何第三方依赖。
 *
 * 数据由 /api/run 返回，结构见 qlab/webapp/server.py：
 *   meta / metrics / series / annual / monthly / monthly_benchmark
 *   markers / factors / selected / trades / holdings / cost
 * 所有图表都在浏览器里按当前统计区间重绘，切换“样本内/样本外”时
 * 净值会重新以区间首日归一，回撤按区间内高点重算。
 */

"use strict";

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => Array.from(root.querySelectorAll(s));
const SEGS = ["全样本", "样本内", "样本外"];

const STATE = {
  data: null,
  seg: "全样本",
  monthlyView: "strategy",
  tradeSide: "all",
};

const COLORS = {
  primary: "#1f4e79",
  primarySoft: "rgba(31, 78, 121, 0.08)",
  secondary: "#c0504d",
  accent: "#c8871f",
  green: "#12805c",
  muted: "#8a95a3",
  grid: "#eef2f7",
  band: "rgba(18, 128, 92, 0.055)",
};

/* ------------------------------ 格式化 ------------------------------ */
const isNum = (v) => typeof v === "number" && isFinite(v);

function pct(v, d = 2) {
  return isNum(v) ? (v * 100).toFixed(d) + "%" : "—";
}
function pctPoint(v, d = 1) {
  return isNum(v) ? v.toFixed(d) + "%" : "—";
}
function pctSigned(v, d = 2) {
  if (!isNum(v)) return "—";
  return (v > 0 ? "+" : "") + (v * 100).toFixed(d) + "%";
}
function num(v, d = 2) {
  return isNum(v) ? v.toFixed(d) : "—";
}
function money(v) {
  if (!isNum(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1e8) return (v / 1e8).toFixed(2) + " 亿";
  if (a >= 1e4) return (v / 1e4).toFixed(1) + " 万";
  return v.toFixed(0);
}
function signClass(v) {
  if (!isNum(v)) return "";
  return v > 0 ? "up" : v < 0 ? "down" : "";
}
function esc(text) {
  return String(text == null ? "" : text).replace(/[&<>"]/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]
  ));
}
function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}
function niceTicks(lo, hi, count = 5) {
  const span = hi - lo;
  if (!isFinite(span) || span <= 0) return [lo];
  const rough = span / Math.max(2, count);
  const mag = Math.pow(10, Math.floor(Math.log10(rough)));
  const norm = rough / mag;
  const step = (norm <= 1.2 ? 1 : norm <= 2.5 ? 2 : norm <= 5 ? 5 : 10) * mag;
  const first = Math.ceil(lo / step - 1e-9) * step;
  const out = [];
  for (let v = first; v <= hi + step * 1e-6; v += step) out.push(Number(v.toFixed(10)));
  return out.length >= 2 ? out : [lo, hi];
}
function drawdownSeries(values) {
  let peak = -Infinity;
  return values.map((v) => {
    if (!isNum(v)) return null;
    peak = Math.max(peak, v);
    return peak > 0 ? v / peak - 1 : null;
  });
}
function mddInfo(dd, dates) {
  let min = 0;
  let at = -1;
  dd.forEach((v, i) => {
    if (isNum(v) && v < min) { min = v; at = i; }
  });
  if (at < 0) return null;
  /* 回撤为 0 的点都是新高；取“谷底之前最后一个新高”，才是真正的峰。 */
  let peak = at;
  let peakValue = -Infinity;
  for (let i = 0; i <= at; i++) {
    if (isNum(dd[i]) && dd[i] >= peakValue - 1e-12) { peakValue = dd[i]; peak = i; }
  }
  return { value: min, trough: dates[at], peak: dates[peak], troughIndex: at, peakIndex: peak };
}

/* ------------------------------ 折线图 ------------------------------ */
class SeriesChart {
  constructor(canvas, tipEl) {
    this.canvas = canvas;
    this.tip = tipEl;
    this.cfg = null;
    this.hover = -1;
    this.w = 0;
    this.h = 0;
    this.pad = { l: 58, r: 18, t: 18, b: 30 };

    canvas.addEventListener("mousemove", (e) => this._onMove(e));
    canvas.addEventListener("mouseleave", () => {
      this.hover = -1;
      if (this.tip) this.tip.style.display = "none";
      this.draw();
    });
  }

  set(cfg) {
    this.cfg = cfg;
    this.hover = -1;
    if (this.tip) this.tip.style.display = "none";
    this.draw();
  }

  _prep() {
    const dpr = window.devicePixelRatio || 1;
    const cssW = this.canvas.clientWidth || this.canvas.parentElement.clientWidth || 800;
    const cssH = this.canvas.clientHeight || parseInt(this.canvas.style.height, 10) || 320;
    this.w = cssW;
    this.h = cssH;
    this.canvas.width = Math.round(cssW * dpr);
    this.canvas.height = Math.round(cssH * dpr);
    const ctx = this.canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);
    return ctx;
  }

  _geom() {
    const cfg = this.cfg;
    const pad = cfg.padRight
      ? { ...this.pad, r: cfg.padRight }
      : this.pad;
    return { pad, W: Math.max(10, this.w - pad.l - pad.r), H: Math.max(10, this.h - pad.t - pad.b) };
  }

  draw() {
    const cfg = this.cfg;
    const ctx = this._prep();
    if (!cfg || !cfg.dates || !cfg.dates.length) return;

    const { pad, W, H } = this._geom();
    const padL = pad.l;
    const padT = pad.t;
    const dates = cfg.dates;
    const n = dates.length;
    const series = cfg.series || [];
    const values = [];
    series.forEach((s) => (s.data || []).forEach((v) => { if (isNum(v)) values.push(v); }));
    if (!values.length) return;

    let lo = Math.min(...values);
    let hi = Math.max(...values);
    if (cfg.zeroLine) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
    if (cfg.baseline !== undefined) { lo = Math.min(lo, cfg.baseline); hi = Math.max(hi, cfg.baseline); }
    if (hi - lo < 1e-9) { hi += 0.01; lo -= 0.01; }
    const padY = (hi - lo) * 0.08;
    lo -= padY;
    hi += padY;
    if (cfg.baseline !== undefined) { lo = Math.min(lo, cfg.baseline); hi = Math.max(hi, cfg.baseline); }

    const X = (i) => padL + (n <= 1 ? W / 2 : (i / (n - 1)) * W);
    const Y = (v) => padT + H - ((v - lo) / (hi - lo)) * H;

    /* 减仓区间由底部的窄色带表达，避免整幅竖条干扰曲线阅读 */

    /* 样本内外分界 */
    let splitIdx = -1;
    if (cfg.splitDate) {
      for (let i = 0; i < n; i++) {
        if (dates[i] > cfg.splitDate) { splitIdx = i; break; }
      }
    }
    if (splitIdx > 0) {
      ctx.fillStyle = "rgba(31, 78, 121, 0.035)";
      ctx.fillRect(X(splitIdx), padT, padL + W - X(splitIdx), H);
      ctx.save();
      ctx.setLineDash([5, 4]);
      ctx.strokeStyle = "#98a4b3";
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.moveTo(X(splitIdx), padT);
      ctx.lineTo(X(splitIdx), padT + H);
      ctx.stroke();
      ctx.restore();
      ctx.fillStyle = "#8a95a3";
      ctx.font = "11px system-ui, sans-serif";
      ctx.textAlign = "right";
      ctx.fillText("样本外 →", padL + W - 6, padT + 13);
    }

    /* 网格与 y 轴 */
    ctx.font = "11px system-ui, sans-serif";
    ctx.textBaseline = "middle";
    const ticks = niceTicks(lo, hi, 5);
    ticks.forEach((v) => {
      const y = Y(v);
      if (y < padT - 1 || y > padT + H + 1) return;
      ctx.strokeStyle = COLORS.grid;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(padL + W, y);
      ctx.stroke();
      ctx.fillStyle = COLORS.muted;
      ctx.textAlign = "right";
      ctx.fillText((cfg.yFmt || ((x) => x.toFixed(2)))(v), padL - 8, y);
    });

    /* 基线 */
    if (cfg.baseline !== undefined && cfg.baseline >= lo && cfg.baseline <= hi) {
      ctx.save();
      ctx.setLineDash([5, 4]);
      ctx.strokeStyle = "#cfd6e0";
      ctx.beginPath();
      ctx.moveTo(padL, Y(cfg.baseline));
      ctx.lineTo(padL + W, Y(cfg.baseline));
      ctx.stroke();
      ctx.restore();
    }

    /* 自定义水平线（如满仓线、减仓线） */
    (cfg.hlines || []).forEach((h) => {
      if (h.v < lo || h.v > hi) return;
      const y = Y(h.v);
      ctx.save();
      ctx.setLineDash(h.dash || [4, 4]);
      ctx.strokeStyle = h.color || "#b0b8c1";
      ctx.lineWidth = h.width || 1;
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(padL + W, y);
      ctx.stroke();
      ctx.restore();
      if (h.label) {
        ctx.fillStyle = h.color || COLORS.muted;
        ctx.textAlign = "left";
        ctx.fillText(h.label, padL + 4, y - 8);
      }
    });

    /* 两条曲线之间的差距（超额部分） */
    if (cfg.areaBetween && series.length >= 2) {
      const a = series[0].data;
      const b = series[1].data;
      ctx.beginPath();
      let started = false;
      for (let i = 0; i < n; i++) {
        if (!isNum(a[i]) || !isNum(b[i])) continue;
        if (!started) { ctx.moveTo(X(i), Y(a[i])); started = true; }
        else ctx.lineTo(X(i), Y(a[i]));
      }
      for (let i = n - 1; i >= 0; i--) {
        if (!isNum(a[i]) || !isNum(b[i])) continue;
        ctx.lineTo(X(i), Y(b[i]));
      }
      ctx.closePath();
      ctx.fillStyle = "rgba(31, 78, 121, 0.08)";
      ctx.fill();
    }

    /* 面积（回撤/仓位/相对强弱） */
    if (cfg.fillToBaseline !== undefined && series.length) {
      const data = series[0].data;
      ctx.beginPath();
      let started = false;
      let firstIdx = -1;
      let lastIdx = -1;
      for (let i = 0; i < n; i++) {
        if (!isNum(data[i])) continue;
        if (!started) { ctx.moveTo(X(i), Y(data[i])); started = true; firstIdx = i; }
        else ctx.lineTo(X(i), Y(data[i]));
        lastIdx = i;
      }
      if (started && lastIdx >= 0) {
        ctx.lineTo(X(lastIdx), Y(cfg.fillToBaseline));
        ctx.lineTo(X(firstIdx), Y(cfg.fillToBaseline));
        ctx.closePath();
        ctx.fillStyle = cfg.fillColor || COLORS.primarySoft;
        ctx.fill();
      }
    }

    /* 曲线 */
    series.forEach((s) => {
      ctx.beginPath();
      ctx.strokeStyle = s.color;
      ctx.lineWidth = s.width || 2;
      ctx.lineJoin = "round";
      if (s.dash) ctx.setLineDash(s.dash); else ctx.setLineDash([]);
      let started = false;
      for (let i = 0; i < n; i++) {
        const v = s.data[i];
        if (!isNum(v)) continue;
        if (!started) { ctx.moveTo(X(i), Y(v)); started = true; }
        else ctx.lineTo(X(i), Y(v));
      }
      ctx.stroke();
      ctx.setLineDash([]);
    });

    /* 底部减仓色带：与图例“减仓区间”对应，比整幅底色更克制 */
    if ((cfg.bands || []).length) {
      const stripY = padT + H - 5;
      (cfg.bands || []).forEach((b) => {
        const x0 = X(b.from);
        const x1 = X(b.to);
        if (x1 <= x0) return;
        ctx.fillStyle = "rgba(18, 128, 92, 0.5)";
        ctx.fillRect(x0, stripY, x1 - x0, 5);
      });
    }

    /* x 轴刻度 */
    ctx.fillStyle = COLORS.muted;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    const years = new Set(dates.map((d) => d.slice(0, 4)));
    const yearly = years.size >= 4;
    let lastX = -100;
    for (let i = 0; i < n; i++) {
      const d = dates[i];
      const month = Number(d.slice(5, 7));
      const show = yearly ? (i === 0 || d.slice(0, 4) !== dates[i - 1].slice(0, 4)) : (month % 3 === 1);
      if (!show) continue;
      const x = X(i);
      if (x - lastX < 42 || (x < padL + 8 && i !== 0)) continue;
      ctx.textAlign = i === 0 ? "left" : "center";
      ctx.fillText(yearly ? d.slice(0, 4) : d.slice(2, 7), i === 0 ? x + 2 : x, padT + H + 8);
      lastX = x;
    }

    /* 买卖点标记 */
    const markers = cfg.markers || [];
    let lastLabelX = -1e9;
    markers.forEach((m) => {
      const s0 = series[0] || { data: [] };
      const v = s0.data[m.i];
      if (!isNum(v)) return;
      const cx = X(m.i);
      const cy = Y(v);
      const size = 6;
      ctx.beginPath();
      if (m.kind === "down") {
        ctx.moveTo(cx, cy + size * 1.7);
        ctx.lineTo(cx - size, cy + size * 0.4);
        ctx.lineTo(cx + size, cy + size * 0.4);
        ctx.fillStyle = COLORS.green;
      } else {
        ctx.moveTo(cx, cy - size * 1.7);
        ctx.lineTo(cx - size, cy - size * 0.4);
        ctx.lineTo(cx + size, cy - size * 0.4);
        ctx.fillStyle = COLORS.accent;
      }
      ctx.closePath();
      ctx.fill();

      if (cx - lastLabelX > 78 && cx > padL + 20 && cx < padL + W - 26) {
        ctx.fillStyle = COLORS.muted;
        ctx.font = "10.5px system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = m.kind === "down" ? "top" : "bottom";
        ctx.fillText(m.label, cx, m.kind === "down" ? cy + size * 3.4 : cy - size * 2.8);
        lastLabelX = cx;
      }
    });

    /* 末端数值标签 */
    if (cfg.endLabels) {
      const labels = [];
      series.forEach((s) => {
        let last = -1;
        for (let i = n - 1; i >= 0; i--) {
          if (isNum(s.data[i])) { last = i; break; }
        }
        if (last >= 0) labels.push({ y: Y(s.data[last]), value: s.data[last], color: s.color });
      });
      labels.sort((a, b) => a.y - b.y);
      for (let i = 1; i < labels.length; i++) {
        if (labels[i].y - labels[i - 1].y < 16) labels[i].y = labels[i - 1].y + 16;
      }
      const boxW = 52;
      labels.forEach((l) => {
        const x = padL + W + 6;
        const y = clamp(l.y, padT + 6, padT + H - 6);
        ctx.fillStyle = "#fff";
        ctx.strokeStyle = l.color;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.rect(x, y - 8, boxW, 16);
        ctx.fill();
        ctx.stroke();
        ctx.fillStyle = l.color;
        ctx.font = "10.5px system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText((cfg.endFmt || ((v) => v.toFixed(2)))(l.value), x + boxW / 2, y + 0.5);
      });
    }

    /* 悬停十字线 */
    const hover = this.hover;
    if (hover >= 0 && hover < n) {
      const x = X(hover);
      ctx.save();
      ctx.strokeStyle = "#9aa8b8";
      ctx.setLineDash([3, 3]);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, padT);
      ctx.lineTo(x, padT + H);
      ctx.stroke();
      ctx.restore();
      series.forEach((s) => {
        const v = s.data[hover];
        if (!isNum(v)) return;
        ctx.beginPath();
        ctx.fillStyle = s.color;
        ctx.arc(x, Y(v), 3.4, 0, Math.PI * 2);
        ctx.fill();
        ctx.beginPath();
        ctx.strokeStyle = "#fff";
        ctx.lineWidth = 1.5;
        ctx.arc(x, Y(v), 3.4, 0, Math.PI * 2);
        ctx.stroke();
      });
    }
  }

  _onMove(e) {
    if (!this.cfg || !this.w) return;
    const { pad, W } = this._geom();
    const rect = this.canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const n = this.cfg.dates.length;
    if (n < 2 || x < pad.l - 8 || x > pad.l + W + 8) {
      if (this.hover !== -1) {
        this.hover = -1;
        if (this.tip) this.tip.style.display = "none";
        this.draw();
      }
      return;
    }
    const t = clamp((x - pad.l) / W, 0, 1);
    const i = Math.round(t * (n - 1));
    if (i === this.hover) return;
    this.hover = i;
    this.draw();
    if (!this.tip) return;

    const rows = this.cfg.tip ? this.cfg.tip(i) : [];
    if (!rows || !rows.length) { this.tip.style.display = "none"; return; }
    this.tip.innerHTML =
      `<div class="t">${this.cfg.dates[i]}</div>` +
      rows.map((r) =>
        `<div class="r"><span>${esc(r[0])}</span><b style="color:${r[2] || "#fff"}">${esc(r[1])}</b></div>`
      ).join("");
    this.tip.style.display = "block";
    const tw = this.tip.offsetWidth;
    let left = pad.l + t * W + 14;
    if (left + tw > this.w - 8) left = pad.l + t * W - tw - 14;
    this.tip.style.left = Math.max(6, left) + "px";
    this.tip.style.top = "12px";
  }
}

/* ------------------------------ 柱状图 ------------------------------ */
class BarChart {
  constructor(canvas, tipEl) {
    this.canvas = canvas;
    this.tip = tipEl;
    this.cfg = null;
    this.hover = -1;
    this.w = 0;
    this.h = 0;
    this.pad = { l: 50, r: 14, t: 26, b: 30 };
    canvas.addEventListener("mousemove", (e) => this._onMove(e));
    canvas.addEventListener("mouseleave", () => {
      this.hover = -1;
      if (this.tip) this.tip.style.display = "none";
      this.draw();
    });
  }

  set(cfg) {
    this.cfg = cfg;
    this.hover = -1;
    if (this.tip) this.tip.style.display = "none";
    this.draw();
  }

  _prep() {
    const dpr = window.devicePixelRatio || 1;
    const cssW = this.canvas.clientWidth || this.canvas.parentElement.clientWidth || 600;
    const cssH = this.canvas.clientHeight || parseInt(this.canvas.style.height, 10) || 280;
    this.w = cssW;
    this.h = cssH;
    this.canvas.width = Math.round(cssW * dpr);
    this.canvas.height = Math.round(cssH * dpr);
    const ctx = this.canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);
    return ctx;
  }

  draw() {
    const cfg = this.cfg;
    const ctx = this._prep();
    if (!cfg || !cfg.labels || !cfg.labels.length) return;

    const pad = this.pad;
    const W = Math.max(10, this.w - pad.l - pad.r);
    const H = Math.max(10, this.h - pad.t - pad.b);
    const n = cfg.labels.length;
    const series = cfg.series || [];

    let lo = 0;
    let hi = 0;
    series.forEach((s) => (s.data || []).forEach((v) => {
      if (!isNum(v)) return;
      lo = Math.min(lo, v);
      hi = Math.max(hi, v);
    }));
    if (hi - lo < 1e-9) { hi = 1; lo = -1; }
    const padY = (hi - lo) * 0.16;
    lo -= padY;
    hi += padY;
    const ticks = niceTicks(lo, hi, 4);
    const Y = (v) => pad.t + H - ((v - lo) / (hi - lo)) * H;
    const groupW = W / n;
    const barW = Math.max(7, Math.min(26, groupW * 0.28));
    const gap = 3;

    ctx.font = "11px system-ui, sans-serif";
    ctx.textBaseline = "middle";
    ticks.forEach((v) => {
      const y = Y(v);
      if (y < pad.t - 1 || y > pad.t + H + 1) return;
      ctx.strokeStyle = COLORS.grid;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(pad.l, y);
      ctx.lineTo(pad.l + W, y);
      ctx.stroke();
      ctx.fillStyle = COLORS.muted;
      ctx.textAlign = "right";
      ctx.fillText(`${v.toFixed(0)}%`, pad.l - 8, y);
    });

    if (this.hover >= 0 && this.hover < n) {
      ctx.fillStyle = "rgba(31, 78, 121, 0.05)";
      ctx.fillRect(pad.l + this.hover * groupW, pad.t, groupW, H);
    }

    for (let i = 0; i < n; i++) {
      const center = pad.l + groupW * (i + 0.5);
      series.forEach((s, si) => {
        const v = s.data[i];
        if (!isNum(v)) return;
        const offset = (si - (series.length - 1) / 2) * (barW + gap);
        const x = center + offset - barW / 2;
        const y0 = Y(0);
        const y1 = Y(v);
        ctx.fillStyle = s.color;
        ctx.fillRect(x, Math.min(y0, y1), barW, Math.abs(y1 - y0));
        ctx.fillStyle = "#5b6674";
        ctx.font = "10px system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = v >= 0 ? "bottom" : "top";
        ctx.fillText(`${v.toFixed(1)}`, x + barW / 2, v >= 0 ? y1 - 3 : y1 + 3);
      });
      ctx.fillStyle = COLORS.muted;
      ctx.font = "11px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      ctx.fillText(cfg.labels[i], center, pad.t + H + 8);
    }
  }

  _onMove(e) {
    if (!this.cfg || !this.w) return;
    const pad = this.pad;
    const W = this.w - pad.l - pad.r;
    const rect = this.canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const n = this.cfg.labels.length;
    if (x < pad.l || x > pad.l + W) {
      if (this.hover !== -1) {
        this.hover = -1;
        if (this.tip) this.tip.style.display = "none";
        this.draw();
      }
      return;
    }
    const i = clamp(Math.floor(((x - pad.l) / W) * n), 0, n - 1);
    if (i === this.hover) return;
    this.hover = i;
    this.draw();
    if (!this.tip) return;
    const rows = this.cfg.tip ? this.cfg.tip(i) : [];
    if (!rows || !rows.length) { this.tip.style.display = "none"; return; }
    this.tip.innerHTML =
      `<div class="t">${this.cfg.labels[i]}</div>` +
      rows.map((r) =>
        `<div class="r"><span>${esc(r[0])}</span><b style="color:${r[2] || "#fff"}">${esc(r[1])}</b></div>`
      ).join("");
    this.tip.style.display = "block";
    const tw = this.tip.offsetWidth;
    let left = pad.l + ((i + 0.5) / n) * W + 10;
    if (left + tw > this.w - 8) left = pad.l + ((i + 0.5) / n) * W - tw - 10;
    this.tip.style.left = Math.max(6, left) + "px";
    this.tip.style.top = "10px";
  }
}

/* ------------------------------ 数据切片 ------------------------------ */
function seriesData() {
  return (STATE.data && STATE.data.series) || {};
}
function metricsOf(seg) {
  return (STATE.data && STATE.data.metrics && STATE.data.metrics[seg]) || {};
}
function segmentBounds() {
  const s = seriesData();
  const dates = s.dates || [];
  const n = dates.length;
  const isEnd = (STATE.data && STATE.data.meta && STATE.data.meta.is_end) || "";
  if (!n) return { from: 0, to: 0, empty: true };
  if (STATE.seg === "样本内") {
    let to = -1;
    for (let i = 0; i < n; i++) if (isEnd && dates[i] <= isEnd) to = i;
    return { from: 0, to: to < 0 ? n - 1 : to, empty: to < 0 };
  }
  if (STATE.seg === "样本外") {
    let from = n;
    for (let i = 0; i < n; i++) if (isEnd && dates[i] > isEnd) { from = i; break; }
    if (from >= n) return { from: n - 1, to: n - 1, empty: true };
    return { from, to: n - 1, empty: false };
  }
  return { from: 0, to: n - 1, empty: false };
}
function sliceSeries() {
  const s = seriesData();
  const dates = s.dates || [];
  const b = segmentBounds();
  if (!dates.length) return null;
  const nav = (s.nav || []).slice(b.from, b.to + 1);
  const bench = (s.benchmark || []).slice(b.from, b.to + 1);
  const exposure = (s.exposure || []).slice(b.from, b.to + 1);
  const holdings = (s.n_holdings || []).slice(b.from, b.to + 1);
  const nav0 = nav.find(isNum);
  const bench0 = bench.find(isNum);
  const navN = nav.map((v) => (isNum(v) && isNum(nav0) && nav0 > 0 ? v / nav0 : null));
  const benchN = bench.map((v) => (isNum(v) && isNum(bench0) && bench0 > 0 ? v / bench0 : null));
  return {
    dates: dates.slice(b.from, b.to + 1),
    nav: navN,
    bench: benchN,
    exposure,
    holdings,
    from: b.from,
    to: b.to,
    empty: b.empty,
  };
}
function reductionBands(exposure) {
  const bands = [];
  let start = -1;
  exposure.forEach((v, i) => {
    const low = isNum(v) && v > 0.001 && v < 0.95;
    if (low && start < 0) start = i;
    if (!low && start >= 0) {
      if (i - start > 2) bands.push({ from: start, to: i - 1 });
      start = -1;
    }
  });
  if (start >= 0 && exposure.length - start > 2) bands.push({ from: start, to: exposure.length - 1 });
  return bands;
}
function sliceMarkers(sl) {
  const all = (STATE.data && STATE.data.markers) || [];
  if (all.length) {
    return all
      .filter((m) => m.i >= sl.from && m.i <= sl.to)
      .map((m) => ({ i: m.i - sl.from, label: m.label, kind: m.kind }));
  }
  /* 兼容旧版接口：没有 markers 时按仓位跳变粗略标记 */
  const exp = sl.exposure || [];
  const marks = [];
  let last = -1;
  exp.forEach((v, i) => {
    if (!isNum(v)) return;
    if (last >= 0 && isNum(exp[last]) && Math.abs(v - exp[last]) > 0.2) {
      marks.push({ i, label: v > exp[last] ? "加仓" : "降仓", kind: v > exp[last] ? "up" : "down" });
      last = i;
    } else if (last < 0 && v > 0.01) {
      marks.push({ i, label: "建仓", kind: "up" });
      last = i;
    }
  });
  return marks;
}

/* ------------------------------ 渲染：结论与卡片 ------------------------------ */
function renderVerdict() {
  const el = $("#verdictText");
  const sub = $("#verdictSub");
  const kpis = $("#verdictKpis");
  if (!el) return;
  const m = metricsOf("全样本");
  if (!isNum(m["累计收益率"])) {
    el.textContent = "正在等待回测结果…";
    sub.textContent = "";
    kpis.innerHTML = "";
    return;
  }
  const cum = m["累计收益率"];
  const bCum = m["基准累计收益率"];
  const ex = m["超额收益(年化)"];
  const mdd = m["最大回撤"];
  const bMdd = m["基准最大回撤"];
  const sharpe = m["夏普比率"];
  const bSharpe = m["基准夏普比率"];
  const ratio = isNum(mdd) && isNum(bMdd) && bMdd < 0 ? mdd / bMdd : null;
  const verdictWord = isNum(cum) && isNum(bCum)
    ? (cum > bCum ? "跑赢基准" : "落后基准")
    : "结果待定";

  el.innerHTML =
    `策略累计收益 <b>${pct(cum)}</b>，同期 ${esc((STATE.data.meta || {}).benchmark || "基准")} ` +
    `<b>${pct(bCum)}</b>；年化超额 <b>${pctSigned(ex)}</b>，最大回撤 ` +
    `<b>${pct(mdd)}</b>（基准 ${pct(bMdd)}${isNum(ratio) ? `，约为基准的 ${(ratio * 100).toFixed(0)}%` : ""}），` +
    `夏普 <b>${num(sharpe)}</b>（基准 ${num(bSharpe)}）。整体${verdictWord}。`;

  const oos = metricsOf("样本外");
  const oosText = isNum(oos["年化收益率"])
    ? `样本外年化 ${pct(oos["年化收益率"])}（基准 ${pct(oos["基准年化收益率"])}），` +
      `夏普 ${num(oos["夏普比率"])}（基准 ${num(oos["基准夏普比率"])}）。`
    : "样本外区间不足，暂不单独统计。";
  sub.textContent = oosText + ` 所有因子与择时参数只用样本内数据确定。`;

  const cost = (STATE.data && STATE.data.cost) || {};
  const kpiList = [
    ["年化超额", pctSigned(ex), isNum(ex) ? signClass(ex) : ""],
    ["信息比率", num(m["信息比率"]), ""],
    ["样本外年化", pct(oos["年化收益率"]), signClass(oos["年化收益率"])],
    ["成本拖累/年", pct(cost.annual), ""],
  ];
  kpis.innerHTML = kpiList.map(([k, v, cls]) =>
    `<div class="kpi"><span>${esc(k)}</span><b class="${cls}">${esc(v)}</b></div>`).join("");
}

function renderCards() {
  const m = metricsOf(STATE.seg);
  const cum = m["累计收益率"];
  const bCum = m["基准累计收益率"];
  const cagr = m["年化收益率"];
  const bCagr = m["基准年化收益率"];
  const mdd = m["最大回撤"];
  const bMdd = m["基准最大回撤"];
  const sharpe = m["夏普比率"];
  const bSharpe = m["基准夏普比率"];
  const ex = m["超额收益(年化)"];
  const vol = m["年化波动率"];
  const bVol = m["基准年化波动率"];
  const mddRatio = isNum(mdd) && isNum(bMdd) && bMdd < 0 ? mdd / bMdd : null;
  const sharpeRatio = isNum(sharpe) && isNum(bSharpe) && bSharpe > 0 ? sharpe / bSharpe : null;

  const cards = [
    ["累计收益率", pct(cum), `基准 ${pct(bCum)}（超额 ${pctSigned(isNum(cum) && isNum(bCum) ? cum - bCum : null)}）`, cum],
    ["年化收益率", pct(cagr), `基准 ${pct(bCagr)}，年化超额 ${pctSigned(ex)}`, cagr],
    ["最大回撤", pct(mdd), `基准 ${pct(bMdd)}${isNum(mddRatio) ? `，为基准的 ${(mddRatio * 100).toFixed(0)}%` : ""}`, mdd],
    ["夏普比率", num(sharpe), `基准 ${num(bSharpe)}${isNum(sharpeRatio) ? `，为基准的 ${sharpeRatio.toFixed(1)} 倍` : ""}`, sharpe],
    ["年化超额", pctSigned(ex), `信息比率 ${num(m["信息比率"])}`, ex],
    ["年化波动率", pct(vol), `基准 ${pct(bVol)}，跟踪误差 ${pct(m["跟踪误差"])}`, null],
    ["月胜率", pct(m["月胜率"], 1), `日胜率 ${pct(m["日胜率"], 1)}`, null],
    ["年化换手", isNum(m["年化换手率"]) ? m["年化换手率"].toFixed(1) + " 倍" : "—",
      `交易日 ${isNum(m["交易日数"]) ? m["交易日数"] : "—"} 天`, null],
  ];

  $("#cards").innerHTML = cards.map(([k, v, sub, raw]) => {
    const cls = raw === null ? "" : signClass(raw);
    return `<div class="card"><div class="k">${esc(k)}</div>
      <div class="v ${cls}">${esc(v)}</div><div class="x">${esc(sub)}</div></div>`;
  }).join("");
}

/* ------------------------------ 渲染：对比表 ------------------------------ */
const METRIC_ROWS = [
  { key: "累计收益率", bench: "基准累计收益率", fmt: pct },
  { key: "年化收益率", bench: "基准年化收益率", fmt: pct },
  { key: "年化波动率", bench: "基准年化波动率", fmt: pct, lowerBetter: true },
  { key: "最大回撤", bench: "基准最大回撤", fmt: pct },
  { key: "夏普比率", bench: "基准夏普比率", fmt: (v) => num(v, 3) },
  { key: "索提诺比率", bench: "基准索提诺比率", fmt: (v) => num(v, 3) },
  { key: "卡玛比率", fmt: (v) => num(v, 3) },
  { key: "月胜率", fmt: (v) => pct(v, 1) },
  { key: "年化换手率", fmt: (v) => (isNum(v) ? v.toFixed(1) + " 倍" : "—") },
  { key: "超额收益(年化)", fmt: pctSigned },
  { key: "信息比率", fmt: (v) => num(v, 3) },
  { key: "Beta", fmt: (v) => num(v, 3) },
  { key: "上行捕获", fmt: (v) => num(v, 3) },
  { key: "下行捕获", fmt: (v) => num(v, 3) },
];

function renderMetricTable() {
  const rows = METRIC_ROWS.map((d) => {
    const cells = SEGS.map((seg) => {
      const m = metricsOf(seg);
      const sv = m[d.key];
      const bv = d.bench ? m[d.bench] : null;
      const better = d.bench && isNum(sv) && isNum(bv)
        ? (d.lowerBetter ? sv < bv : sv > bv)
        : false;
      const sCell = `<td class="${better ? "win" : ""}">${d.fmt(sv)}</td>`;
      const bCell = d.bench ? `<td class="dim">${d.fmt(bv)}</td>` : '<td class="dim">—</td>';
      return sCell + bCell;
    }).join("");
    return `<tr><td>${esc(d.key)}</td>${cells}</tr>`;
  });
  $("#metricTable tbody").innerHTML = rows.join("");
}

/* ------------------------------ 渲染：热力图 ------------------------------ */
function monthlyStats(matrix) {
  let best = null;
  let worst = null;
  let positive = 0;
  let total = 0;
  matrix.forEach((row, y) => {
    row.forEach((v, m) => {
      if (!isNum(v)) return;
      total += 1;
      if (v > 0) positive += 1;
      if (!best || v > best.value) best = { value: v, year: y, month: m };
      if (!worst || v < worst.value) worst = { value: v, year: y, month: m };
    });
  });
  return { best, worst, positive, total };
}

function renderHeatmap() {
  const d = STATE.data || {};
  const src = STATE.monthlyView === "benchmark" && d.monthly_benchmark
    ? d.monthly_benchmark
    : (d.monthly || {});
  const years = src.years || [];
  const matrix = src.matrix || [];
  const el = $("#heatmap");
  if (!years.length) { el.innerHTML = ""; return; }

  let max = 0;
  matrix.forEach((row) => row.forEach((v) => { if (isNum(v)) max = Math.max(max, Math.abs(v)); }));
  max = max || 1;

  const cell = (v) => {
    if (!isNum(v)) return '<div class="hm-cell">–</div>';
    const t = Math.min(1, Math.abs(v) / max);
    const bg = v >= 0
      ? `rgba(192, 80, 77, ${0.08 + 0.72 * t})`
      : `rgba(18, 128, 92, ${0.08 + 0.72 * t})`;
    const color = t > 0.55 ? "#fff" : "#2b3445";
    return `<div class="hm-cell" style="background:${bg};color:${color}" title="${v.toFixed(2)}%">${v.toFixed(1)}</div>`;
  };

  let html = '<div class="hm-cell hm-head"></div>' +
    Array.from({ length: 12 }, (_, i) => `<div class="hm-cell hm-head">${i + 1}月</div>`).join("") +
    '<div class="hm-cell hm-head">全年</div>';

  years.forEach((year, i) => {
    html += `<div class="hm-cell hm-year">${year}</div>`;
    const row = matrix[i] || [];
    let prod = 1;
    let has = false;
    for (let j = 0; j < 12; j++) {
      const v = row[j];
      if (isNum(v)) { prod *= 1 + v / 100; has = true; }
      html += cell(v);
    }
    html += cell(has ? (prod - 1) * 100 : null);
  });
  el.innerHTML = html;

  const stats = monthlyStats(matrix);
  const note = $("#monthlyNote");
  if (note) {
    const label = STATE.monthlyView === "benchmark" ? "基准" : "策略";
    note.textContent =
      `${label}：正收益月份 ${stats.positive}/${stats.total}` +
      (stats.best ? `，最佳 ${stats.best.value.toFixed(1)}%（${years[stats.best.year]}-${String(stats.best.month + 1).padStart(2, "0")}）` : "") +
      (stats.worst ? `，最差 ${stats.worst.value.toFixed(1)}%（${years[stats.worst.year]}-${String(stats.worst.month + 1).padStart(2, "0")}）` : "") +
      "。";
  }
}

/* ------------------------------ 渲染：图表 ------------------------------ */
function renderCharts() {
  const d = STATE.data;
  if (!d || !d.series) return;
  const sl = sliceSeries();
  if (!sl || !sl.dates.length) return;
  const meta = d.meta || {};
  const dd = drawdownSeries(sl.nav);
  const bdd = drawdownSeries(sl.bench);
  const timingLow = isNum(meta.timing_low) ? meta.timing_low : 0.5;

  /* 净值 */
  navChart.set({
    dates: sl.dates,
    padRight: 64,
    series: [
      { data: sl.nav, color: COLORS.primary, width: 2.3 },
      { data: sl.bench, color: COLORS.secondary, width: 1.5, dash: [5, 3] },
    ],
    baseline: 1.0,
    areaBetween: true,
    splitDate: meta.is_end,
    bands: reductionBands(sl.exposure),
    markers: sliceMarkers(sl),
    yFmt: (v) => v.toFixed(2),
    endFmt: (v) => v.toFixed(2),
    endLabels: true,
    tip: (i) => {
      const rows = [
        ["策略", isNum(sl.nav[i]) ? sl.nav[i].toFixed(4) : "—", "#7fb3e0"],
        ["基准", isNum(sl.bench[i]) ? sl.bench[i].toFixed(4) : "—", "#e8a5a3"],
        ["仓位", isNum(sl.exposure[i]) ? (sl.exposure[i] * 100).toFixed(0) + "%" : "—", "#fff"],
      ];
      if (isNum(sl.nav[i]) && isNum(sl.bench[i])) {
        rows.push(["相对基准", ((sl.nav[i] / sl.bench[i] - 1) * 100).toFixed(2) + "%", "#ffd479"]);
      }
      if (isNum(sl.holdings[i])) rows.push(["持仓", `${sl.holdings[i]} 只`, "#fff"]);
      return rows;
    },
  });

  const navEnd = sl.nav.filter(isNum).slice(-1)[0];
  const benchEnd = sl.bench.filter(isNum).slice(-1)[0];
  const info = mddInfo(dd, sl.dates);
  const navStat = $("#navStat");
  if (navStat) {
    navStat.textContent =
      `${STATE.seg}：策略 ${pctSigned(isNum(navEnd) ? navEnd - 1 : null)}，基准 ${pctSigned(isNum(benchEnd) ? benchEnd - 1 : null)}` +
      (info ? `；策略最大回撤 ${pct(info.value)}（${info.peak} → ${info.trough}）` : "") +
      `；全样本共 ${d.rebalances || 0} 次调仓。`;
  }
  const navHint = $("#navHint");
  if (navHint) {
    navHint.textContent = STATE.seg === "全样本"
      ? `策略与基准均从 1.0 起算；${sl.dates[0]} ~ ${sl.dates[sl.dates.length - 1]}；底部绿色色带为减仓区间。`
      : `${STATE.seg}区间内重新归一至 1.0；底部绿色色带为减仓区间。`;
  }

  /* 回撤 */
  ddChart.set({
    dates: sl.dates,
    series: [
      { data: dd, color: COLORS.primary, width: 1.8 },
      { data: bdd, color: COLORS.secondary, width: 1.4, dash: [5, 3] },
    ],
    zeroLine: true,
    fillToBaseline: 0,
    fillColor: "rgba(31, 78, 121, 0.10)",
    splitDate: meta.is_end,
    yFmt: (v) => (v * 100).toFixed(0) + "%",
    tip: (i) => [
      ["策略回撤", isNum(dd[i]) ? (dd[i] * 100).toFixed(2) + "%" : "—", "#7fb3e0"],
      ["基准回撤", isNum(bdd[i]) ? (bdd[i] * 100).toFixed(2) + "%" : "—", "#e8a5a3"],
    ],
  });
  const ddStat = $("#ddStat");
  if (ddStat) {
    const si = mddInfo(dd, sl.dates);
    const bi = mddInfo(bdd, sl.dates);
    ddStat.textContent =
      `策略 ${si ? pct(si.value) + `（${si.peak} → ${si.trough}）` : "—"}；` +
      `基准 ${bi ? pct(bi.value) + `（${bi.peak} → ${bi.trough}）` : "—"}。`;
  }

  /* 仓位 */
  expoChart.set({
    dates: sl.dates,
    series: [{ data: sl.exposure, color: COLORS.primary, width: 1.8 }],
    zeroLine: true,
    fillToBaseline: 0,
    fillColor: "rgba(31, 78, 121, 0.08)",
    hlines: [
      { v: 1.0, color: "#b0b8c1", dash: [4, 4], label: "满仓" },
      { v: timingLow, color: COLORS.green, dash: [4, 4], label: `减仓线 ${(timingLow * 100).toFixed(0)}%` },
    ],
    splitDate: meta.is_end,
    yFmt: (v) => (v * 100).toFixed(0) + "%",
    tip: (i) => {
      const rows = [
        ["股票仓位", isNum(sl.exposure[i]) ? (sl.exposure[i] * 100).toFixed(1) + "%" : "—", "#7fb3e0"],
      ];
      if (isNum(sl.holdings[i])) rows.push(["持仓只数", `${sl.holdings[i]} 只`, "#fff"]);
      return rows;
    },
  });
  const expoStat = $("#expoStat");
  if (expoStat) {
    const vals = sl.exposure.filter(isNum);
    const avg = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
    const lowRatio = vals.length ? vals.filter((v) => v < 0.95).length / vals.length : null;
    const last = vals.length ? vals[vals.length - 1] : null;
    expoStat.textContent =
      `平均仓位 ${pct(avg)}，降仓时间占比 ${pct(lowRatio)}` +
      `；期末仓位 ${pct(last)}；择时为 MA${meta.timing_ma || 120}，均线下方降至 ${pct(timingLow, 0)}。`;
  }

  /* 年度收益 */
  const annual = d.annual || {};
  annualChart.set({
    labels: (annual.years || []).map(String),
    series: [
      { data: annual.strategy || [], color: COLORS.primary },
      { data: annual.benchmark || [], color: COLORS.secondary },
    ],
    tip: (i) => [
      ["策略", pctPoint((annual.strategy || [])[i]), "#7fb3e0"],
      ["基准", pctPoint((annual.benchmark || [])[i]), "#e8a5a3"],
    ],
  });
  const annualStat = $("#annualStat");
  if (annualStat && annual.years) {
    let win = 0;
    let total = 0;
    annual.strategy.forEach((v, i) => {
      if (isNum(v) && isNum(annual.benchmark[i])) {
        total += 1;
        if (v > annual.benchmark[i]) win += 1;
      }
    });
    const diff = annual.strategy.map((v, i) => (isNum(v) && isNum(annual.benchmark[i]) ? v - annual.benchmark[i] : null));
    const best = diff.reduce((acc, v, i) => (isNum(v) && (!acc || v > acc.v) ? { v, i } : acc), null);
    annualStat.textContent =
      `策略有 ${win}/${total} 个自然年跑赢基准` +
      (best ? `，超额最大的年份是 ${annual.years[best.i]}（+${best.v.toFixed(1)} 个百分点）` : "") +
      "。";
  }

  /* 相对强弱 */
  const ratio = sl.nav.map((v, i) => (isNum(v) && isNum(sl.bench[i]) && sl.bench[i] > 0 ? v / sl.bench[i] : null));
  const base = ratio.find(isNum);
  const ratioN = ratio.map((v) => (isNum(v) && isNum(base) && base > 0 ? v / base : null));
  relChart.set({
    dates: sl.dates,
    series: [{ data: ratioN, color: COLORS.primary, width: 1.9 }],
    baseline: 1.0,
    fillToBaseline: 1.0,
    fillColor: "rgba(31, 78, 121, 0.07)",
    splitDate: meta.is_end,
    yFmt: (v) => v.toFixed(2),
    tip: (i) => [
      ["相对强弱", isNum(ratioN[i]) ? ratioN[i].toFixed(4) : "—", "#7fb3e0"],
      ["累计超额", isNum(ratioN[i]) ? ((ratioN[i] - 1) * 100).toFixed(2) + "%" : "—", "#ffd479"],
    ],
  });
  const relStat = $("#relStat");
  if (relStat) {
    const vals = ratioN.filter(isNum);
    const last = vals.length ? vals[vals.length - 1] : null;
    const max = vals.length ? Math.max(...vals) : null;
    const min = vals.length ? Math.min(...vals) : null;
    relStat.textContent =
      `期末相对强弱 ${num(last, 3)}（累计超额 ${pctSigned(isNum(last) ? last - 1 : null)}）` +
      (isNum(max) && isNum(min) ? `；区间最高 ${num(max, 3)}、最低 ${num(min, 3)}。` : "。");
  }
}

/* ------------------------------ 渲染：表格与说明 ------------------------------ */
function renderFactors() {
  const data = STATE.data || {};
  const selected = new Set((data.selected || []).map((s) => s.factor));
  const weight = {};
  (data.selected || []).forEach((s) => { weight[s.factor] = s.weight; });

  const list = (data.factors || []).slice().sort(
    (a, b) => Math.abs(b.icir || 0) - Math.abs(a.icir || 0));
  const maxAbs = list.reduce((m, f) => Math.max(m, Math.abs(f.icir || 0)), 0) || 1;

  $("#factorTable tbody").innerHTML = list.map((f) => {
    const on = selected.has(f.factor);
    const ic = isNum(f.mean_ic) ? (f.mean_ic > 0 ? "+" : "") + f.mean_ic.toFixed(4) : "—";
    const icir = isNum(f.icir) ? (f.icir > 0 ? "+" : "") + f.icir.toFixed(3) : "—";
    const t = isNum(f.t_stat) ? f.t_stat.toFixed(2) : "—";
    const w = on ? weight[f.factor].toFixed(3) : "—";
    const barW = isNum(f.icir) ? Math.max(3, (Math.abs(f.icir) / maxAbs) * 100) : 0;
    return `<tr class="${on ? "on" : "off"}">
      <td>${esc(f.factor)}${on ? " ●" : ""}</td>
      <td>${ic}</td>
      <td><span class="mini-bar"><i style="width:${barW}%"></i></span>${icir}</td>
      <td>${t}</td><td>${w}</td></tr>`;
  }).join("");
}

function renderHoldings() {
  const list = (STATE.data && STATE.data.holdings) || [];
  const maxW = list.reduce((m, h) => Math.max(m, h.weight || 0), 0) || 1;
  $("#holdTable tbody").innerHTML = list.map((h) =>
    `<tr><td>${esc(h.symbol)}</td><td>${esc(h.name)}</td>
      <td><span class="mini-bar"><i style="width:${((h.weight || 0) / maxW) * 100}%"></i></span>${pct(h.weight, 2)}</td></tr>`
  ).join("");
  const note = $("#holdHint");
  if (note) {
    if (!list.length) {
      note.textContent = "最近一个调仓日为空仓或暂无持仓。";
    } else {
      const total = list.reduce((sum, h) => sum + (isNum(h.weight) ? h.weight : 0), 0);
      note.textContent = `最近一个调仓日，共 ${list.length} 只，合计目标仓位 ${(total * 100).toFixed(0)}%。`;
    }
  }
}

function renderTrades() {
  const all = (STATE.data && STATE.data.trades) || [];
  const side = STATE.tradeSide;
  const list = all.filter((t) => {
    if (side === "buy") return t.side === "BUY";
    if (side === "sell") return t.side === "SELL";
    return true;
  });
  $("#tradeTable tbody").innerHTML = list.slice().reverse().map((t) => {
    const isBuy = t.side === "BUY";
    const date = String(t.date || "").slice(0, 10);
    return `<tr><td>${esc(date)}</td><td>${esc(t.symbol)}</td><td>${esc(t.name)}</td>
      <td class="${isBuy ? "buy" : "sell"}">${isBuy ? "买入" : "卖出"}</td>
      <td>${t.shares}</td>
      <td>${num(t.price)}</td>
      <td>${money(t.notional)}</td>
      <td>${num(t.total_fee, 1)}</td></tr>`;
  }).join("");
  const hint = $("#tradeHint");
  if (hint) {
    hint.textContent = `按成交时间倒序，当前显示 ${list.length} 笔（接口最多返回最近 80 笔）`;
  }
}

function renderExcess() {
  const el = $("#excessText");
  if (!el) return;
  const m = metricsOf("全样本");
  if (!isNum(m["累计收益率"])) { el.textContent = "等待回测结果…"; return; }
  const ex = m["超额收益(年化)"];
  const ir = m["信息比率"];
  const beta = m["Beta"];
  const up = m["上行捕获"];
  const down = m["下行捕获"];
  const parts = [
    `全样本年化超额 ${pctSigned(ex)}，策略 ${pct(m["年化收益率"])}，基准 ${pct(m["基准年化收益率"])}；` +
    `累计超额 ${pctSigned(m["超额收益(累计)"])}。`,
  ];
  if (isNum(ir)) {
    const judge = Math.abs(ir) < 0.1
      ? "主动收益与跟踪误差基本相抵"
      : ir > 0.3 ? "主动收益相对稳定"
        : ir > 0 ? "主动收益偏弱但为正"
          : "主动收益为负，超额不稳定";
    parts.push(`信息比率 ${num(ir)}，${judge}。`);
  }
  if (isNum(beta)) {
    parts.push(`Beta ${num(beta)}，低于 1 的部分主要来自择时降仓，收益里的市场 beta 成分被压低。`);
  }
  if (isNum(up) && isNum(down)) {
    parts.push(`上行捕获 ${num(up)}、下行捕获 ${num(down)}，` +
      `${down < up ? "下跌时比上涨时更抗跌" : "上涨跟得上，下跌也扛得多"}。`);
  }
  if (isNum(m["最大回撤"]) && isNum(m["基准最大回撤"])) {
    parts.push(`最大回撤 ${pct(m["最大回撤"])}，基准 ${pct(m["基准最大回撤"])}。`);
  }
  el.textContent = parts.join("");

  const cost = (STATE.data && STATE.data.cost) || {};
  const grid = $("#costGrid");
  if (grid) {
    if (!cost.total) {
      grid.innerHTML = "";
    } else {
      grid.innerHTML = [
        ["佣金", money(cost.commission)],
        ["印花税", money(cost.stamp_duty)],
        ["滑点", money(cost.slippage)],
        ["费用合计", `${money(cost.total)}（年化 ${pct(cost.annual)}）`],
        ["成交笔数", `${cost.trades || 0} 笔`],
      ].map(([k, v]) => `<div class="cost-item"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join("");
    }
  }
}

function renderMeta() {
  const data = STATE.data || {};
  const m = data.meta || {};
  const adjLabel = !m.adjust || m.adjust === "day"
    ? "不复权" : (m.adjust === "qfq" ? "前复权" : "后复权");
  const rows = [
    ["数据来源", `${m.provider || "akshare"} 日线（${adjLabel}），股票池取自行情接口`],
    ["股票池", `${m.universe_size || "—"} 只 A 股（请求 ${m.universe_requested || "—"} 只，其余因历史不足或质量检查剔除）`],
    ["数据区间", `${m.data_start || "—"} ~ ${m.data_end || "—"}`],
    ["回测区间", `${m.start || "—"} ~ ${m.end || "—"}`],
    ["样本内外", `样本内 ${m.start || "—"} ~ ${m.is_end || "—"}，之后为样本外`],
    ["基准", m.benchmark || "沪深300"],
    ["调仓", `${data.rebalances || 0} 次，月末最后交易日信号、次一交易日开盘成交`],
    ["持仓", `综合得分前 ${m.top_n || "—"} 只等权，单票上限 ${pct(m.max_weight, 0)}`],
    ["因子", `${m.factor_k || "—"} 个入选因子，${m.weighting === "equal" ? "等权" : "ICIR 加权"}`],
    ["择时", m.timing
      ? `沪深300 收盘价与 MA${m.timing_ma || 120} 比较，下方降至 ${pct(m.timing_low, 0)} 仓位`
      : "未启用"],
    ["期末仓位", pct(m.current_exposure)],
    ["交易成本", "佣金万 2.5（单笔最低 5 元）、卖出印花税、单边滑点 5bp、100 股整手"],
  ];
  $("#metaList").innerHTML = rows.map(([k, v]) =>
    `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
}

/* ------------------------------ 汇总渲染 ------------------------------ */
function renderAll() {
  if (!STATE.data) return;
  renderVerdict();
  renderCards();
  renderMetricTable();
  renderHeatmap();
  renderFactors();
  renderHoldings();
  renderTrades();
  renderMeta();
  renderExcess();
  renderCharts();

  const m = STATE.data.meta || {};
  $("#dataset").textContent =
    `${m.universe_size || "—"} 只标的 · ${m.start || "—"} ~ ${m.end || "—"} · ` +
    `基准 ${m.benchmark || "—"} · Top${m.top_n || "—"}`;

  const note = $("#segNote");
  if (note) {
    const mm = metricsOf(STATE.seg);
    note.textContent =
      `当前显示「${STATE.seg}」：${isNum(mm["交易日数"]) ? mm["交易日数"] : "—"} 个交易日；` +
      `样本内截至 ${m.is_end || "—"}，样本外自其后一个交易日起。` +
      (STATE.seg === "样本外" && !isNum(mm["累计收益率"]) ? "（样本外区间不足，暂无法统计）" : "");
  }
}

/* ------------------------------ 交互 ------------------------------ */
const navChart = new SeriesChart($("#navChart"), $("#navTip"));
const ddChart = new SeriesChart($("#ddChart"), $("#ddTip"));
const expoChart = new SeriesChart($("#expoChart"), $("#expoTip"));
const relChart = new SeriesChart($("#relChart"), $("#relTip"));
const annualChart = new BarChart($("#annualChart"), $("#annualTip"));

function setStatus(text, kind) {
  const status = $("#status");
  const badge = $("#badge");
  if (status) status.textContent = text;
  if (!badge) return;
  badge.className = "pill" + (kind ? " " + kind : "");
  badge.textContent = kind === "ok" ? "已计算"
    : kind === "err" ? "出错"
      : (text.indexOf("计算") === 0 ? "计算中" : "未运行");
}

function readParams() {
  const f = new FormData($("#form"));
  return {
    start: f.get("start"),
    is_end: f.get("is_end"),
    top_n: Number(f.get("top_n")),
    factor_k: Number(f.get("factor_k")),
    weighting: f.get("weighting"),
    timing: f.get("timing") === "on",
    capital: Number(f.get("capital")),
  };
}

async function fetchJSON(url, options) {
  const res = await fetch(url, options);
  let data = null;
  try { data = await res.json(); } catch (err) { data = null; }
  if (!res.ok) {
    throw new Error((data && data.error) || `${res.status} ${res.statusText}`);
  }
  return data;
}

async function runBacktest(event) {
  if (event) event.preventDefault();
  const btn = $("#run");
  const oldText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "计算中…";
  setStatus("计算中…", "");
  $("#verdictText").textContent = "正在重新回测：加载数据 → 计算因子 → 生成信号 → 撮合 → 统计指标…";
  const t0 = performance.now();

  try {
    const data = await fetchJSON("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(readParams()),
    });
    STATE.data = data;
    STATE.seg = "全样本";
    $$("#seg button").forEach((b) => b.classList.toggle("on", b.dataset.seg === STATE.seg));
    renderAll();
    setStatus(`完成 · ${((performance.now() - t0) / 1000).toFixed(1)}s`, "ok");
  } catch (err) {
    setStatus("失败", "err");
    $("#cards").innerHTML = `<div class="err">回测失败：${esc(err.message)}</div>`;
    $("#verdictText").textContent = `回测失败：${err.message}`;
    $("#verdictSub").textContent = "请检查输入区间，或确认 data/raw 下的行情缓存是否完整。";
    console.error(err);
  } finally {
    btn.disabled = false;
    btn.textContent = oldText;
  }
}

function bindSeg() {
  $$("#seg button").forEach((btn) => {
    btn.addEventListener("click", () => {
      STATE.seg = btn.dataset.seg;
      $$("#seg button").forEach((b) => b.classList.toggle("on", b === btn));
      renderAll();
    });
  });
}

function bindMonthly() {
  $$("#monthlySeg button").forEach((btn) => {
    btn.addEventListener("click", () => {
      STATE.monthlyView = btn.dataset.view;
      $$("#monthlySeg button").forEach((b) => b.classList.toggle("on", b === btn));
      renderHeatmap();
    });
  });
}

function bindTradeSide() {
  const sel = $("#tradeSide");
  if (!sel) return;
  sel.addEventListener("change", () => {
    STATE.tradeSide = sel.value;
    renderTrades();
  });
}

function bindReset() {
  const btn = $("#reset");
  if (!btn) return;
  btn.addEventListener("click", () => {
    const form = $("#form");
    const m = (STATE.data && STATE.data.meta) || {};
    form.elements.start.value = "2019-01-01";
    form.elements.is_end.value = "2022-12-31";
    form.elements.top_n.value = 30;
    form.elements.factor_k.value = 5;
    form.elements.weighting.value = "icir";
    form.elements.timing.checked = true;
    form.elements.capital.value = 1000000;
    $("#formNote").textContent = m.is_end
      ? `已恢复默认参数；最近一次结果的样本内结束日为 ${m.is_end}。`
      : "已恢复默认参数。";
  });
}

function bindSubnav() {
  const links = $$(".subnav a");
  const sections = links.map((a) => $(a.getAttribute("href"))).filter(Boolean);
  const activate = (id) => {
    links.forEach((a) => a.classList.toggle("on", a.getAttribute("href") === "#" + id));
  };
  const update = () => {
    const line = window.scrollY + 130;
    let current = sections.length ? sections[0].id : "";
    sections.forEach((s) => {
      if (s.offsetTop <= line) current = s.id;
    });
    activate(current);
  };
  window.addEventListener("scroll", update, { passive: true });
  links.forEach((a) => {
    a.addEventListener("click", () => {
      activate(a.getAttribute("href").slice(1));
      /* 点击后浏览器会跳转，延迟一帧再按滚动位置校正 */
      window.setTimeout(update, 0);
    });
  });
  update();
}

function bindResize() {
  let timer = 0;
  window.addEventListener("resize", () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => {
      if (!STATE.data) return;
      navChart.draw();
      ddChart.draw();
      expoChart.draw();
      relChart.draw();
      annualChart.draw();
    }, 120);
  });
}

/* ------------------------------ 启动 ------------------------------ */
async function boot() {
  bindSeg();
  bindMonthly();
  bindTradeSide();
  bindReset();
  bindSubnav();
  bindResize();
  $("#form").addEventListener("submit", runBacktest);

  try {
    const meta = await fetchJSON("/api/meta");
    $("#dataset").textContent =
      `${meta.universe_size} 只标的 · ${meta.data_start} ~ ${meta.data_end} · 基准 ${meta.benchmark}`;
  } catch (err) {
    $("#dataset").textContent = "数据未就绪，请先执行 python scripts/download_data.py";
  }

  try {
    const data = await fetchJSON("/api/result");
    if (data && data.metrics && data.series) {
      STATE.data = data;
      renderAll();
      setStatus("已加载", "ok");
      return;
    }
  } catch (err) {
    /* 还没有结果，直接跑一次 */
  }

  await runBacktest();
}

boot();
