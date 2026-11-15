#!/usr/bin/env python3
"""Oracle Always Free 的轻度保活（systemd: dataagent-keepalive.service）。

Oracle 官方规则：连续 7 天内 CPU（95 分位）、网络、内存利用率**三者都**低于 20%
会被判定为闲置并可能回收实例。这个常驻进程只做三件小事：

1. 每轮做一次本机健康检查 + 一次外部 HTTPS 请求（产生真实 VNIC 网络流量）；
2. 每轮忙 90 秒做纯 CPU 计算、随后空闲 510 秒（约 15% 的采样点 CPU 高于 20%，
   足以让 95 分位不落在低负载区间），其余时间几乎不耗电；
3. 可选常驻一块内存（默认关闭）。内存要超过 12 GB 的 20% 约需 2.4 GB，
   若确实需要，在 /opt/dataagent/keepalive.env 里设 KEEPALIVE_MEM_MB=2800。

不挖矿、不对外发包、不占用带宽；这属于避免误判的轻度保活，不能替代真实负载。
最稳妥的做法仍是让 VM 跑真实服务，或升级 PAYG（Always Free 资源依旧 0 元）。
"""
from __future__ import annotations

import hashlib
import os
import time
import urllib.request

BUSY_SECONDS = float(os.getenv("KEEPALIVE_BUSY_SECONDS", "90"))
IDLE_SECONDS = float(os.getenv("KEEPALIVE_IDLE_SECONDS", "510"))
MEM_MB = int(os.getenv("KEEPALIVE_MEM_MB", "0"))
LOCAL_HEALTH = os.getenv("KEEPALIVE_HEALTH_URL", "http://127.0.0.1:8014/api/health")
EXTERNAL_URL = os.getenv("KEEPALIVE_PING_URL", "https://www.cloudflare.com/cdn-cgi/trace")


def reserve_memory(mb: int) -> bytearray | None:
    if mb <= 0:
        return None
    block = bytearray(mb * 1024 * 1024)
    for offset in range(0, len(block), 4096):
        block[offset] = 1  # 逐页写入，确保真正提交物理内存
    return block


def touch_network() -> None:
    for url in (LOCAL_HEALTH, EXTERNAL_URL):
        try:
            with urllib.request.urlopen(url, timeout=8) as response:
                response.read(256)
        except Exception:
            pass


def busy(seconds: float) -> None:
    digest = hashlib.sha256()
    payload = os.urandom(1024)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        digest.update(payload)


def main() -> None:
    hold = reserve_memory(MEM_MB)  # noqa: F841 - 必须保持引用，防止被 GC 释放
    while True:
        touch_network()
        busy(BUSY_SECONDS)
        time.sleep(IDLE_SECONDS)


if __name__ == "__main__":
    main()
