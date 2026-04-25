from __future__ import annotations

from datetime import datetime
import re
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

from app.models import SectorFlowRecord


TONGHUASHUN_1D_URL = "https://data.10jqka.com.cn/funds/gnzjl/field/tradezdf/order/desc/page/1/free/1/"
TONGHUASHUN_3D_URL = (
    "https://data.10jqka.com.cn/funds/gnzjl/board/3/field/tradezdf/order/desc/page/1/free/1/"
)
TONGHUASHUN_5D_URL = (
    "https://data.10jqka.com.cn/funds/gnzjl/board/5/field/tradezdf/order/desc/page/1/free/1/"
)
TONGHUASHUN_10D_URL = (
    "https://data.10jqka.com.cn/funds/gnzjl/board/10/field/tradezdf/order/desc/page/1/free/1/"
)
TONGHUASHUN_20D_URL = (
    "https://data.10jqka.com.cn/funds/gnzjl/board/20/field/tradezdf/order/desc/page/1/free/1/"
)


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
            text = re.sub(r"\s+", " ", text).strip()
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


def _parse_cn_amount(text: str) -> float | None:
    cleaned = text.replace(",", "").replace(" ", "")
    if not cleaned or cleaned == "-":
        return None
    match = re.search(r"([+-]?\d+(?:\.\d+)?)", cleaned)
    if not match:
        return None
    value = float(match.group(1))
    if "亿" in cleaned:
        return value * 100000000
    if "万" in cleaned:
        return value * 10000
    return value


def _parse_cn_amount_yi(text: str) -> float | None:
    value = _parse_cn_amount(text)
    if value is None:
        return None
    if "亿" in text or "万" in text:
        return value
    return value * 100000000


def _parse_pct(text: str) -> float | None:
    match = re.search(r"([+-]?\d+(?:\.\d+)?)%", text)
    if not match:
        return None
    return float(match.group(1))


def parse_board_file(path: Path, window_days: int) -> list[SectorFlowRecord]:
    if not path.exists():
        return []

    html = path.read_text(encoding="gbk", errors="ignore")
    parser = _TableParser()
    parser.feed(html)
    if not parser.tables:
        return []

    candidate = max(parser.tables, key=lambda table: len(table))
    if len(candidate) <= 1:
        return []

    rows = candidate[1:]
    records: list[SectorFlowRecord] = []
    trade_date_match = re.search(r"(\d{4}-\d{2}-\d{2})", html)
    trade_date = (
        trade_date_match.group(1)
        if trade_date_match
        else datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    )

    for index, row in enumerate(rows, start=1):
        if len(row) < 8:
            continue
        sector_name = row[1].strip()
        if not sector_name:
            continue
        records.append(
            SectorFlowRecord(
                trade_date=trade_date,
                window_days=window_days,
                source="tonghuashun",
                sector_code=sector_name,
                sector_name=sector_name,
                latest_index_value=_parse_cn_amount(row[3]),
                pct_change=_parse_pct(row[4]),
                main_net_inflow=_parse_cn_amount_yi(row[7]),
                rank_no=index,
                raw_payload=" | ".join(row),
            )
        )
        if len(records) >= 50:
            break

    return records


def parse_20d_file(path: Path) -> list[SectorFlowRecord]:
    return parse_board_file(path, 20)
