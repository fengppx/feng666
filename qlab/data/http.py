"""极简 HTTP 工具：重试、超时、gzip、限速。

只依赖标准库 urllib，避免为了抓数据引入额外依赖。
"""

from __future__ import annotations

import gzip
import json
import random
import time
import urllib.error
import urllib.request
from typing import Any

from qlab.logging_utils import get_logger

log = get_logger("qlab.http")

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "close",
}


def fetch_bytes(
    url: str,
    *,
    timeout: float = 20.0,
    retry: int = 3,
    headers: dict[str, str] | None = None,
) -> bytes:
    """带指数退避重试的 GET。失败抛出最后一次异常。

    数据站被高频访问时常见 501 / 连接重置，这里统一按"可重试"处理，
    退避序列 1.2s → 2.4s → 4.8s（上限 8s），避免把限流打成硬封禁。
    """
    hdrs = dict(DEFAULT_HEADERS)
    if headers:
        hdrs.update(headers)
    last_exc: Exception | None = None
    for attempt in range(max(1, retry)):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            return raw
        except (urllib.error.URLError, OSError, ValueError) as exc:  # 网络/协议错误
            last_exc = exc
            sleep = min(8.0, 1.2 * (2**attempt)) + random.random() * 0.6
            log.debug("请求失败(%s/%s) %s: %s", attempt + 1, retry, url[:90], exc)
            time.sleep(sleep)
    assert last_exc is not None
    raise last_exc


def fetch_text(url: str, **kwargs: Any) -> str:
    raw = fetch_bytes(url, **kwargs)
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def fetch_json(url: str, **kwargs: Any) -> Any:
    text = fetch_text(url, **kwargs)
    return json.loads(text)
