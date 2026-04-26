from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time
from urllib import error
from zoneinfo import ZoneInfo
from datetime import datetime

from app.fund_rank_report.analyzer import PERIOD_LABELS, PERIOD_ORDER
from app.models import FundRankRecord
from app.sources.fund_rank import (
    EASTMONEY_FUND_RANK_PAGE_SIZE,
    expected_rank_files,
    fetch_rank_payload,
    parse_rank_file,
    parse_rank_payload,
)


RANK_REMOTE_REQUEST_INTERVAL_SECONDS = 0.25
RANK_REMOTE_MAX_RETRIES = 3
_LAST_RANK_REMOTE_REQUEST_AT = 0.0


def _current_snapshot_date() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")


def _throttle_rank_remote_requests() -> None:
    global _LAST_RANK_REMOTE_REQUEST_AT
    now = time.monotonic()
    wait_seconds = (_LAST_RANK_REMOTE_REQUEST_AT + RANK_REMOTE_REQUEST_INTERVAL_SECONDS) - now
    if wait_seconds > 0:
        time.sleep(wait_seconds)
    _LAST_RANK_REMOTE_REQUEST_AT = time.monotonic()


def _fetch_rank_page_with_retry(
    period: str,
    user_agent: str,
    *,
    page_size: int,
    page_index: int,
) -> str:
    last_error: Exception | None = None
    for attempt in range(1, RANK_REMOTE_MAX_RETRIES + 1):
        try:
            _throttle_rank_remote_requests()
            return fetch_rank_payload(
                period,
                user_agent,
                page_size=page_size,
                page_index=page_index,
            )
        except (OSError, error.URLError, TimeoutError, subprocess.CalledProcessError) as exc:
            last_error = exc
            if attempt < RANK_REMOTE_MAX_RETRIES:
                time.sleep(RANK_REMOTE_REQUEST_INTERVAL_SECONDS * attempt)
    if last_error is None:
        raise RuntimeError(f"{period} rank page fetch failed without exception")
    raise last_error


def _fetch_top_period_records(
    period: str,
    user_agent: str,
    *,
    top_n: int,
) -> list[FundRankRecord]:
    records: list[FundRankRecord] = []
    page_index = 1

    while len(records) < top_n:
        remaining = top_n - len(records)
        page_size = min(EASTMONEY_FUND_RANK_PAGE_SIZE, remaining)
        payload = _fetch_rank_page_with_retry(
            period,
            user_agent,
            page_size=page_size,
            page_index=page_index,
        )
        page_records = parse_rank_payload(payload, period)
        if not page_records:
            break

        base_rank = len(records)
        for offset, record in enumerate(page_records[:page_size], start=1):
            record.rank_no = base_rank + offset
            records.append(record)

        if len(page_records) < page_size:
            break
        page_index += 1

    return records[:top_n]


def _build_cached_rank_payload(records: list[FundRankRecord]) -> str:
    rows = [record.raw_payload for record in records if record.raw_payload]
    return f"var rankData = {{datas:{json.dumps(rows, ensure_ascii=False)},allRecords:{len(rows)}}};"


def _load_or_fetch_period_records(
    raw_dir: Path,
    period: str,
    user_agent: str,
    *,
    top_n: int,
    refresh: bool,
) -> tuple[list[FundRankRecord], str | None]:
    raw_path = expected_rank_files(raw_dir)[period]
    local_records = parse_rank_file(raw_path, period)[:top_n] if raw_path.exists() else []
    local_snapshot_date = local_records[0].snapshot_date if local_records else ""
    if (
        local_records
        and len(local_records) >= top_n
        and local_snapshot_date == _current_snapshot_date()
        and not refresh
    ):
        return local_records[:top_n], None

    try:
        fetched_records = _fetch_top_period_records(period, user_agent, top_n=top_n)
    except (OSError, error.URLError, TimeoutError, subprocess.CalledProcessError) as exc:
        if local_records:
            return (
                local_records,
                f"{PERIOD_LABELS[period]}排行榜补抓失败，继续使用本地 {len(local_records)}/{top_n} 条：{exc}",
            )
        raise

    if fetched_records:
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(_build_cached_rank_payload(fetched_records), encoding="utf-8")

    if fetched_records and len(fetched_records) >= len(local_records):
        records = fetched_records
    else:
        records = local_records

    if not records:
        if refresh:
            return [], f"{PERIOD_LABELS[period]}排行榜刷新后为空。"
        return [], f"{PERIOD_LABELS[period]}排行榜补抓后为空。"
    if len(records) < top_n:
        if refresh:
            return records, f"{PERIOD_LABELS[period]}排行榜刷新后仅有 {len(records)}/{top_n} 条。"
        return records, f"{PERIOD_LABELS[period]}排行榜补抓后仍只有 {len(records)}/{top_n} 条。"
    return records[:top_n], None


def fetch_period_rank_records(
    raw_dir: Path,
    user_agent: str,
    *,
    top_n: int,
    refresh: bool = False,
) -> tuple[dict[str, list[FundRankRecord]], list[str]]:
    records_by_period: dict[str, list[FundRankRecord]] = {}
    warnings: list[str] = []

    for period in PERIOD_ORDER:
        try:
            records, warning = _load_or_fetch_period_records(
                raw_dir,
                period,
                user_agent,
                top_n=top_n,
                refresh=refresh,
            )
            records_by_period[period] = records
            if warning:
                warnings.append(warning)
        except (OSError, error.URLError, TimeoutError, subprocess.CalledProcessError) as exc:
            records_by_period[period] = []
            warnings.append(f"{PERIOD_LABELS[period]}排行榜抓取失败: {exc}")
        except (ValueError, json.JSONDecodeError) as exc:
            records_by_period[period] = []
            warnings.append(f"{PERIOD_LABELS[period]}排行榜解析失败: {exc}")

    return records_by_period, warnings
