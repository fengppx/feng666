/* QLab 看板前端：原生 JS + Canvas，无任何第三方依赖 */

"use strict";

const $ = (s) => document.querySelector(s);
const SEGS = ["全样本", "样本内", "样本外"];

const STATE = { data: null, seg: "全样本" };

/* ------------------------------ 格式化 ------------------------------ */
const isNum = (v) => typeof v === "number" && isFinite(v);

function pct(v, d = 2) {
  return isNum(v) ? (v * 100).toFixed(d) + "%" : "—";
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

/* ------------------------------ 折线图 ------------------------------ */
class SeriesChart {
  constructor(canvas, tipEl) {
    this.canvas = canvas;
    this.tip = tipEl;
    this.cfg = null;
    this.hover = -1;
    this.w = 0;
    this.h = 0;
    this.pad = { l: 58, r: 16, t: 16, b: 28 };

    canvas.addEventListener("mousemove", (e) => this._onMove(e));
    canvas.addEventListener("mouseleave", () => {
      this.hover = -1;
      this.tip.style.display = "none";
      this.draw();
    });
  }

  set(cfg) {
    this.cfg = cfg;
    this.hover = -1;
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

  draw() {
    const cfg = this.cfg;
    if (!cfg || !cfg.dates || !cfg.dates.length) return;

    const ctx = this._prep();
    const { l: padL, r: padR, t: padT, b: padB } = this.pad;
    const W = Math.max(10, this.w - padL - padR);
    const H = Math.max(10, this.h - padT - padB);
    const dates = cfg.dates;
    const n = dates.length;

    const values = [];
    cfg.series.forEach((s) => s.data.forEach((v) => { if (isNum(v)) values.push(v); }));
    if (!values.length) return;

    let lo = Math.min(...values);
    let hi = Math.max(...values);
    if (cfg.zeroLine) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
    if (!isFinite(lo) || !isFinite(hi)) return;
    if (hi - lo < 1e-9) { hi += 0.01; lo -= 0.01; }
    const padY = (hi - lo) * 0.08;
    lo -= padY;
    /* 回撤图顶端贴住 0，避免 0 以上留白把刻度推成“1%” */
    hi += cfg.zeroLine ? padY * 0.15 : padY;

    const X = (i) => padL + (n <= 1 ? 0 : (i / (n - 1)) * W);
    const Y = (v) => padT + H - ((v - lo) / (hi - lo)) * H;

    /* 样本内外分界 */
    let splitIdx = -1;
    if (cfg.splitDate) {
      for (let i = 0; i < n; i++) {
        if (dates[i] > cfg.splitDate) { splitIdx = i; break; }
      }
    }
    if (splitIdx > 0) {
      ctx.fillStyle = "rgba(31, 78, 121, 0.045)";
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
      ctx.fillText("样本外 →", padL + W - 6, padT + 14);
    }

    /* 横向网格与 y 轴 */
    ctx.font = "11px system-ui, sans-serif";
    ctx.textBaseline = "middle";
    const ticks = 5;
    for (let i = 0; i <= ticks; i++) {
      const v = lo + ((hi - lo) * i) / ticks;
      const y = Y(v);
      ctx.strokeStyle = "#eef2f7";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(padL + W, y);
      ctx.stroke();
      ctx.fillStyle = "#8a95a3";
      ctx.textAlign = "right";
      ctx.fillText((cfg.yFmt || ((x) => x.toFixed(2)))(v), padL - 8, y);
    }

    /* 基准线（净值图用 1.0，回撤图用 0） */
    if (cfg.baseLine !== undefined && cfg.baseLine >= lo && cfg.baseLine <= hi) {
      ctx.save();
      ctx.setLineDash([5, 4]);
      ctx.strokeStyle = "#cfd6e0";
      ctx.beginPath();
      ctx.moveTo(padL, Y(cfg.baseLine));
      ctx.lineTo(padL + W, Y(cfg.baseLine));
      ctx.stroke();
      ctx.restore();
    }

    /* x 轴年份 */
    ctx.fillStyle = "#8a95a3";
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    let lastYear = "";
    for (let i = 0; i < n; i++) {
      const y = dates[i].slice(0, 4);
      if (y !== lastYear) {
        if (X(i) > padL + 14 && X(i) < padL + W - 14) {
          ctx.fillText(y, X(i), padT + H + 8);
        }
        lastYear = y;
      }
    }

    /* 曲线 */
    cfg.series.forEach((s) => {
      ctx.beginPath();
      ctx.strokeStyle = s.color;
      ctx.lineWidth = s.width || 2;
      ctx.lineJoin = "round";
      let started = false;
      for (let i = 0; i < n; i++) {
        const v = s.data[i];
        if (!isNum(v)) continue;
        if (!started) { ctx.moveTo(X(i), Y(v)); started = true; }
        else ctx.lineTo(X(i), Y(v));
      }
      ctx.stroke();
    });

    /* 悬停十字线 */
    const hi2 = this.hover;
    if (hi2 >= 0 && hi2 < n) {
      const x = X(hi2);
      ctx.save();
      ctx.strokeStyle = "#9aa8b8";
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(x, padT);
      ctx.lineTo(x, padT + H);
      ctx.stroke();
      ctx.restore();
      cfg.series.forEach((s) => {
        const v = s.data[hi2];
        if (!isNum(v)) return;
        ctx.beginPath();
        ctx.fillStyle = s.color;
        ctx.arc(x, Y(v), 3.5, 0, Math.PI * 2);
        ctx.fill();
        ctx.beginPath();
        ctx.strokeStyle = "#fff";
        ctx.lineWidth = 1.5;
        ctx.arc(x, Y(v), 3.5, 0, Math.PI * 2);
        ctx.stroke();
      });
    }
  }

  _onMove(e) {
    if (!this.cfg || !this.w) return;
    const rect = this.canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const padL = this.pad.l;
    const W = this.w - padL - this.pad.r;
    const n = this.cfg.dates.length;
    if (n < 2 || x < padL - 8 || x > padL + W + 8) {
      if (this.hover !== -1) { this.hover = -1; this.tip.style.display = "none"; this.draw(); }
      return;
    }
    const t = Math.max(0, Math.min(1, (x - padL) / W));
    const i = Math.round(t * (n - 1));
    if (i === this.hover) return;
    this.hover = i;
    this.draw();

    const rows = this.cfg.tip ? this.cfg.tip(i) : [];
    if (!rows.length) { this.tip.style.display = "none"; return; }
    this.tip.innerHTML =
      `<div class="t">${this.cfg.dates[i]}</div>` +
      rows.map((r) =>
        `<div class="r"><span>${r[0]}</span><b style="color:${r[2] || "#fff"}">${r[1]}</b></div>`
      ).join("");
    this.tip.style.display = "block";
    const tw = this.tip.offsetWidth;
    let left = padL + t * W + 14;
    if (left + tw > this.w - 8) left = padL + t * W - tw - 14;
    this.tip.style.left = Math.max(6, left) + "px";
    this.tip.style.top = "10px";
  }
}

/* ------------------------------ 渲染 ------------------------------ */
const METRIC_ROWS = [
  { key: "累计收益率", bench: "基准累计收益率", fmt: (v) => pct(v) },
  { key: "年化收益率", bench: "基准年化收益率", fmt: (v) => pct(v) },
  { key: "年化波动率", bench: "基准年化波动率", fmt: (v) => pct(v) },
  { key: "最大回撤", bench: "基准最大回撤", fmt: (v) => pct(v) },
  { key: "夏普比率", bench: "基准夏普比率", fmt: (v) => num(v) },
  { key: "索提诺比率", fmt: (v) => num(v) },
  { key: "卡玛比率", fmt: (v) => num(v) },
  { key: "月胜率", fmt: (v) => pct(v, 1) },
  { key: "年化换手率", fmt: (v) => (isNum(v) ? v.toFixed(1) + "x" : "—") },
  { key: "超额收益(年化)", fmt: (v) => pctSigned(v) },
  { key: "信息比率", fmt: (v) => num(v) },
  { key: "Beta", fmt: (v) => num(v) },
];

function segMetrics(seg) {
  return (STATE.data && STATE.data.metrics && STATE.data.metrics[seg]) || {};
}

function renderCards() {
  const m = segMetrics(STATE.seg);
  const cards = [
    ["累计收益率", pct(m["累计收益率"]), `基准 ${pct(m["基准累计收益率"])}`, m["累计收益率"]],
    ["年化收益率", pct(m["年化收益率"]), `基准 ${pct(m["基准年化收益率"])}`, m["年化收益率"]],
    ["最大回撤", pct(m["最大回撤"]), `基准 ${pct(m["基准最大回撤"])}`, m["最大回撤"]],
    ["夏普比率", num(m["夏普比率"]), `基准 ${num(m["基准夏普比率"])}`, m["夏普比率"]],
    ["年化超额", pctSigned(m["超额收益(年化)"]), `信息比率 ${num(m["信息比率"])}`, m["超额收益(年化)"]],
    ["年化波动率", pct(m["年化波动率"]), `基准 ${pct(m["基准年化波动率"])}`, null],
    ["月胜率", pct(m["月胜率"], 1), `日胜率 ${pct(m["日胜率"], 1)}`, null],
    ["年化换手", (isNum(m["年化换手率"]) ? m["年化换手率"].toFixed(1) + "x" : "—"),
      `交易日 ${isNum(m["交易日数"]) ? m["交易日数"] : "—"} 天`, null],
  ];

  $("#cards").innerHTML = cards.map(([k, v, sub, raw]) => {
    const c = raw === null ? "" : signClass(raw);
    return `<div class="card"><div class="k">${k}</div>
      <div class="v ${c}">${v}</div><div class="x">${sub}</div></div>`;
  }).join("");
}

function renderMetricTable() {
  const rows = METRIC_ROWS.map((d) => {
    const cells = SEGS.map((seg) => {
      const m = segMetrics(seg);
      const sv = d.fmt(m[d.key]);
      const bv = d.bench ? d.fmt(m[d.bench]) : '<span class="dim">—</span>';
      return `<td>${sv}</td><td class="dim">${bv}</td>`;
    }).join("");
    return `<tr><td>${d.key}</td>${cells}</tr>`;
  });
  $("#metricTable tbody").innerHTML = rows.join("");
}

function renderHeatmap(monthly) {
  const years = (monthly && monthly.years) || [];
  const matrix = (monthly && monthly.matrix) || [];
  const el = $("#heatmap");

  let max = 0;
  matrix.forEach((row) => row.forEach((v) => { if (isNum(v)) max = Math.max(max, Math.abs(v)); }));
  max = max || 1;

  const cell = (v) => {
    if (!isNum(v)) return `<div class="hm-cell">–</div>`;
    const t = Math.min(1, Math.abs(v) / max);
    const bg = v >= 0
      ? `rgba(192, 80, 77, ${0.08 + 0.72 * t})`
      : `rgba(18, 128, 92, ${0.08 + 0.72 * t})`;
    const color = t > 0.55 ? "#fff" : "#2b3445";
    return `<div class="hm-cell" style="background:${bg};color:${color}" title="${v.toFixed(2)}%">${v.toFixed(1)}</div>`;
  };

  let html = `<div class="hm-cell hm-head"></div>` +
    Array.from({ length: 12 }, (_, i) => `<div class="hm-cell hm-head">${i + 1}月</div>`).join("") +
    `<div class="hm-cell hm-head">全年</div>`;

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
}

function renderFactors() {
  const data = STATE.data || {};
  const selected = new Set((data.selected || []).map((s) => s.factor));
  const weight = {};
  (data.selected || []).forEach((s) => { weight[s.factor] = s.weight; });

  const list = (data.factors || []).slice().sort(
    (a, b) => Math.abs(b.icir || 0) - Math.abs(a.icir || 0));

  $("#factorTable tbody").innerHTML = list.map((f) => {
    const on = selected.has(f.factor);
    const ic = isNum(f.mean_ic) ? (f.mean_ic > 0 ? "+" : "") + f.mean_ic.toFixed(4) : "—";
    const icir = isNum(f.icir) ? (f.icir > 0 ? "+" : "") + f.icir.toFixed(3) : "—";
    const t = isNum(f.t_stat) ? f.t_stat.toFixed(2) : "—";
    const w = on ? weight[f.factor].toFixed(2) : "—";
    return `<tr style="opacity:${on ? 1 : 0.55}">
      <td>${f.factor}${on ? " ●" : ""}</td>
      <td>${ic}</td><td>${icir}</td><td>${t}</td><td>${w}</td></tr>`;
  }).join("");
}

function renderHoldings() {
  const list = (STATE.data && STATE.data.holdings) || [];
  $("#holdTable tbody").innerHTML = list.map((h) =>
    `<tr><td>${h.symbol}</td><td>${h.name}</td><td>${pct(h.weight)}</td></tr>`).join("");
  const note = $("#holdHint");
  if (note) {
    if (!list.length) {
      note.textContent = "最近一个调仓日，暂无持仓";
    } else {
      const total = list.reduce((sum, h) => sum + (isNum(h.weight) ? h.weight : 0), 0);
      note.textContent = `最近一个调仓日，共 ${list.length} 只，合计仓位 ${(total * 100).toFixed(0)}%`;
    }
  }
}

function renderTrades() {
  const list = (STATE.data && STATE.data.trades) || [];
  $("#tradeTable tbody").innerHTML = list.slice().reverse().map((t) => {
    const side = t.side === "BUY" ? "买入" : "卖出";
    const cls = t.side === "BUY" ? "buy" : "sell";
    const date = String(t.date || "").slice(0, 10);
    return `<tr><td>${date}</td><td>${t.symbol}</td><td>${t.name}</td>
      <td class="${cls}">${side}</td>
      <td>${t.shares}</td>
      <td>${num(t.price)}</td>
      <td>${money(t.notional)}</td>
      <td>${num(t.total_fee, 1)}</td></tr>`;
  }).join("");
}

function renderMeta() {
  const data = STATE.data || {};
  const m = data.meta || {};
  const rows = [
    ["数据来源", "腾讯日线接口（不复权），股票池取自新浪行情列表"],
    ["股票池", `${m.universe_size || "—"} 只 A 股`],
    ["基准", m.benchmark || "沪深300"],
    ["回测区间", `${m.start || "—"} ~ ${m.end || "—"}`],
    ["样本内外", `样本内截至 ${m.is_end || "—"}，之后为样本外`],
    ["调仓", `每月最后一个交易日出信号，次一交易日开盘成交`],
    ["持仓", `综合得分前 ${m.top_n || "—"} 只等权，单票上限 8%`],
    ["因子加权", m.weighting === "equal" ? "等权" : "ICIR 加权"],
    ["择时", m.timing ? "沪深300 收盘价与 120 日均线比较，下方降至 30% 仓位" : "未启用"],
    ["交易成本", "佣金万 2.5（单笔最低 5 元）、卖出印花税、单边滑点 5bp、100 股整手"],
    ["调仓次数", `${data.rebalances || 0} 次`],
  ];
  $("#metaList").innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");
}

function renderCharts() {
  const d = STATE.data;
  if (!d || !d.series) return;
  const s = d.series;
  const split = (d.meta && d.meta.is_end) || null;

  navChart.set({
    dates: s.dates,
    series: [
      { data: s.nav, color: "#1f4e79", width: 2.2 },
      { data: s.benchmark, color: "#c0504d", width: 1.6 },
    ],
    baseLine: 1.0,
    splitDate: split,
    yFmt: (v) => v.toFixed(2),
    tip: (i) => [
      ["策略", isNum(s.nav[i]) ? s.nav[i].toFixed(4) : "—", "#7fb3e0"],
      ["基准", isNum(s.benchmark[i]) ? s.benchmark[i].toFixed(4) : "—", "#e8a5a3"],
      ["仓位", isNum(s.exposure && s.exposure[i]) ? (s.exposure[i] * 100).toFixed(0) + "%" : "—", "#fff"],
    ],
  });

  ddChart.set({
    dates: s.dates,
    series: [
      { data: s.drawdown || [], color: "#1f4e79", width: 1.8 },
    ],
    baseLine: 0,
    zeroLine: true,
    splitDate: split,
    yFmt: (v) => (v * 100).toFixed(0) + "%",
    tip: (i) => [
      ["策略回撤", isNum(s.drawdown && s.drawdown[i]) ? (s.drawdown[i] * 100).toFixed(2) + "%" : "—", "#7fb3e0"],
    ],
  });
}

function renderAll() {
  renderCards();
  renderMetricTable();
  renderHeatmap(STATE.data.monthly);
  renderFactors();
  renderHoldings();
  renderTrades();
  renderMeta();
  renderCharts();

  const m = STATE.data.meta || {};
  $("#dataset").textContent =
    `${m.universe_size || "—"} 只标的 · ${m.start || "—"} ~ ${m.end || "—"} · ` +
    `基准 ${m.benchmark || "—"} · Top${m.top_n || "—"}`;

  const note = $("#segNote");
  if (note) {
    note.textContent = `当前显示「${STATE.seg}」的指标；样本内截至 ${m.is_end || "—"}，` +
      `样本外自其后一个交易日起。`;
  }
}

/* ------------------------------ 交互 ------------------------------ */
const navChart = new SeriesChart($("#navChart"), $("#navTip"));
const ddChart = new SeriesChart($("#ddChart"), $("#ddTip"));

function setStatus(text, kind) {
  $("#status").textContent = text;
  const badge = $("#badge");
  badge.className = "pill" + (kind ? " " + kind : "");
  badge.textContent = kind === "ok" ? "已计算" : kind === "err" ? "出错" : "未运行";
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

async function runBacktest(event) {
  if (event) event.preventDefault();
  const btn = $("#run");
  btn.disabled = true;
  setStatus("计算中…", "");
  const t0 = performance.now();

  try {
    const res = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(readParams()),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);

    STATE.data = data;
    STATE.seg = "全样本";
    document.querySelectorAll("#seg button").forEach((b) =>
      b.classList.toggle("on", b.dataset.seg === STATE.seg));
    renderAll();
    setStatus(`完成 · ${((performance.now() - t0) / 1000).toFixed(1)}s`, "ok");
  } catch (err) {
    setStatus("失败", "err");
    $("#cards").innerHTML = `<div class="err">回测失败：${err.message}</div>`;
    console.error(err);
  } finally {
    btn.disabled = false;
  }
}

function bindSeg() {
  document.querySelectorAll("#seg button").forEach((btn) => {
    btn.addEventListener("click", () => {
      STATE.seg = btn.dataset.seg;
      document.querySelectorAll("#seg button").forEach((b) =>
        b.classList.toggle("on", b === btn));
      renderCards();
      renderMetricTable();
      const m = (STATE.data && STATE.data.meta) || {};
      $("#segNote").textContent = `当前显示「${STATE.seg}」的指标；样本内截至 ${m.is_end || "—"}，` +
        `样本外自其后一个交易日起。`;
    });
  });
}

async function boot() {
  bindSeg();
  $("#form").addEventListener("submit", runBacktest);

  window.addEventListener("resize", () => {
    if (STATE.data) { navChart.draw(); ddChart.draw(); }
  });

  try {
    const res = await fetch("/api/meta");
    const meta = await res.json();
    $("#dataset").textContent =
      `${meta.universe_size} 只标的 · ${meta.data_start} ~ ${meta.data_end} · 基准 ${meta.benchmark}`;
  } catch (err) {
    $("#dataset").textContent = "数据未就绪，先执行 python scripts/download_data.py";
  }

  try {
    const res = await fetch("/api/result");
    const data = await res.json();
    if (data && data.metrics && data.series) {
      STATE.data = data;
      renderAll();
      setStatus("已加载", "ok");
      return;
    }
  } catch (err) {
    /* 忽略，下面直接跑一次 */
  }

  await runBacktest();
}

boot();
