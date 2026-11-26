"""展示层其三：带后端的 Web 看板。

只用标准库 ``http.server`` 实现，不引入 Flask/FastAPI，保证 `pip install -r
requirements.txt` 之后一条命令就能起服务：

    python -m qlab web            # 默认 http://127.0.0.1:8000

后端提供三个接口：
    GET  /api/meta     数据集信息（股票池规模、区间、基准）
    POST /api/run      用给定参数**真跑一遍回测**，返回指标与曲线
    GET  /api/result   最近一次回测的结果

前端是纯原生 JS + Canvas 绘制的单页看板，不依赖任何 CDN。
"""

from __future__ import annotations

import json
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from qlab.config import BacktestConfig, DataConfig, StrategyConfig
from qlab.logging_utils import get_logger

log = get_logger("qlab.web")

STATIC_DIR = Path(__file__).resolve().parent / "static"


class BacktestService:
    """把「配置 → 面板 → 策略 → 回测 → 摘要」封成一个可复用的服务对象。"""

    def __init__(self, data_cfg: DataConfig | None = None):
        self.data_cfg = data_cfg or DataConfig()
        self.panel = None
        self.ic_table = None
        self.last_result: dict[str, Any] | None = None
        self.lock = threading.Lock()

    # ------------------------------------------------------------------
    def ensure_panel(self):
        if self.panel is None:
            from qlab.data import DataPortal

            portal = DataPortal(self.data_cfg)
            portal.ensure_universe()
            self.panel = portal.load(require_start="2017-09-01")
            log.info("面板就绪：%d 交易日 × %d 标的",
                     len(self.panel.dates), len(self.panel.symbols))
        return self.panel

    def meta(self) -> dict:
        panel = self.ensure_panel()
        return {
            "universe_size": len(panel.symbols),
            "data_start": str(panel.dates[0].date()),
            "data_end": str(panel.dates[-1].date()),
            "benchmark": panel.benchmark_name,
            "default_start": "2019-01-01",
            "default_is_end": "2022-12-31",
        }

    # ------------------------------------------------------------------
    def run(self, params: dict) -> dict:
        """按前端参数跑一次回测。

        参数全部来自看板表单，这里做一次服务端校验：前端校验可以被绕过，
        而回测区间、样本内边界、Top-N 等一旦非法，后面的取数/切片会抛出不
        易理解的 IndexError，所以先给出可读的错误信息。
        """
        import pandas as pd

        from qlab.analytics.metrics import SegmentSpec, perf_metrics
        from qlab.backtest import PortfolioBacktester
        from qlab.strategy import MultiFactorStrategy

        with self.lock:
            panel = self.ensure_panel()

            start = str(params.get("start") or "2019-01-01")
            is_end = str(params.get("is_end") or "2022-12-31")
            try:
                start_ts = pd.Timestamp(start)
                is_end_ts = pd.Timestamp(is_end)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"日期格式应为 YYYY-MM-DD（{exc}）") from exc
            if is_end_ts <= start_ts:
                raise ValueError("样本内结束日必须晚于回测起始日")
            if start_ts > panel.dates[-1]:
                raise ValueError(
                    f"回测起始日 {start} 晚于数据末端 {panel.dates[-1].date()}，请先更新行情"
                )
            if start_ts < panel.dates[0]:
                start = str(panel.dates[0].date())
                start_ts = panel.dates[0]
            if is_end_ts > panel.dates[-1]:
                is_end = str(panel.dates[-1].date())
                is_end_ts = panel.dates[-1]

            top_n = int(params.get("top_n") or 30)
            factor_k = int(params.get("factor_k") or 5)
            capital = float(params.get("capital") or 1_000_000)
            weighting = str(params.get("weighting") or "icir")
            timing = params.get("timing", True)
            if isinstance(timing, str):
                timing = timing.strip().lower() not in ("false", "0", "off", "")
            if not 5 <= top_n <= 100:
                raise ValueError("持仓只数应在 5~100 之间")
            if not 2 <= factor_k <= 12:
                raise ValueError("因子个数应在 2~12 之间")
            if capital < 100_000:
                raise ValueError("初始资金不应低于 10 万元")
            if weighting not in ("icir", "equal"):
                raise ValueError("因子加权方式只能是 icir 或 equal")

            bt_cfg = BacktestConfig(
                start=start, end=None, is_end=is_end,
                top_n=top_n, initial_cash=capital,
            )
            strat_cfg = StrategyConfig(
                factor_select_k=factor_k,
                weighting=weighting,
                timing_enabled=bool(timing),
            )
            bt_panel = panel.slice(bt_cfg.start, bt_cfg.end)
            panel_is = panel.slice(None, bt_cfg.is_end)
            if len(bt_panel.dates) < 60:
                raise ValueError("回测区间不足 60 个交易日，请调整起始日")
            if len(panel_is.dates) < 300:
                raise ValueError("样本内交易日不足 300 天，无法估计因子，请把样本内结束日往后调")

            strategy = MultiFactorStrategy(strat_cfg, bt_cfg).fit(panel_is)
            targets = strategy.generate_targets(panel, start=bt_cfg.start)
            targets = targets[targets.index >= bt_panel.dates[0]]
            result = PortfolioBacktester(bt_cfg).run(bt_panel, targets)

            # 分段边界跟随用户输入，而不是固定 2019/2022
            segments = [
                SegmentSpec("全样本", None, None),
                SegmentSpec("样本内", start, is_end),
                SegmentSpec(
                    "样本外",
                    (is_end_ts + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                    None,
                ),
            ]
            metrics = {}
            for seg in segments:
                try:
                    sub = result.segment(seg.start, seg.end)
                except ValueError:
                    continue
                metrics[seg.label] = perf_metrics(
                    sub.nav, sub.benchmark_nav, rf=bt_cfg.risk_free_rate,
                    trades=sub.trades, avg_equity=float(sub.equity.mean()),
                )

            nav = result.nav
            bench = result.benchmark_nav.reindex(nav.index).ffill()
            bench = bench / bench.dropna().iloc[0]
            step = max(1, len(nav) // 1000)
            positions = list(range(0, len(nav), step))
            if positions[-1] != len(nav) - 1:
                positions.append(len(nav) - 1)  # 保证末端点不被抽样丢掉
            idx = nav.index[positions]
            dd = nav / nav.cummax() - 1.0
            bench_dd = bench / bench.cummax() - 1.0

            # 净值采样后的序列索引与原始交易日不同，标记点要在采样后的坐标上
            pos_of = {d: i for i, d in enumerate(idx)}

            def marker_index(day) -> int | None:
                if day in pos_of:
                    return pos_of[day]
                later = idx[idx >= day]
                return pos_of[later[0]] if len(later) else None

            markers = []
            if not result.targets.empty:
                gross = result.targets.sum(axis=1)
                prev = None
                for day, value in gross.items():
                    if prev is None:
                        label = "建仓"
                    elif value - prev > 1e-6:
                        label = "加仓"
                    else:
                        prev = value
                        continue
                    i = marker_index(day)
                    if i is not None:
                        markers.append({
                            "i": i, "date": day.strftime("%Y-%m-%d"),
                            "label": label, "kind": "up",
                        })
                    prev = value
                # 降仓单独扫描，避免和“加仓/建仓”混在同一条件里
                prev = None
                for day, value in gross.items():
                    if prev is not None and prev - value > 1e-6:
                        i = marker_index(day)
                        if i is not None:
                            markers.append({
                                "i": i, "date": day.strftime("%Y-%m-%d"),
                                "label": "降仓", "kind": "down",
                            })
                    prev = value
                markers.sort(key=lambda m: (m["i"], m["kind"] != "up"))

            monthly = result.monthly_returns()
            monthly_bench = _monthly_matrix(bench)
            annual = _annual_returns(nav, bench)

            years = len(nav) / bt_cfg.trading_days
            avg_equity = float(result.equity.mean())
            total_cost = float(result.total_cost)
            cost_annual = total_cost / avg_equity / years if avg_equity > 0 and years > 0 else 0.0

            payload = {
                "meta": {
                    "universe_size": len(panel.symbols),
                    "universe_requested": int(self.data_cfg.universe_size),
                    "data_start": str(panel.dates[0].date()),
                    "data_end": str(panel.dates[-1].date()),
                    "start": str(bt_panel.dates[0].date()),
                    "end": str(bt_panel.dates[-1].date()),
                    "is_end": str(bt_cfg.is_end),
                    "benchmark": panel.benchmark_name,
                    "top_n": bt_cfg.top_n,
                    "factor_k": strat_cfg.factor_select_k,
                    "weighting": strat_cfg.weighting,
                    "timing": strat_cfg.timing_enabled,
                    "timing_ma": strat_cfg.timing_ma_window,
                    "timing_low": strat_cfg.timing_low_exposure,
                    "max_weight": strat_cfg.max_weight,
                    "provider": self.data_cfg.provider,
                    "adjust": self.data_cfg.adjust,
                    "current_exposure": float(result.exposure.iloc[-1]),
                },
                "metrics": {k: _clean(v) for k, v in metrics.items()},
                "series": {
                    "dates": [d.strftime("%Y-%m-%d") for d in idx],
                    # 用 _clean 把 NaN 转成 null：Python 的 json 默认允许 NaN，
                    # 但浏览器的 JSON.parse 不接受，会导致整个看板加载失败。
                    "nav": _clean([float(v) for v in nav.loc[idx]]),
                    "benchmark": _clean([float(v) for v in bench.loc[idx]]),
                    "drawdown": _clean([float(v) for v in dd.loc[idx]]),
                    "benchmark_drawdown": _clean([float(v) for v in bench_dd.loc[idx]]),
                    "exposure": _clean([float(v) for v in result.exposure.loc[idx]]),
                    "n_holdings": [int(v) for v in result.n_holdings.loc[idx]],
                },
                "annual": annual,
                "monthly": {
                    "years": [int(y) for y in monthly.index],
                    "matrix": [[None if v != v else float(v) for v in row]
                               for row in monthly.to_numpy()],
                },
                "monthly_benchmark": {
                    "years": [int(y) for y in monthly_bench.index],
                    "matrix": [[None if v != v else float(v) for v in row]
                               for row in monthly_bench.to_numpy()],
                },
                "markers": markers,
                "factors": _clean(strategy.ic_table.to_dict(orient="records")),
                "selected": [s.as_dict() for s in strategy.specs],
                "trades": _clean(result.trades.tail(80).to_dict(orient="records")),
                "holdings": [
                    {"symbol": s, "name": panel.names.get(s, s), "weight": float(w)}
                    for s, w in result.targets.iloc[-1].sort_values(ascending=False).items()
                    if w > 0
                ] if not result.targets.empty else [],
                "rebalances": len(result.rebalance_dates),
                "cost": {
                    "commission": float(result.trades["commission"].sum()) if not result.trades.empty else 0.0,
                    "stamp_duty": float(result.trades["stamp_duty"].sum()) if not result.trades.empty else 0.0,
                    "slippage": float(result.trades["slippage_cost"].sum()) if not result.trades.empty else 0.0,
                    "total": total_cost,
                    "annual": cost_annual,
                    "trades": int(len(result.trades)),
                },
                "rebalance_dates": [d.strftime("%Y-%m-%d") for d in result.rebalance_dates],
            }
            self.last_result = payload
            log.info("Web 端回测完成：Top%d / %s / 择时=%s → 累计收益 %.1f%%",
                     bt_cfg.top_n, strat_cfg.weighting, strat_cfg.timing_enabled,
                     100 * (nav.iloc[-1] - 1))
            return payload


# ----------------------------------------------------------------------
def _monthly_matrix(series):
    """把日频净值序列转成「年 × 月」收益矩阵（%），首月以上市首日为基期。"""
    import pandas as pd

    series = series.dropna()
    if len(series) < 2:
        return pd.DataFrame()
    me = series.resample("ME").last()
    first = pd.Series([series.iloc[0]], index=[series.index[0] - pd.offsets.MonthEnd(0)])
    rets = pd.concat([first, me]).pct_change(fill_method=None).dropna()
    out = pd.DataFrame({"year": rets.index.year, "month": rets.index.month,
                        "ret": rets.to_numpy()})
    table = out.pivot_table(index="year", columns="month", values="ret", aggfunc="last")
    return (table * 100.0).round(2)


def _annual_returns(nav, bench) -> dict:
    """逐年收益（%）：以每年最后一个交易日对上一年末（首年对回测首日）计算。"""
    def yearly(series):
        series = series.dropna()
        if len(series) < 2:
            return []
        last = series.groupby(series.index.year).last()
        prev = float(series.iloc[0])
        values = []
        for value in last.to_numpy():
            values.append((float(value) / prev - 1.0) * 100.0 if prev > 0 else None)
            prev = float(value)
        return values

    years = [int(y) for y in nav.dropna().groupby(nav.dropna().index.year).last().index]
    return {"years": years, "strategy": yearly(nav), "benchmark": yearly(bench)}


def _clean(obj):
    """把 NaN / numpy / pandas 类型转成 JSON 可序列化对象。"""
    import numpy as np
    import pandas as pd

    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, pd.Timestamp):
        return obj.strftime("%Y-%m-%d")
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        return None if obj != obj else float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


class Handler(BaseHTTPRequestHandler):
    service: BacktestService = None  # type: ignore[assignment]
    server_version = "QLab/1.0"

    # ---------------- 工具 ----------------
    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _file(self, path: Path):
        # 只允许读取 static 目录内的文件，避免 /static/../ 目录穿越
        root = STATIC_DIR.resolve()
        try:
            target = path.resolve()
        except OSError:
            target = path
        if target != root and root not in target.parents:
            self._json({"error": "not found"}, 404)
            return
        if not target.exists():
            self._json({"error": "not found"}, 404)
            return
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".png": "image/png",
        }.get(target.suffix, "text/plain; charset=utf-8")
        self._send(200, target.read_bytes(), ctype)

    def log_message(self, fmt, *args):  # 降低访问日志噪音
        log.debug("web %s - %s", self.address_string(), fmt % args)

    # ---------------- 路由 ----------------
    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path in ("/", "/index.html"):
                self._file(STATIC_DIR / "index.html")
            elif path.startswith("/static/"):
                self._file(STATIC_DIR / path[len("/static/"):])
            elif path == "/api/meta":
                self._json(self.service.meta())
            elif path == "/api/result":
                self._json(self.service.last_result or {"error": "尚未运行回测"}, 200)
            elif path == "/api/health":
                self._json({"ok": True})
            else:
                self._json({"error": "not found"}, 404)
        except Exception as exc:  # pragma: no cover
            log.error("GET %s 失败：%s", path, exc)
            self._json({"error": str(exc)}, 500)

    def do_POST(self):
        path = self.path.split("?")[0]
        if path != "/api/run":
            self._json({"error": "not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            params = json.loads(raw.decode("utf-8") or "{}")
        except Exception as exc:
            self._json({"error": f"参数解析失败: {exc}"}, 400)
            return
        try:
            payload = self.service.run(params)
            self._json(payload)
        except Exception as exc:  # pragma: no cover
            log.error("回测失败：%s\n%s", exc, traceback.format_exc())
            self._json({"error": str(exc)}, 500)


def serve(host: str = "127.0.0.1", port: int = 8000, data_cfg: DataConfig | None = None):
    """启动 Web 服务（阻塞）。"""
    service = BacktestService(data_cfg)
    Handler.service = service

    # 启动时预热面板（数据没下载会立刻报错，便于尽早暴露问题）
    meta = service.meta()
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"QLab 看板已启动： http://{host}:{port}")
    print(f"  数据：{meta['universe_size']} 只标的，{meta['data_start']} ~ {meta['data_end']}，"
          f"基准 {meta['benchmark']}")
    print("  Ctrl+C 退出")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()
