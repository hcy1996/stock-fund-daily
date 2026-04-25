from __future__ import annotations

from pathlib import Path


FETCH_STATUS_PATH = Path("fetch_status.tsv")


def load_fetch_status(raw_dir: Path) -> dict[tuple[str, int], bool]:
    path = raw_dir / FETCH_STATUS_PATH
    if not path.exists():
        return {}

    status_map: dict[tuple[str, int], bool] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        source, window_days_raw, status = parts
        try:
            window_days = int(window_days_raw)
        except ValueError:
            continue
        status_map[(source, window_days)] = status == "success"
    return status_map


def fetch_succeeded(status_map: dict[tuple[str, int], bool], source: str, window_days: int) -> bool:
    if not status_map:
        return True
    return status_map.get((source, window_days), False)
