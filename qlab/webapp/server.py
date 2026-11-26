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
        """按前端参数跑一次回测。"""
        from qlab.analytics.metrics import DEFAULT_SEGMENTS, perf_metrics
        from qlab.backtest import PortfolioBacktester
        from qlab.strategy import MultiFactorStrategy

        with self.lock:
            panel = self.ensure_panel()
            bt_cfg = BacktestConfig(
                start=str(params.get("start") or "2019-01-01"),
                end=params.get("end"),
                is_end=str(params.get("is_end") or "2022-12-31"),
                top_n=int(params.get("top_n") or 30),
                initial_cash=float(params.get("capital") or 1_000_000),
            )
            strat_cfg = StrategyConfig(
                factor_select_k=int(params.get("factor_k") or 5),
                weighting=str(params.get("weighting") or "icir"),
                timing_enabled=bool(params.get("timing", True)),
            )
            bt_panel = panel.slice(bt_cfg.start, bt_cfg.end)
            panel_is = panel.slice(None, bt_cfg.is_end)

            strategy = MultiFactorStrategy(strat_cfg, bt_cfg).fit(panel_is)
            targets = strategy.generate_targets(panel, start=bt_cfg.start)
            targets = targets[targets.index >= bt_panel.dates[0]]
            result = PortfolioBacktester(bt_cfg).run(bt_panel, targets)

            metrics = {}
            for seg in DEFAULT_SEGMENTS:
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
            step = max(1, len(nav) // 900)
            idx = nav.index[::step]
            monthly = result.monthly_returns()
            dd = nav / nav.cummax() - 1.0

            payload = {
                "meta": {
                    "universe_size": len(panel.symbols),
                    "start": str(bt_panel.dates[0].date()),
                    "end": str(bt_panel.dates[-1].date()),
                    "is_end": str(bt_cfg.is_end),
                    "benchmark": panel.benchmark_name,
                    "top_n": bt_cfg.top_n,
                    "weighting": strat_cfg.weighting,
                    "timing": strat_cfg.timing_enabled,
                    "provider": self.data_cfg.provider,
                    "adjust": self.data_cfg.adjust,
                },
                "metrics": {k: _clean(v) for k, v in metrics.items()},
                "series": {
                    "dates": [d.strftime("%Y-%m-%d") for d in idx],
                    "nav": [float(v) for v in nav.loc[idx]],
                    "benchmark": [float(v) for v in bench.loc[idx]],
                    "drawdown": [float(v) for v in dd.loc[idx]],
                    "exposure": [float(v) for v in result.exposure.loc[idx]],
                },
                "monthly": {
                    "years": [int(y) for y in monthly.index],
                    "matrix": [[None if v != v else float(v) for v in row]
                               for row in monthly.to_numpy()],
                },
                "factors": _clean(strategy.ic_table.to_dict(orient="records")),
                "selected": [s.as_dict() for s in strategy.specs],
                "trades": _clean(result.trades.tail(60).to_dict(orient="records")),
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
                    "total": float(result.total_cost),
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
