"""本地行情缓存：一个标的 = 一个 CSV + 一份 manifest。

缓存有三点好处：
1. 回测完全离线可复现（评审/换机无需重新抓）；
2. 抓取失败的标的可单独重试，不必全量重跑；
3. manifest 记录抓取时间与行数，便于检查数据完整性。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from qlab.config import RAW_DIR
from qlab.data.providers.base import normalize_frame
from qlab.logging_utils import get_logger

log = get_logger("qlab.cache")

MANIFEST_PATH = RAW_DIR / "_manifest.json"
UNIVERSE_PATH = RAW_DIR / "universe.csv"
_SAFE = re.compile(r"[^0-9A-Za-z_.]")


def _safe(symbol: str) -> str:
    return _SAFE.sub("_", symbol)


def price_path(symbol: str) -> Path:
    return RAW_DIR / f"{_safe(symbol)}.csv"


# ----------------------------------------------------------------------
# 行情
# ----------------------------------------------------------------------
def save_prices(symbol: str, df: pd.DataFrame) -> None:
    df.to_csv(price_path(symbol), encoding="utf-8", float_format="%.4f")


def load_prices(symbol: str) -> pd.DataFrame | None:
    p = price_path(symbol)
    if not p.exists():
        return None
    df = pd.read_csv(p, index_col="date", parse_dates=["date"])
    return normalize_frame(df)


def has_prices(symbol: str) -> bool:
    return price_path(symbol).exists()


# ----------------------------------------------------------------------
# manifest
# ----------------------------------------------------------------------
def read_manifest() -> dict:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {"symbols": {}, "updated_at": None}


def write_manifest(manifest: dict) -> None:
    manifest["updated_at"] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def update_manifest(symbol: str, info: dict) -> None:
    manifest = read_manifest()
    manifest.setdefault("symbols", {})[symbol] = info
    write_manifest(manifest)


# ----------------------------------------------------------------------
# 股票池
# ----------------------------------------------------------------------
def save_universe(df: pd.DataFrame) -> None:
    df.to_csv(UNIVERSE_PATH, encoding="utf-8-sig")


def load_universe() -> pd.DataFrame | None:
    if not UNIVERSE_PATH.exists():
        return None
    return pd.read_csv(UNIVERSE_PATH, encoding="utf-8-sig")
