#!/usr/bin/env python3
"""Run the monitor repeatedly on a PC or an inexpensive always-on server."""

from __future__ import annotations

import os
import time
from datetime import datetime

from nip_monitor.app import run_monitor


def run() -> None:
    interval = max(60, int(os.getenv("CHECK_INTERVAL_SECONDS", "900")))
    print(f"常驻监控已启动，每 {interval} 秒检查一次。")
    first_run = True
    while True:
        print(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] 开始检查")
        try:
            run_monitor(force_schedule=first_run)
            first_run = False
        except Exception as exc:
            print(f"运行失败：{exc}")
            print("本轮失败；不会退出，将在下个周期自动重试。")
        time.sleep(interval)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("\n监控已停止。")
