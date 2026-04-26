from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.config import PROJECT_ROOT


RAW_ARCHIVE_ROOT = PROJECT_ROOT / "data" / "archive" / "raw"
REPORT_HISTORY_ROOT = PROJECT_ROOT / "reports" / "history"


def archive_raw_snapshot(raw_dir: Path, trade_date: str) -> Path:
    archive_dir = RAW_ARCHIVE_ROOT / trade_date
    archive_dir.mkdir(parents=True, exist_ok=True)

    if not raw_dir.exists():
        return archive_dir

    for source_path in raw_dir.rglob("*"):
        if not source_path.is_file():
            continue
        target_path = archive_dir / source_path.relative_to(raw_dir)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)
    return archive_dir


def build_analysis_snapshot(
    trade_date: str,
    daily_ai_input: dict,
    daily_ai_summary: str | None,
    daily_ai_warning: str | None,
    weekly_ai_summary: str | None,
    weekly_ai_warning: str | None,
    bridge_ai_prompt: str | None = None,
    bridge_ai_summary: str | None = None,
    bridge_ai_warning: str | None = None,
) -> dict:
    return {
        "trade_date": trade_date,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "daily_ai_input": daily_ai_input,
        "daily_ai_summary": daily_ai_summary,
        "daily_ai_warning": daily_ai_warning,
        "weekly_ai_summary": weekly_ai_summary,
        "weekly_ai_warning": weekly_ai_warning,
        "bridge_ai_prompt": bridge_ai_prompt,
        "bridge_ai_summary": bridge_ai_summary,
        "bridge_ai_warning": bridge_ai_warning,
    }


def load_recent_analysis_snapshots(
    limit_days: int,
    exclude_trade_dates: set[str] | None = None,
) -> list[dict]:
    if not REPORT_HISTORY_ROOT.exists():
        return []

    excluded = exclude_trade_dates or set()
    snapshots: list[dict] = []
    for day_dir in sorted(REPORT_HISTORY_ROOT.iterdir(), reverse=True):
        if not day_dir.is_dir() or day_dir.name in excluded:
            continue
        snapshot_path = day_dir / "snapshot.json"
        if not snapshot_path.exists():
            continue
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        trade_date = str(snapshot.get("trade_date", "")).strip()
        if not trade_date or trade_date in excluded:
            continue
        snapshots.append(snapshot)
        if len(snapshots) >= limit_days:
            break
    snapshots.reverse()
    return snapshots


def _write_text(path: Path, content: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text((content or "").strip(), encoding="utf-8")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def save_analysis_artifacts(
    output_dir: Path,
    trade_date: str,
    snapshot: dict,
    daily_ai_prompt: str,
    daily_ai_summary: str | None,
    daily_ai_warning: str | None,
    weekly_ai_prompt: str,
    weekly_ai_summary: str | None,
    weekly_ai_warning: str | None,
    bridge_ai_prompt: str | None = None,
    bridge_ai_summary: str | None = None,
    bridge_ai_warning: str | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    history_dir = REPORT_HISTORY_ROOT / trade_date
    history_dir.mkdir(parents=True, exist_ok=True)

    _write_text(output_dir / f"{trade_date}-ai-prompt.txt", daily_ai_prompt)
    _write_text(output_dir / f"{trade_date}-ai-summary.txt", daily_ai_summary)
    _write_text(output_dir / f"{trade_date}-ai-warning.txt", daily_ai_warning)
    _write_text(output_dir / f"{trade_date}-weekly-ai-prompt.txt", weekly_ai_prompt)
    _write_text(output_dir / f"{trade_date}-weekly-ai-summary.txt", weekly_ai_summary)
    _write_text(output_dir / f"{trade_date}-weekly-ai-warning.txt", weekly_ai_warning)
    _write_text(output_dir / f"{trade_date}-sector-bridge-ai-prompt.txt", bridge_ai_prompt)
    _write_text(output_dir / f"{trade_date}-sector-bridge-ai-summary.txt", bridge_ai_summary)
    _write_text(output_dir / f"{trade_date}-sector-bridge-ai-warning.txt", bridge_ai_warning)
    _write_json(output_dir / f"{trade_date}-analysis-snapshot.json", snapshot)

    _write_text(history_dir / "daily-ai-prompt.txt", daily_ai_prompt)
    _write_text(history_dir / "daily-ai-summary.txt", daily_ai_summary)
    _write_text(history_dir / "daily-ai-warning.txt", daily_ai_warning)
    _write_text(history_dir / "weekly-ai-prompt.txt", weekly_ai_prompt)
    _write_text(history_dir / "weekly-ai-summary.txt", weekly_ai_summary)
    _write_text(history_dir / "weekly-ai-warning.txt", weekly_ai_warning)
    _write_text(history_dir / "sector-bridge-ai-prompt.txt", bridge_ai_prompt)
    _write_text(history_dir / "sector-bridge-ai-summary.txt", bridge_ai_summary)
    _write_text(history_dir / "sector-bridge-ai-warning.txt", bridge_ai_warning)
    _write_json(history_dir / "snapshot.json", snapshot)
