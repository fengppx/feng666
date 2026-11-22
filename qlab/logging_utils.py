"""统一日志：同时输出到控制台与 output/logs/qlab.log。"""

from __future__ import annotations

import logging
import sys

from qlab.config import LOG_DIR

_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_configured = False


def get_logger(name: str = "qlab", level: int = logging.INFO) -> logging.Logger:
    global _configured
    logger = logging.getLogger(name)
    if not _configured:
        root = logging.getLogger("qlab")
        root.setLevel(level)
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(logging.Formatter(_FORMAT, datefmt="%H:%M:%S"))
        root.addHandler(sh)
        fh = logging.FileHandler(LOG_DIR / "qlab.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(fh)
        _configured = True
    return logger
