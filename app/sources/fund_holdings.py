from __future__ import annotations

from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
import re
import subprocess
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from app.models import FundHoldingRecord, FundRankRecord
from app.sources.fund_rank import expected_rank_files, parse_rank_file


EASTMONEY_FUND_HOLDINGS_BASE_URL = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
EASTMONEY_FUND_HOLDINGS_REFERER = "https://fundf10.eastmoney.com/ccmx_{fund_code}.html"
EASTMONEY_FUND_HOLDINGS_DIR = Path("eastmoney") / "fund_holdings"
_CONTENT_PATTERN = re.compile(r'content:"(.*?)",arryear', re.S)
_REPORT_DATE_PATTERN = re.compile(r"截止至：\s*<font[^>]*>\s*(\d{4}-\d{2}-\d{2})\s*</font>", re.S)
_FUND_TITLE_PATTERN = re.compile(r"<a\s+title='([^']+)'")
_TAG_PATTERN = re.compile(r"<[^>]+>")
_FLOAT_PATTERN = re.compile(r"([+-]?\d+(?:\.\d+)?)")


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._current_table: list[list[str]] | None = None
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._current_table = []
        elif tag == "tr" and self._current_table is not None:
            self._current_row = []
        elif tag in {"td", "th"} and self._current_row is not None:
            self._current_cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._current_row is not None and self._current_cell is not None:
            text = "".join(self._current_cell)
            text = re.sub(r"\s+", " ", unescape(text)).strip()
            self._current_row.append(text)
            self._current_cell = None
        elif tag == "tr" and self._current_table is not None and self._current_row is not None:
            if any(cell for cell in self._current_row):
                self._current_table.append(self._current_row)
            self._current_row = None
        elif tag == "table" and self._current_table is not None:
            if self._current_table:
                self.tables.append(self._current_table)
            self._current_table = None

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)


def holdings_raw_path(raw_dir: Path, fund_code: str) -> Path:
    return raw_dir / EASTMONEY_FUND_HOLDINGS_DIR / f"{fund_code}.js"


def build_holdings_url(fund_code: str, year: int | None = None, top_line: int = 10) -> str:
    query_year = year or datetime.now(tz=ZoneInfo("Asia/Shanghai")).year
    return (
        f"{EASTMONEY_FUND_HOLDINGS_BASE_URL}"
        f"?type=jjcc&code={fund_code}&topline={top_line}&year={query_year}&month="
    )


def _extract_content(raw_payload: str) -> str:
    match = _CONTENT_PATTERN.search(raw_payload)
    if not match:
        return ""
    return match.group(1).replace(r"\"", '"')


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(_TAG_PATTERN.sub("", value))).strip()


def _to_float(value: str) -> float | None:
    cleaned = value.replace(",", "").replace("%", "").strip()
    if cleaned in {"", "-", "--"}:
        return None
    match = _FLOAT_PATTERN.search(cleaned)
    if not match:
        return None
    return float(match.group(1))


def parse_holdings_payload(raw_payload: str, fund_code: str) -> list[FundHoldingRecord]:
    content = _extract_content(raw_payload)
    if not content:
        return []

    date_match = _REPORT_DATE_PATTERN.search(content)
    report_date = date_match.group(1) if date_match else ""
    if not report_date:
        return []

    title_match = _FUND_TITLE_PATTERN.search(content)
    fund_name = _clean_text(title_match.group(1)) if title_match else fund_code

    parser = _TableParser()
    parser.feed(content)
    if not parser.tables:
        return []

    rows = parser.tables[0]
    if rows and rows[0] and rows[0][0] in {"序号", "排名"}:
        rows = rows[1:]
    records: list[FundHoldingRecord] = []
    for row in rows:
        if len(row) < 6:
            continue
        stock_code = row[1].strip()
        stock_name = row[2].strip()
        if not re.fullmatch(r"\d{6}", stock_code) or not stock_name:
            continue
        records.append(
            FundHoldingRecord(
                fund_code=fund_code,
                fund_name=fund_name,
                report_date=report_date,
                stock_code=stock_code,
                stock_name=stock_name,
                net_value_ratio=_to_float(row[-3]),
                shares_wan=_to_float(row[-2]),
                market_value_wan=_to_float(row[-1]),
                rank_no=int(_to_float(row[0]) or 0) or None,
                raw_payload=" | ".join(row),
            )
        )
    return records


def parse_holdings_file(path: Path, fund_code: str) -> list[FundHoldingRecord]:
    if not path.exists():
        return []
    return parse_holdings_payload(path.read_text(encoding="utf-8"), fund_code)


def collect_rank_funds(raw_dir: Path, top_n: int) -> list[FundRankRecord]:
    funds: dict[str, FundRankRecord] = {}
    for period, path in expected_rank_files(raw_dir).items():
        for record in parse_rank_file(path, period)[:top_n]:
            funds.setdefault(record.fund_code, record)
    return list(funds.values())


def fetch_holdings_payload(fund_code: str, user_agent: str, *, year: int | None = None) -> str:
    url = build_holdings_url(fund_code, year=year)
    referer = EASTMONEY_FUND_HOLDINGS_REFERER.format(fund_code=fund_code)
    request = Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Referer": referer,
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
                f"Referer: {referer}",
                url,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout


def fetch_rank_fund_holdings(raw_dir: Path, user_agent: str, top_n: int) -> int:
    funds = collect_rank_funds(raw_dir, top_n)
    output_dir = raw_dir / EASTMONEY_FUND_HOLDINGS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    updated = 0
    current_year = datetime.now(tz=ZoneInfo("Asia/Shanghai")).year
    for fund in funds:
        try:
            payload = fetch_holdings_payload(fund.fund_code, user_agent, year=current_year)
            records = parse_holdings_payload(payload, fund.fund_code)
            if not records and current_year > 2000:
                payload = fetch_holdings_payload(fund.fund_code, user_agent, year=current_year - 1)
                records = parse_holdings_payload(payload, fund.fund_code)
        except (OSError, subprocess.CalledProcessError):
            continue
        if not records:
            continue
        holdings_raw_path(raw_dir, fund.fund_code).write_text(payload, encoding="utf-8")
        updated += 1
    return updated
