from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import subprocess
import time
from zoneinfo import ZoneInfo

from app.config import AppConfig, PROJECT_ROOT


def _next_run_datetime(config: AppConfig) -> datetime:
    zone = ZoneInfo(config.schedule.timezone)
    now = datetime.now(zone)
    target = now.replace(hour=config.schedule.hour, minute=config.schedule.minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def run_forever(config: AppConfig) -> None:
    run_script = PROJECT_ROOT / "scripts" / "run_daily.sh"
    while True:
        target = _next_run_datetime(config)
        sleep_seconds = max(1, int((target - datetime.now(ZoneInfo(config.schedule.timezone))).total_seconds()))
        print(f"Next run at {target.isoformat()}")
        time.sleep(sleep_seconds)
        subprocess.run(["/bin/bash", str(run_script)], cwd=PROJECT_ROOT, check=False)
