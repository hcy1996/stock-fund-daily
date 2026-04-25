from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from html.parser import HTMLParser
import json
import re
from pathlib import Path
import subprocess
from urllib.request import Request, urlopen

from app.fetch_status import fetch_succeeded, load_fetch_status
from app.models import SectorComponentRecord, SectorFlowRecord
from app.sources.eastmoney import parse_window_file
from app.sources.tonghuashun import parse_board_file


EASTMONEY_20D_ROLLUP_PATH = Path("eastmoney") / "20d.json"
EASTMONEY_WINDOW_ROLLUP_PATHS = {
    1: Path("eastmoney") / "1d_fallback.json",
    3: Path("eastmoney") / "3d_fallback.json",
    5: Path("eastmoney") / "5d_fallback.json",
    10: Path("eastmoney") / "10d_fallback.json",
}
TONGHUASHUN_COMPONENTS_PATH = Path("tonghuashun") / "components_top10.json"

EASTMONEY_20D_HISTORY_URL = (
    "http://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
    "?lmt=0&klt=101"
    "&fields1=f1,f2,f3,f7"
    "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"
    "&ut=b2884a393a59ad64002292a3e90d46a5"
)

TONGHUASHUN_CONCEPT_INDEX_URL = "https://q.10jqka.com.cn/gn/"
TONGHUASHUN_CONCEPT_DETAIL_URL = "https://q.10jqka.com.cn/gn/detail/code/{concept_code}/"
EASTMONEY_CONCEPT_INDEX_URLS = (
    "https://data.eastmoney.com/bkzj/gn.html",
    "http://data.eastmoney.com/bkzj/gn.html",
    "https://quote.eastmoney.com/center/boardlist.html#concept_board",
    "http://quote.eastmoney.com/center/boardlist.html#concept_board",
    "https://quote.eastmoney.com/center/gridlist.html#boards-notion",
    "http://quote.eastmoney.com/center/gridlist.html#boards-notion",
)

_FLOAT_PATTERN = re.compile(r"([+-]?\d+(?:\.\d+)?)")
_THS_CONCEPT_LINK_PATTERN = re.compile(
    r'href="https?://q\.10jqka\.com\.cn/gn/detail/code/(\d+)/"[^>]*>([^<]+)</a>'
)
_THS_COMPONENT_TABLE_PATTERN = re.compile(
    r'<table class="m-table m-pager-table">.*?</table>',
    re.S,
)
_SECTOR_NAME_NORMALIZE_PATTERN = re.compile(r"[\s()（）\-_/.]+")
_EASTMONEY_BOARD_CODE_PATTERN = re.compile(r"BK\d+")
_VALID_EASTMONEY_BOARD_NAME_PATTERN = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")


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


class _EastmoneyLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._current_href: str | None = None
        self._current_text: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = ""
        for key, value in attrs:
            if key == "href" and value:
                href = value
                break
        if _EASTMONEY_BOARD_CODE_PATTERN.search(href):
            self._current_href = href
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._current_text is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._current_href or self._current_text is None:
            return
        code_match = _EASTMONEY_BOARD_CODE_PATTERN.search(self._current_href)
        text = re.sub(r"\s+", " ", "".join(self._current_text)).strip()
        if code_match and text and _VALID_EASTMONEY_BOARD_NAME_PATTERN.search(text):
            self.links.append((code_match.group(0), text))
        self._current_href = None
        self._current_text = None


def _normalize_sector_name(name: str) -> str:
    return _SECTOR_NAME_NORMALIZE_PATTERN.sub("", name)


def _request_bytes(url: str, user_agent: str, referer: str | None = None) -> bytes:
    headers = {"User-Agent": user_agent}
    if referer:
        headers["Referer"] = referer

    last_error: OSError | None = None
    for _ in range(2):
        try:
            request = Request(url, headers=headers)
            with urlopen(request, timeout=20) as response:
                return response.read()
        except OSError as exc:
            last_error = exc

    curl_cmd = [
        "curl",
        "-L",
        "--silent",
        "--show-error",
        "--fail",
        "-A",
        user_agent,
    ]
    if referer:
        curl_cmd.extend(["-H", f"Referer: {referer}"])
    curl_cmd.append(url)
    try:
        completed = subprocess.run(
            curl_cmd,
            check=True,
            capture_output=True,
            text=False,
        )
        return completed.stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        if isinstance(exc, OSError):
            last_error = exc
        elif exc.stderr:
            last_error = OSError(exc.stderr.decode("utf-8", errors="ignore").strip())

    if last_error is not None:
        raise last_error
    raise RuntimeError(f"request failed: {url}")


def _fetch_json(url: str, user_agent: str, referer: str | None = None) -> dict:
    payload = _request_bytes(url, user_agent, referer=referer)
    return json.loads(payload.decode("utf-8"))


def _fetch_text(
    url: str,
    user_agent: str,
    *,
    encoding: str,
    referer: str | None = None,
) -> str:
    payload = _request_bytes(url, user_agent, referer=referer)
    return payload.decode(encoding, errors="ignore")


def _fetch_text_from_candidates(
    urls: tuple[str, ...],
    user_agent: str,
    *,
    encoding: str,
    referer: str | None = None,
) -> str:
    last_error: OSError | RuntimeError | None = None
    for url in urls:
        try:
            return _fetch_text(url, user_agent, encoding=encoding, referer=referer)
        except (OSError, RuntimeError) as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise RuntimeError("no candidate urls")


def _to_float(value: str) -> float | None:
    if value in {"", "-", "--"}:
        return None
    match = _FLOAT_PATTERN.search(value.replace(",", ""))
    if not match:
        return None
    return float(match.group(1))


def _build_20d_history_record(record: SectorFlowRecord, user_agent: str) -> SectorFlowRecord | None:
    history_url = f"{EASTMONEY_20D_HISTORY_URL}&secid=90.{record.sector_code}"
    payload = _fetch_json(history_url, user_agent, referer="https://data.eastmoney.com/")
    klines = payload.get("data", {}).get("klines", [])
    if len(klines) < 20:
        return None

    recent_klines = klines[-20:]
    main_total = 0.0
    super_total = 0.0
    large_total = 0.0
    medium_total = 0.0
    small_total = 0.0

    for kline in recent_klines:
        values = kline.split(",")
        if len(values) < 6:
            return None
        main_total += float(values[1])
        small_total += float(values[2])
        medium_total += float(values[3])
        large_total += float(values[4])
        super_total += float(values[5])

    return SectorFlowRecord(
        trade_date=record.trade_date,
        window_days=20,
        source="eastmoney_history",
        sector_code=record.sector_code,
        sector_name=record.sector_name,
        latest_index_value=record.latest_index_value,
        pct_change=record.pct_change,
        main_net_inflow=main_total,
        super_order_inflow=super_total,
        large_order_inflow=large_total,
        medium_order_inflow=medium_total,
        small_order_inflow=small_total,
        leader_stock_name=record.leader_stock_name,
        leader_stock_code=record.leader_stock_code,
        leader_stock_pct_change=record.leader_stock_pct_change,
        raw_payload=json.dumps(
            {
                "sample_size": len(recent_klines),
                "history_code": record.sector_code,
            },
            ensure_ascii=False,
        ),
    )


def build_eastmoney_20d_rollup(
    daily_records: list[SectorFlowRecord],
    user_agent: str,
    *,
    max_workers: int = 8,
) -> list[SectorFlowRecord]:
    records: list[SectorFlowRecord] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_build_20d_history_record, record, user_agent): record.sector_code
            for record in daily_records
        }
        for future in as_completed(futures):
            try:
                record = future.result()
            except (OSError, ValueError):
                continue
            if record is not None:
                records.append(record)

    ranked_records = sorted(
        records,
        key=lambda item: item.main_net_inflow if item.main_net_inflow is not None else float("-inf"),
        reverse=True,
    )
    for index, record in enumerate(ranked_records, start=1):
        record.rank_no = index
    return ranked_records


def _save_eastmoney_window_rollup(raw_dir: Path, window_days: int, records: list[SectorFlowRecord]) -> Path:
    output_path = raw_dir / EASTMONEY_WINDOW_ROLLUP_PATHS[window_days]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"records": [asdict(record) for record in records]}
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def load_eastmoney_window_rollup(path: Path) -> list[SectorFlowRecord]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [SectorFlowRecord(**item) for item in payload.get("records", [])]


def eastmoney_window_rollup_path(raw_dir: Path, window_days: int) -> Path:
    return raw_dir / EASTMONEY_WINDOW_ROLLUP_PATHS[window_days]


def save_eastmoney_20d_rollup(raw_dir: Path, records: list[SectorFlowRecord]) -> Path:
    output_path = raw_dir / EASTMONEY_20D_ROLLUP_PATH
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"records": [asdict(record) for record in records]}
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def load_eastmoney_20d_rollup(path: Path) -> list[SectorFlowRecord]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [SectorFlowRecord(**item) for item in payload.get("records", [])]


def _parse_eastmoney_concept_catalog(html: str) -> list[tuple[str, str]]:
    parser = _EastmoneyLinkParser()
    parser.feed(html)

    catalog: list[tuple[str, str]] = []
    seen_codes: set[str] = set()
    for sector_code, sector_name in parser.links:
        clean_name = re.sub(r"\s+", " ", sector_name).strip()
        if (
            not clean_name
            or sector_code in seen_codes
            or len(clean_name) > 24
            or clean_name in {"概念板块", "行业板块", "地域板块", "更多", "详情"}
        ):
            continue
        seen_codes.add(sector_code)
        catalog.append((sector_code, clean_name))
    return catalog


def _fetch_eastmoney_concept_catalog(user_agent: str) -> list[tuple[str, str]]:
    html = _fetch_text_from_candidates(
        EASTMONEY_CONCEPT_INDEX_URLS,
        user_agent,
        encoding="utf-8",
    )
    return _parse_eastmoney_concept_catalog(html)


def _build_eastmoney_window_records_from_klines(
    sector_code: str,
    sector_name: str,
    klines: list[str],
    window_days: tuple[int, ...],
) -> dict[int, SectorFlowRecord]:
    rows = [kline.split(",") for kline in klines if kline]
    if not rows:
        return {}

    last_row = rows[-1]
    if len(last_row) < 13:
        return {}

    latest_index_value = float(last_row[11])
    pct_change = float(last_row[12])
    trade_date = last_row[0]
    built: dict[int, SectorFlowRecord] = {}
    for days in window_days:
        if len(rows) < days:
            continue
        recent_rows = rows[-days:]
        main_net_inflow = sum(float(item[1]) for item in recent_rows if len(item) > 1)
        built[days] = SectorFlowRecord(
            trade_date=trade_date,
            window_days=days,
            source="eastmoney",
            sector_code=sector_code,
            sector_name=sector_name,
            latest_index_value=latest_index_value,
            pct_change=pct_change,
            main_net_inflow=main_net_inflow,
            raw_payload=json.dumps(
                {
                    "derived_from": "push2his",
                    "sample_size": len(recent_rows),
                    "sector_code": sector_code,
                },
                ensure_ascii=False,
            ),
        )
    return built


def _fetch_eastmoney_history_window_bundle(
    sector_code: str,
    sector_name: str,
    user_agent: str,
    window_days: tuple[int, ...],
) -> dict[int, SectorFlowRecord]:
    history_url = f"{EASTMONEY_20D_HISTORY_URL}&secid=90.{sector_code}"
    payload = _fetch_json(history_url, user_agent, referer="https://data.eastmoney.com/")
    klines = payload.get("data", {}).get("klines", [])
    return _build_eastmoney_window_records_from_klines(
        sector_code,
        sector_name,
        klines,
        window_days,
    )


def build_eastmoney_window_rollups_from_history(
    user_agent: str,
    *,
    window_days: tuple[int, ...] = (1, 3, 5, 10),
    max_workers: int = 6,
) -> dict[int, list[SectorFlowRecord]]:
    catalog = _fetch_eastmoney_concept_catalog(user_agent)
    if not catalog:
        return {days: [] for days in window_days}

    collected = {days: [] for days in window_days}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _fetch_eastmoney_history_window_bundle,
                sector_code,
                sector_name,
                user_agent,
                window_days,
            ): sector_code
            for sector_code, sector_name in catalog
        }
        for future in as_completed(futures):
            try:
                bundle = future.result()
            except (OSError, ValueError, RuntimeError):
                continue
            for days, record in bundle.items():
                collected[days].append(record)

    for days, records in collected.items():
        ranked_records = sorted(
            records,
            key=lambda item: item.main_net_inflow if item.main_net_inflow is not None else float("-inf"),
            reverse=True,
        )
        for index, record in enumerate(ranked_records, start=1):
            record.rank_no = index
        collected[days] = ranked_records
    return collected


def _parse_tonghuashun_concept_index(html: str) -> tuple[dict[str, str], dict[str, str]]:
    exact_map: dict[str, str] = {}
    normalized_map: dict[str, str] = {}

    for concept_code, sector_name in _THS_CONCEPT_LINK_PATTERN.findall(html):
        clean_name = re.sub(r"\s+", " ", sector_name).strip()
        if not clean_name:
            continue
        exact_map.setdefault(clean_name, concept_code)
        normalized_map.setdefault(_normalize_sector_name(clean_name), concept_code)

    return exact_map, normalized_map


def _match_tonghuashun_concept_code(
    sector_name: str,
    exact_map: dict[str, str],
    normalized_map: dict[str, str],
) -> tuple[str | None, str | None]:
    if sector_name in exact_map:
        return exact_map[sector_name], "exact"

    normalized_name = _normalize_sector_name(sector_name)
    if normalized_name in normalized_map:
        return normalized_map[normalized_name], "normalized"

    return None, None


def _parse_tonghuashun_component_rows(
    html: str,
    sector_name: str,
) -> list[SectorComponentRecord]:
    table_match = _THS_COMPONENT_TABLE_PATTERN.search(html)
    if not table_match:
        return []

    parser = _TableParser()
    parser.feed(table_match.group(0))
    if not parser.tables:
        return []

    rows = parser.tables[0][1:]
    components: list[SectorComponentRecord] = []
    for row in rows[:10]:
        if len(row) < 5:
            continue
        components.append(
            SectorComponentRecord(
                sector_name=sector_name,
                stock_code=row[1],
                stock_name=row[2],
                latest_price=_to_float(row[3]),
                pct_change=_to_float(row[4]),
                rank_no=int(row[0]) if row[0].isdigit() else None,
                raw_payload=" | ".join(row),
            )
        )
    return components


def build_tonghuashun_top_components(
    daily_records: list[SectorFlowRecord],
    user_agent: str,
    *,
    sector_count: int,
) -> dict:
    concept_index_html = _fetch_text(
        TONGHUASHUN_CONCEPT_INDEX_URL,
        user_agent,
        encoding="gbk",
    )
    exact_map, normalized_map = _parse_tonghuashun_concept_index(concept_index_html)

    sectors_payload: list[dict] = []
    unmatched_sectors: list[str] = []

    for record in daily_records[:sector_count]:
        concept_code, match_source = _match_tonghuashun_concept_code(
            record.sector_name,
            exact_map,
            normalized_map,
        )
        if not concept_code:
            unmatched_sectors.append(record.sector_name)
            continue

        try:
            detail_html = _fetch_text(
                TONGHUASHUN_CONCEPT_DETAIL_URL.format(concept_code=concept_code),
                user_agent,
                encoding="gbk",
                referer=TONGHUASHUN_CONCEPT_INDEX_URL,
            )
        except OSError:
            unmatched_sectors.append(record.sector_name)
            continue
        components = _parse_tonghuashun_component_rows(detail_html, record.sector_name)
        if not components:
            try:
                detail_html = _fetch_text(
                    TONGHUASHUN_CONCEPT_DETAIL_URL.format(concept_code=concept_code),
                    user_agent,
                    encoding="gbk",
                    referer=TONGHUASHUN_CONCEPT_INDEX_URL,
                )
            except OSError:
                detail_html = ""
            if detail_html:
                components = _parse_tonghuashun_component_rows(detail_html, record.sector_name)
        sectors_payload.append(
            {
                "sector_name": record.sector_name,
                "concept_code": concept_code,
                "match_source": match_source,
                "components": [asdict(component) for component in components],
            }
        )

    return {
        "sectors": sectors_payload,
        "unmatched_sectors": unmatched_sectors,
    }


def save_tonghuashun_top_components(raw_dir: Path, payload: dict) -> Path:
    output_path = raw_dir / TONGHUASHUN_COMPONENTS_PATH
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def load_tonghuashun_top_components(path: Path) -> tuple[dict[str, list[SectorComponentRecord]], list[str]]:
    if not path.exists():
        return {}, []

    payload = json.loads(path.read_text(encoding="utf-8"))
    sector_map = {
        item["sector_name"]: [SectorComponentRecord(**component) for component in item.get("components", [])]
        for item in payload.get("sectors", [])
    }
    return sector_map, list(payload.get("unmatched_sectors", []))


def enrich_raw_data(raw_dir: Path, user_agent: str, top_sector_count: int) -> dict[str, int]:
    fetch_status = load_fetch_status(raw_dir)
    eastmoney_available = fetch_succeeded(fetch_status, "eastmoney", 1)
    tonghuashun_available = fetch_succeeded(fetch_status, "tonghuashun", 1)

    eastmoney_daily_records = parse_window_file(raw_dir / "eastmoney" / "1d.json", 1) if eastmoney_available else []
    if eastmoney_available and len(eastmoney_daily_records) < top_sector_count:
        eastmoney_available = False

    daily_records = eastmoney_daily_records
    if not daily_records and tonghuashun_available:
        daily_records = parse_board_file(raw_dir / "tonghuashun" / "1d.html", 1)

    if not daily_records:
        raise RuntimeError("缺少可用的 1 日板块数据，无法生成派生数据。")

    eastmoney_window_rollups = {window_days: [] for window_days in (1, 3, 5, 10)}
    if not eastmoney_available:
        try:
            eastmoney_window_rollups = build_eastmoney_window_rollups_from_history(user_agent)
        except (OSError, RuntimeError, ValueError):
            eastmoney_window_rollups = {window_days: [] for window_days in (1, 3, 5, 10)}
        fallback_daily_records = eastmoney_window_rollups[1]
        if fallback_daily_records:
            daily_records = fallback_daily_records

    for window_days, records in eastmoney_window_rollups.items():
        _save_eastmoney_window_rollup(raw_dir, window_days, records)

    rollup_records = []
    if eastmoney_available:
        rollup_records = build_eastmoney_20d_rollup(eastmoney_daily_records, user_agent)
    save_eastmoney_20d_rollup(raw_dir, rollup_records)

    components_payload = build_tonghuashun_top_components(
        daily_records,
        user_agent,
        sector_count=top_sector_count,
    )
    save_tonghuashun_top_components(raw_dir, components_payload)

    return {
        "rollup_count": len(rollup_records),
        "eastmoney_fallback_daily_count": len(eastmoney_window_rollups[1]),
        "component_sector_count": len(components_payload["sectors"]),
        "component_unmatched_count": len(components_payload["unmatched_sectors"]),
    }
