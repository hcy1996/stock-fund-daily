from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.models import FundRankRecord


EASTMONEY_FUND_RANK_BASE_URL = "https://fund.eastmoney.com/data/rankhandler.aspx"
EASTMONEY_FUND_RANK_REFERER = "https://fund.eastmoney.com/data/fundranking.html"
EASTMONEY_FUND_RANK_PERIODS = {
    "day": {
        "filename": "fund_rank_day.js",
        "status_days": 1,
        "sort_field": "rzdf",
        "title": "当日基金排行",
        "value_label": "日增长率",
    },
    "week": {
        "filename": "fund_rank_week.js",
        "status_days": 7,
        "sort_field": "zzf",
        "title": "近一周基金排行",
        "value_label": "近1周",
    },
    "month": {
        "filename": "fund_rank_month.js",
        "status_days": 30,
        "sort_field": "1yzf",
        "title": "近一月基金排行",
        "value_label": "近1月",
    },
    "quarter": {
        "filename": "fund_rank_quarter.js",
        "status_days": 90,
        "sort_field": "3yzf",
        "title": "近三月基金排行",
        "value_label": "近3月",
    },
    "half_year": {
        "filename": "fund_rank_half_year.js",
        "status_days": 180,
        "sort_field": "6yzf",
        "title": "近六月基金排行",
        "value_label": "近6月",
    },
    "year": {
        "filename": "fund_rank_year.js",
        "status_days": 365,
        "sort_field": "1nzf",
        "title": "近一年基金排行",
        "value_label": "近1年",
    },
}
EASTMONEY_FUND_RANK_SOURCE = "eastmoney_fund_rank"
EASTMONEY_FUND_RANK_PAGE_SIZE = 150
_DATAS_PATTERN = re.compile(r"datas\s*:\s*(\[.*?\])\s*,\s*allRecords", re.S)


def build_rank_url(
    period: str,
    page_size: int = EASTMONEY_FUND_RANK_PAGE_SIZE,
    page_index: int = 1,
) -> str:
    config = EASTMONEY_FUND_RANK_PERIODS[period]
    query = urlencode(
        {
            "op": "ph",
            "dt": "kf",
            "ft": "all",
            "rs": "",
            "gs": "0",
            "sc": config["sort_field"],
            "st": "desc",
            "pi": str(page_index),
            "pn": str(page_size),
            "dx": "1",
        }
    )
    return f"{EASTMONEY_FUND_RANK_BASE_URL}?{query}"


def expected_rank_files(raw_dir: Path) -> dict[str, Path]:
    return {
        period: raw_dir / "eastmoney" / str(config["filename"])
        for period, config in EASTMONEY_FUND_RANK_PERIODS.items()
    }


def _to_float(value: str) -> float | None:
    if value in {"", "-", "--"}:
        return None
    return float(value)


def _field(fields: list[str], index: int) -> str:
    if index >= len(fields):
        return ""
    return fields[index].strip()


def _extract_datas(raw_payload: str) -> list[str]:
    match = _DATAS_PATTERN.search(raw_payload)
    if not match:
        return []
    return json.loads(match.group(1))


def parse_rank_payload(raw_payload: str, period: str) -> list[FundRankRecord]:
    rows = _extract_datas(raw_payload)
    snapshot_date = max(
        (_field(row.split(","), 3) for row in rows),
        default="",
    )
    if not snapshot_date:
        return []

    records: list[FundRankRecord] = []
    for index, row in enumerate(rows, start=1):
        fields = row.split(",")
        fund_code = _field(fields, 0)
        fund_name = _field(fields, 1)
        if not fund_code or not fund_name:
            continue

        records.append(
            FundRankRecord(
                snapshot_date=snapshot_date,
                ranking_period=period,
                fund_code=fund_code,
                fund_name=fund_name,
                net_value_date=_field(fields, 3) or None,
                unit_net_value=_to_float(_field(fields, 4)),
                accumulated_net_value=_to_float(_field(fields, 5)),
                daily_growth_pct=_to_float(_field(fields, 6)),
                weekly_growth_pct=_to_float(_field(fields, 7)),
                monthly_growth_pct=_to_float(_field(fields, 8)),
                quarter_growth_pct=_to_float(_field(fields, 9)),
                half_year_growth_pct=_to_float(_field(fields, 10)),
                year_growth_pct=_to_float(_field(fields, 11)),
                rank_no=index,
                raw_payload=row,
            )
        )
    return records


def parse_rank_file(path: Path, period: str) -> list[FundRankRecord]:
    if not path.exists():
        return []
    return parse_rank_payload(path.read_text(encoding="utf-8-sig"), period)


def fetch_rank_payload(
    period: str,
    user_agent: str,
    *,
    page_size: int = EASTMONEY_FUND_RANK_PAGE_SIZE,
    page_index: int = 1,
) -> str:
    url = build_rank_url(period, page_size=page_size, page_index=page_index)
    request = Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Referer": EASTMONEY_FUND_RANK_REFERER,
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            return response.read().decode("utf-8", errors="ignore")
    except OSError:
        completed = subprocess.run(
            [
                "curl",
                "-L",
                "--silent",
                "--show-error",
                "--fail",
                "-A",
                user_agent,
                "-H",
                f"Referer: {EASTMONEY_FUND_RANK_REFERER}",
                url,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout
