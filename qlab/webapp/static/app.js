/* QLab 看板前端：原生 JS + Canvas，无任何外部依赖 */

const $ = (s) => document.querySelector(s);
const fmtPct = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v))
  ? "—" : (v * 100).toFixed(d) + "%";
const fmtNum = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v))
  ? "—" : Number(v).toFixed(d);

/* ---------------- Canvas 折线图 ---------------- */
function drawLineChart(canvas, series, opts = {}) {
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth || 900;
  const cssH = canvas.height || 340;
  canvas.width = cssW * dpr;
  canvas.height = cssH * dpr;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);

  const pad = { l: 56, r: 16, t: 14, b: 30 };
  const W = cssW - pad.l - pad.r, H = cssH - pad.t - pad.b;
  const all = series.flatMap((s) => s.data);
  let lo = Math.min(...all), hi = Math.max(...all);
  const padY = (hi - lo) * 0.08 || 0.05;
  lo -= padY; hi += padY;

  const n = series[0].data.length;
  const X = (i) => pad.l + (n <= 1 ? 0 : (i / (n - 1)) * W);
  const Y = (v) => pad.t + H - ((v - lo) / (hi - lo)) * H;

  // 网格 + y 轴
  ctx.font = "11px system-ui, sans-serif";
  ctx.strokeStyle = "#eef1f5";
  ctx.fillStyle = "#8a94a6";
  const ticks = 5;
  for (let i = 0; i <= ticks; i++) {
    const v = lo + ((hi - lo) * i) / ticks;
    const y = Y(v);
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(pad.l + W, y); ctx.stroke();
    ctx.textAlign = "right"; ctx.textBaseline = "middle";
    ctx.fillText(v.toFixed(2), pad.l - 8, y);
  }
  // 1.0 基准线
  if (lo < 1 && hi > 1) {
    ctx.strokeStyle = "#cfd6e0"; ctx.setLineDash([5, 4]);
    ctx.beginPath(); ctx.moveTo(pad.l, Y(1)); ctx.lineTo(pad.l + W, Y(1)); ctx.stroke();
    ctx.setLineDash([]);
  }
  // x 轴标签（按年）
  const dates = opts.dates || [];
  if (dates.length === n) {
    let lastYear = null;
    ctx.textAlign = "center"; ctx.textBaseline = "top"; ctx.fillStyle = "#8a94a6";
    dates.forEach((d, i) => {
      const y = d.slice(0, 4);
      if (y !== lastYear) {
        if (i === 0 || i > n * 0.02) {
          ctx.fillText(y, X(i), pad.t + H + 8);
        }
        lastYear = y;
      }
    });
  }
  // 曲线
  series.forEach((s) => {
    ctx.beginPath();
    ctx.strokeStyle = s.color;
    ctx.lineWidth = s.width || 2;
    s.data.forEach((v, i) => (i ? ctx.lineTo(X(i), Y(v)) : ctx.moveTo(X(i), Y(v))));
    ctx.stroke();
  });
}

/* ---------------- 渲染 ---------------- */
function renderCards(metrics) {
  const m = metrics["全样本"] || {};
  const cards = [
    ["累计收益率", fmtPct(m["累计收益率"]), "策略"],
    ["年化收益率", fmtPct(m["年化收益率"]), "年化"],
    ["最大回撤", fmtPct(m["最大回撤"]), "峰谷最大跌幅"],
    ["夏普比率", fmtNum(m["夏普比率"]), "rf = 1.5%"],
    ["超额收益(年化)", fmtPct(m["超额收益(年化)"]), "相对沪深300"],
    ["信息比率", fmtNum(m["信息比率"]), "主动收益/跟踪误差"],
    ["月胜率", fmtPct(m["月胜率"], 0), "月度正收益占比"],
    ["年化换手", fmtNum(m["年化换手率"], 1) + "x", "双边成交额/平均市值"],
  ];
  $("#cards").innerHTML = cards.map(([k, v, x]) => {
    const cls = (v.startsWith("-") && k.includes("收益")) ? "neg" : "";
    return `<div class="card"><div class="k">${k}</div>
            <div class="v ${cls}">${v}</div><div class="x">${x}</div></div>`;
  }).join("");
}

function renderHeatmap(monthly) {
  const { years, matrix } = monthly;
  const months = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"];
  let max = 0;
  matrix.forEach((row) => row.forEach((v) => { if (v !== null) max = Math.max(max, Math.abs(v)); }));
  max = max || 1;
  let html = `<div class="hm-cell hm-head"></div>` +
    months.map((m) => `<div class="hm-cell hm-head">${m}月</div>`).join("");
  years.forEach((y, i) => {
    html += `<div class="hm-cell hm-head">${y}</div>`;
    months.forEach((_, j) => {
      const v = matrix[i][j];
      if (v === null || v === undefined) {
        html += `<div class="hm-cell" style="background:#f6f8fa;color:#b9c1cc">-</div>`;
      } else {
        const t = Math.min(1, Math.abs(v) / max);
        const bg = v >= 0 ? `rgba(192,80,77,${0.10 + 0.72 * t})` : `rgba(18,128,92,${0.10 + 0.72 * t})`;
        const color = t > 0.55 ? "#fff" : "#2b3445";
        html += `<div class="hm-cell" style="background:${bg};color:${color}">${v.toFixed(1)}</div>`;
      }
    });
  });
  $("#heatmap").innerHTML = html;
}

function renderFactors(data) {
  const selected = new Set((data.selected || []).map((s) => s.factor));
  const weights = Object.fromEntries((data.selected || []).map((s) => [s.factor, s.weight]));
  const rows = (data.factors || [])
    .slice()
    .sort((a, b) => Math.abs(b.icir) - Math.abs(a.icir))
    .map((f) => {
      const on = selected.has(f.factor);
      return `<tr style="opacity:${on ? 1 : 0.55}">
        <td>${f.factor}${on ? " ●" : ""}</td>
        <td>${f.mean_ic >= 0 ? "+" : ""}${f.mean_ic.toFixed(4)}</td>
        <td>${f.icir >= 0 ? "+" : ""}${f.icir.toFixed(3)}</td>
        <td>${f.t_stat.toFixed(2)}</td>
        <td>${on ? weights[f.factor].toFixed(2) : "—"}</td>
      </tr>`;
    });
  $("#factorTable tbody").innerHTML = rows.join("");
}

function renderHoldings(list) {
  $("#holdTable tbody").innerHTML = (list || []).map((h) =>
    `<tr><td>${h.symbol}</td><td>${h.name}</td><td>${fmtPct(h.weight, 2)}</td></tr>`).join("");
}

function renderTrades(list) {
  $("#tradeTable tbody").innerHTML = (list || []).slice().reverse().map((t) =>
    `<tr><td>${String(t.date).slice(0, 10)}</td><td>${t.symbol}</td><td>${t.name}</td>
     <td style="color:${t.side === "BUY" ? "#c0392b" : "#12805c"}">
       ${t.side === "BUY" ? "买入" : "卖出"}</td>
     <td>${t.shares}</td><td>${Number(t.price).toFixed(2)}</td>
     <td>${Number(t.notional).toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
     <td>${Number(t.total_fee).toFixed(1)}</td></tr>`).join("");
}

function renderAll(data) {
  window.__lastData = data;
  renderCards(data.metrics);
  renderFactors(data);
  renderHeatmap(data.monthly);
  renderHoldings(data.holdings);
  renderTrades(data.trades);
  drawLineChart($("#navChart"), [
    { data: data.series.nav, color: "#1f4e79", width: 2.2 },
    { data: data.series.benchmark, color: "#c0504d", width: 1.6 },
  ], { dates: data.series.dates });
  const m = data.meta;
  $("#dataset").textContent =
    `${m.universe_size} 只标的 · ${m.start} ~ ${m.end} · 基准 ${m.benchmark} · ` +
    `Top${m.top_n} · ${m.timing ? "含择时" : "不含择时"}`;
}

/* ---------------- 交互 ---------------- */
async function runBacktest(e) {
  if (e) e.preventDefault();
  const btn = $("#run");
  btn.disabled = true;
  $("#status").textContent = "回测运行中…";
  const form = new FormData($("#form"));
  const params = {
    start: form.get("start"),
    is_end: form.get("is_end"),
    top_n: Number(form.get("top_n")),
    factor_k: Number(form.get("factor_k")),
    weighting: form.get("weighting"),
    timing: form.get("timing") === "on",
    capital: Number(form.get("capital")),
  };
  try {
    const t0 = performance.now();
    const res = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    renderAll(data);
    const secs = ((performance.now() - t0) / 1000).toFixed(1);
    $("#status").textContent = `完成 · ${secs}s`;
  } catch (err) {
    $("#status").textContent = "出错";
    $("#cards").innerHTML = `<div class="err">回测失败：${err.message}</div>`;
  } finally {
    btn.disabled = false;
  }
}

async function boot() {
  try {
    const meta = await (await fetch("/api/meta")).json();
    $("#dataset").textContent =
      `${meta.universe_size} 只标的 · ${meta.data_start} ~ ${meta.data_end} · 基准 ${meta.benchmark}`;
  } catch (err) {
    $("#dataset").textContent = "数据未就绪，请先运行 scripts/download_data.py";
  }
  $("#form").addEventListener("submit", runBacktest);
  // 自动跑一次默认配置，页面打开即有结果
  runBacktest();
  window.addEventListener("resize", () => {
    if (window.__lastData) drawLineChart($("#navChart"), [
      { data: window.__lastData.series.nav, color: "#1f4e79", width: 2.2 },
      { data: window.__lastData.series.benchmark, color: "#c0504d", width: 1.6 },
    ], { dates: window.__lastData.series.dates });
  });
}

boot();
