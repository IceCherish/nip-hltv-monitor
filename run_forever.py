#!/usr/bin/env python3
"""Run the monitor repeatedly on a PC or an inexpensive always-on server."""

from __future__ import annotations

import os
import time
from datetime import datetime

from nip_monitor.app import main


def run() -> None:
    interval = max(60, int(os.getenv("CHECK_INTERVAL_SECONDS", "360")))
    print(f"常驻监控已启动，每 {interval} 秒检查一次。")
    while True:
        print(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] 开始检查")
        exit_code = main([])
        if exit_code:
            print("本轮失败；不会退出，将在下个周期自动重试。")
        time.sleep(interval)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("\n监控已停止。")
