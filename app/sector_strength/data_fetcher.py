from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

try:
    import akshare as ak
except ImportError:  # pragma: no cover - runtime fallback
    ak = None


CONCEPT_BOARD_FS = "m:90 t:3 f:!50"
INDUSTRY_BOARD_FS = "m:90 t:2 f:!50"
BOARD_LIST_URL = "https://79.push2.eastmoney.com/api/qt/clist/get"
BOARD_COMPONENT_URL = "https://29.push2.eastmoney.com/api/qt/clist/get"
BOARD_KLINE_URL = "https://91.push2his.eastmoney.com/api/qt/stock/kline/get"
INDEX_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
LIMIT_UP_POOL_URL = "https://push2ex.eastmoney.com/getTopicZTPool"
BOARD_UT = "bd1d9ddb04089700cf9c27f6f7426281"
LIMIT_UP_UT = "7eea3edcaed734bea9cbfc24409ed989"
BOARD_CODE_PATTERN = re.compile(r"^BK\d+$", re.I)
NORMALIZE_NAME_PATTERN = re.compile(r"[\s()（）\-_/.]+")
THS_COMPONENT_TABLE_PATTERN = re.compile(r'<table class="m-table m-pager-table">.*?</table>', re.S)
THS_PAGE_INFO_PATTERN = re.compile(r'<span class="page_info">(\d+)/(\d+)</span>')
FUND_FLOW_PERIOD_LABELS = {
    1: "即时",
    3: "3日排行",
    5: "5日排行",
    10: "10日排行",
}
THS_CONCEPT_DETAIL_URL = "https://q.10jqka.com.cn/gn/detail/code/{code}/"
THS_INDUSTRY_DETAIL_URL = "https://q.10jqka.com.cn/thshy/detail/code/{code}/"


@dataclass(slots=True)
class BoardCandidate:
    name: str
    code: str | None
    board_type: str | None
    rank_no: int | None
    trade_date: str | None
    pool_source: str
    pool_window_days: int | None
    in_candidate_pool: bool = True
    snapshot_flows: dict[int, float | None] = field(default_factory=dict)
    snapshot_flow_sources: dict[int, str | None] = field(default_factory=dict)
    snapshot_flow_history: dict[int, list[tuple[str, float | None, str | None]]] = field(default_factory=dict)


@dataclass(slots=True)
class BoardRegistryEntry:
    name: str
    code: str
    board_type: str


@dataclass(slots=True)
class BoardRegistryLookup:
    by_code: dict[str, BoardRegistryEntry]
    by_name: dict[str, list[BoardRegistryEntry]]
    by_normalized_name: dict[str, list[BoardRegistryEntry]]


@dataclass(slots=True)
class KlineBar:
    trade_date: str
    close: float | None
    pct_change: float | None
    amount: float | None


@dataclass(slots=True)
class NetFlowBar:
    trade_date: str
    main_net_inflow: float | None


@dataclass(slots=True)
class BoardFundFlow:
    window_days: int
    main_net_inflow: float | None
    source: str


@dataclass(slots=True)
class BoardComponent:
    stock_code: str
    stock_name: str
    pct_change: float | None
    amount: float | None
    turnover_rate: float | None
    is_limit_up: bool


@dataclass(slots=True)
class BoardMarketSnapshot:
    candidate: BoardCandidate
    resolved_name: str | None
    resolved_code: str | None
    board_type: str | None
    trade_date: str | None
    board_kline_60: list[KlineBar]
    benchmark_kline_60: list[KlineBar]
    fund_flow_hist_60: list[NetFlowBar]
    fund_flow_1d: BoardFundFlow | None
    fund_flow_3d: BoardFundFlow | None
    fund_flow_5d: BoardFundFlow | None
    fund_flow_10d: BoardFundFlow | None
    components: list[BoardComponent]
    data_sources: dict[str, str]
    warnings: list[str] = field(default_factory=list)


class _SimpleTableParser(HTMLParser):
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
            text = re.sub(r"\s+", " ", "".join(self._current_cell)).strip()
            self._current_row.append(text)
            self._current_cell = None
        elif tag == "tr" and self._current_table is not None and self._current_row is not None:
            if any(self._current_row):
                self._current_table.append(self._current_row)
            self._current_row = None
        elif tag == "table" and self._current_table is not None:
            if self._current_table:
                self.tables.append(self._current_table)
            self._current_table = None

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)


def _request_json(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    full_url = url
    if params:
        full_url = f"{url}?{urlencode(params)}"
    request = Request(full_url, headers={"User-Agent": "Mozilla/5.0"})
    last_error: Exception | None = None
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        last_error = exc

    try:
        completed = subprocess.run(
            [
                "curl",
                "-L",
                "--silent",
                "--show-error",
                "--fail",
                "-A",
                "Mozilla/5.0",
                full_url,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout)
    except Exception as exc:
        if last_error is not None:
            raise last_error
        raise exc


def _request_text(url: str, encoding: str = "utf-8") -> str:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    last_error: Exception | None = None
    try:
        with urlopen(request, timeout=20) as response:
            return response.read().decode(encoding, errors="ignore")
    except Exception as exc:
        last_error = exc

    try:
        completed = subprocess.run(
            [
                "curl",
                "-L",
                "--silent",
                "--show-error",
                "--fail",
                "-A",
                "Mozilla/5.0",
                url,
            ],
            check=True,
            capture_output=True,
        )
        return completed.stdout.decode(encoding, errors="ignore")
    except Exception as exc:
        if last_error is not None:
            raise last_error
        raise exc


def _safe_float(value: Any) -> float | None:
    if value in ("", "-", "--", None):
        return None
    if isinstance(value, str):
        value = value.replace(",", "").strip()
        if not value:
            return None
    return float(value)


def _parse_cn_amount(value: Any) -> float | None:
    if value in ("", "-", "--", None):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").strip()
    if not text:
        return None
    multiplier = 1.0
    if text.endswith("亿"):
        multiplier = 100000000.0
        text = text[:-1]
    elif text.endswith("万"):
        multiplier = 10000.0
        text = text[:-1]
    elif text.endswith("元"):
        text = text[:-1]
    text = text.replace("%", "")
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def _parse_cn_amount_yi_default(value: Any) -> float | None:
    """Parse THS-style fund-flow values, treating plain numbers as 亿元."""
    parsed = _parse_cn_amount(value)
    if parsed is None:
        return None
    if isinstance(value, str):
        text = value.replace(",", "").strip()
        if any(unit in text for unit in ("亿", "万", "元")):
            return parsed
    return parsed * 100000000.0


def _parse_pct(value: Any) -> float | None:
    if value in ("", "-", "--", None):
        return None
    if isinstance(value, str):
        value = value.strip().rstrip("%")
    return _safe_float(value)


def _normalize_name(name: str) -> str:
    return NORMALIZE_NAME_PATTERN.sub("", name)


def _parse_ths_table_rows(html: str) -> list[list[str]]:
    match = THS_COMPONENT_TABLE_PATTERN.search(html)
    if not match:
        return []
    parser = _SimpleTableParser()
    parser.feed(match.group(0))
    if not parser.tables:
        return []
    return parser.tables[0]


def _parse_ths_page_count(html: str) -> int:
    match = THS_PAGE_INFO_PATTERN.search(html)
    if not match:
        return 1
    return max(1, int(match.group(2)))


def _build_registry_lookup(entries: list[BoardRegistryEntry]) -> BoardRegistryLookup:
    by_code: dict[str, BoardRegistryEntry] = {}
    by_name: dict[str, list[BoardRegistryEntry]] = {}
    by_normalized_name: dict[str, list[BoardRegistryEntry]] = {}
    for entry in entries:
        by_code[entry.code.upper()] = entry
        by_name.setdefault(entry.name, []).append(entry)
        normalized = _normalize_name(entry.name)
        by_normalized_name.setdefault(normalized, []).append(entry)
    return BoardRegistryLookup(
        by_code=by_code,
        by_name=by_name,
        by_normalized_name=by_normalized_name,
    )


def _unique_entries(entries: list[BoardRegistryEntry]) -> list[BoardRegistryEntry]:
    dedup: dict[str, BoardRegistryEntry] = {}
    for entry in entries:
        dedup[f"{entry.board_type}:{entry.code.upper()}"] = entry
    return list(dedup.values())


def _entries_from_board_df(df: pd.DataFrame, board_type: str) -> list[BoardRegistryEntry]:
    if df.empty:
        return []
    entries: list[BoardRegistryEntry] = []
    for _, row in df.iterrows():
        name = str(row.get("板块名称", "")).strip()
        code = str(row.get("板块代码", "")).strip().upper()
        if not name or not code:
            continue
        entries.append(BoardRegistryEntry(name=name, code=code, board_type=board_type))
    return entries


def _fetch_board_list_from_eastmoney(board_type: str) -> list[BoardRegistryEntry]:
    fs = CONCEPT_BOARD_FS if board_type == "concept" else INDUSTRY_BOARD_FS
    payload = _request_json(
        BOARD_LIST_URL,
        {
            "pn": "1",
            "pz": "500",
            "po": "1",
            "np": "1",
            "ut": BOARD_UT,
            "fltt": "2",
            "invt": "2",
            "fid": "f12",
            "fs": fs,
            "fields": "f12,f14",
        },
    )
    diff = payload.get("data", {}).get("diff", []) or []
    return [
        BoardRegistryEntry(
            name=str(item.get("f14", "")).strip(),
            code=str(item.get("f12", "")).strip().upper(),
            board_type=board_type,
        )
        for item in diff
        if str(item.get("f12", "")).strip() and str(item.get("f14", "")).strip()
    ]


@lru_cache(maxsize=2)
def _load_ths_board_code_map(board_type: str) -> dict[str, str]:
    if ak is None:
        return {}
    if board_type == "concept":
        df = ak.stock_board_concept_name_ths()
    else:
        df = ak.stock_board_industry_name_ths()
    if df.empty:
        return {}
    return {
        str(row.get("name", "")).strip(): str(row.get("code", "")).strip()
        for _, row in df.iterrows()
        if str(row.get("name", "")).strip() and str(row.get("code", "")).strip()
    }


@lru_cache(maxsize=1)
def load_board_registry_lookup() -> BoardRegistryLookup:
    entries: list[BoardRegistryEntry] = []
    if ak is not None:
        try:
            entries.extend(_entries_from_board_df(ak.stock_board_concept_name_em(), "concept"))
        except Exception:
            pass
        try:
            entries.extend(_entries_from_board_df(ak.stock_board_industry_name_em(), "industry"))
        except Exception:
            pass
    if not entries:
        try:
            entries.extend(_fetch_board_list_from_eastmoney("concept"))
        except Exception:
            pass
        try:
            entries.extend(_fetch_board_list_from_eastmoney("industry"))
        except Exception:
            pass
    return _build_registry_lookup(_unique_entries(entries))


def resolve_candidate(candidate: BoardCandidate, lookup: BoardRegistryLookup | None) -> tuple[BoardRegistryEntry | None, list[str]]:
    warnings: list[str] = []
    if candidate.board_type in {"concept", "industry"} and (
        candidate.code is None or BOARD_CODE_PATTERN.match(candidate.code.upper())
    ):
        return (
            BoardRegistryEntry(
                name=candidate.name,
                code=candidate.code.upper() if candidate.code else "",
                board_type=candidate.board_type,
            ),
            warnings,
        )
    if lookup is None:
        warnings.append(f"{candidate.name}: 缺少板块名录，无法补全代码")
        return None, warnings
    if candidate.code and BOARD_CODE_PATTERN.match(candidate.code.upper()):
        entry = lookup.by_code.get(candidate.code.upper())
        if entry is not None:
            return entry, warnings
    exact_matches = lookup.by_name.get(candidate.name, [])
    if len(exact_matches) == 1:
        return exact_matches[0], warnings
    normalized_matches = lookup.by_normalized_name.get(_normalize_name(candidate.name), [])
    if len(normalized_matches) == 1:
        return normalized_matches[0], warnings
    if exact_matches or normalized_matches:
        warnings.append(f"{candidate.name}: 匹配到多个公开板块，已跳过自动解析")
    else:
        warnings.append(f"{candidate.name}: 未在公开板块列表中解析到代码")
    return None, warnings


def resolve_input_board(query: str, lookup: BoardRegistryLookup) -> tuple[BoardRegistryEntry | None, list[str]]:
    warnings: list[str] = []
    text = query.strip()
    if not text:
        return None, warnings
    if BOARD_CODE_PATTERN.match(text.upper()):
        entry = lookup.by_code.get(text.upper())
        if entry is None:
            warnings.append(f"{query}: 未找到对应板块代码")
        return entry, warnings
    exact_matches = lookup.by_name.get(text, [])
    if len(exact_matches) == 1:
        return exact_matches[0], warnings
    normalized_matches = lookup.by_normalized_name.get(_normalize_name(text), [])
    if len(normalized_matches) == 1:
        return normalized_matches[0], warnings

    fuzzy_matches: list[BoardRegistryEntry] = []
    for name, entries in lookup.by_name.items():
        if text in name:
            fuzzy_matches.extend(entries)
    fuzzy_matches = _unique_entries(fuzzy_matches)
    if len(fuzzy_matches) == 1:
        warnings.append(f"{query}: 使用包含匹配 {fuzzy_matches[0].name}")
        return fuzzy_matches[0], warnings
    if len(fuzzy_matches) > 1:
        warnings.append(f"{query}: 匹配到多个板块，请改用更精确名称或 BK 代码")
        return None, warnings
    warnings.append(f"{query}: 未找到可分析板块")
    return None, warnings


@lru_cache(maxsize=2)
def _load_fund_flow_code_map(board_type: str) -> dict[str, str]:
    if board_type == "concept":
        payload = _request_json(
            "https://push2.eastmoney.com/api/qt/clist/get",
            {
                "pn": "1",
                "pz": "500",
                "po": "1",
                "np": "1",
                "fields": "f12,f14",
                "fid": "f62",
                "fs": "m:90+t:3",
                "ut": "b2884a393a59ad64002292a3e90d46a5",
            },
        )
    else:
        payload = _request_json(
            "https://push2.eastmoney.com/api/qt/clist/get",
            {
                "fid": "f62",
                "po": "1",
                "pz": "500",
                "pn": "1",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "ut": "8dec03ba335b81bf4ebdf7b29ec27d15",
                "fs": "m:90 t:2",
                "fields": "f12,f14",
            },
        )
    diff = payload.get("data", {}).get("diff", []) or []
    return {
        str(item.get("f14", "")).strip(): str(item.get("f12", "")).strip().upper()
        for item in diff
        if str(item.get("f12", "")).strip() and str(item.get("f14", "")).strip()
    }


def _resolve_eastmoney_code(entry: BoardRegistryEntry) -> str | None:
    if entry.code and BOARD_CODE_PATTERN.match(entry.code.upper()):
        return entry.code.upper()
    try:
        code_map = _load_fund_flow_code_map(entry.board_type)
    except Exception:
        return None
    direct = code_map.get(entry.name)
    if direct:
        return direct
    normalized_entry = _normalize_name(entry.name)
    for name, code in code_map.items():
        if _normalize_name(name) == normalized_entry:
            return code
    return None


def _today_ymd() -> str:
    return datetime.now().strftime("%Y%m%d")


def _start_date_for_days(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")


def _kline_from_eastmoney(code: str, start_date: str, end_date: str) -> list[KlineBar]:
    payload = _request_json(
        BOARD_KLINE_URL,
        {
            "secid": f"90.{code}",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "101",
            "fqt": "0",
            "beg": start_date,
            "end": end_date,
            "smplmt": "10000",
            "lmt": "1000000",
        },
    )
    klines = payload.get("data", {}).get("klines", []) or []
    bars: list[KlineBar] = []
    for item in klines:
        fields = item.split(",")
        if len(fields) < 9:
            continue
        bars.append(
            KlineBar(
                trade_date=fields[0],
                close=_safe_float(fields[2]),
                pct_change=_safe_float(fields[8]),
                amount=_safe_float(fields[6]),
            )
        )
    return bars


def _kline_from_akshare(entry: BoardRegistryEntry, start_date: str, end_date: str) -> list[KlineBar]:
    if ak is None:
        return []
    bars: list[KlineBar] = []
    try:
        if entry.board_type == "concept":
            df = ak.stock_board_concept_index_ths(
                symbol=entry.name,
                start_date=start_date,
                end_date=end_date,
            )
        else:
            df = ak.stock_board_industry_index_ths(
                symbol=entry.name,
                start_date=start_date,
                end_date=end_date,
            )
        if not df.empty:
            previous_close: float | None = None
            for _, row in df.iterrows():
                close = _safe_float(row.get("收盘价"))
                pct_change = None
                if close is not None and previous_close not in (None, 0):
                    pct_change = (close / previous_close - 1) * 100
                bars.append(
                    KlineBar(
                        trade_date=str(row.get("日期", "")).strip(),
                        close=close,
                        pct_change=pct_change,
                        amount=_safe_float(row.get("成交额")),
                    )
                )
                previous_close = close
            if bars:
                return bars
    except Exception:
        pass

    if entry.board_type == "concept":
        df = ak.stock_board_concept_hist_em(
            symbol=entry.name,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="",
        )
    else:
        df = ak.stock_board_industry_hist_em(
            symbol=entry.name,
            period="日k",
            start_date=start_date,
            end_date=end_date,
            adjust="",
        )
    if df.empty:
        return []
    for _, row in df.iterrows():
        bars.append(
            KlineBar(
                trade_date=str(row.get("日期", "")).strip(),
                close=_safe_float(row.get("收盘")),
                pct_change=_safe_float(row.get("涨跌幅")),
                amount=_safe_float(row.get("成交额")),
            )
        )
    return bars


def fetch_board_kline_60(entry: BoardRegistryEntry) -> tuple[list[KlineBar], str]:
    start_date = _start_date_for_days(160)
    end_date = _today_ymd()
    if ak is not None:
        try:
            bars = _kline_from_akshare(entry, start_date, end_date)
            if bars:
                return bars[-60:], "akshare"
        except Exception:
            pass
    resolved_code = _resolve_eastmoney_code(entry)
    if not resolved_code:
        return [], "eastmoney"
    return _kline_from_eastmoney(resolved_code, start_date, end_date)[-60:], "eastmoney"


def _benchmark_from_eastmoney(start_date: str, end_date: str) -> list[KlineBar]:
    payload = _request_json(
        INDEX_KLINE_URL,
        {
            "secid": "1.000300",
            "fields1": "f1,f2,f3,f4,f5",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
            "klt": "101",
            "fqt": "0",
            "beg": start_date,
            "end": end_date,
        },
    )
    klines = payload.get("data", {}).get("klines", []) or []
    bars: list[KlineBar] = []
    previous_close: float | None = None
    for item in klines:
        fields = item.split(",")
        if len(fields) < 7:
            continue
        close = _safe_float(fields[2])
        pct_change = None
        if close is not None and previous_close not in (None, 0):
            pct_change = (close / previous_close - 1) * 100
        bars.append(
            KlineBar(
                trade_date=fields[0],
                close=close,
                pct_change=pct_change,
                amount=_safe_float(fields[6]),
            )
        )
        previous_close = close
    return bars


@lru_cache(maxsize=1)
def fetch_benchmark_kline_60() -> tuple[list[KlineBar], str]:
    start_date = _start_date_for_days(160)
    end_date = _today_ymd()
    if ak is not None:
        try:
            df = ak.stock_zh_index_daily(symbol="sh000300")
            if not df.empty:
                bars: list[KlineBar] = []
                previous_close: float | None = None
                for _, row in df.iterrows():
                    close = _safe_float(row.get("close"))
                    pct_change = None
                    if close is not None and previous_close not in (None, 0):
                        pct_change = (close / previous_close - 1) * 100
                    bars.append(
                        KlineBar(
                            trade_date=str(row.get("date", "")).strip(),
                            close=close,
                            pct_change=pct_change,
                            amount=None,
                        )
                    )
                    previous_close = close
                recent = [item for item in bars if item.trade_date.replace("-", "") >= start_date][-60:]
                if recent:
                    return recent, "akshare:sina"
        except Exception:
            pass
        try:
            df = ak.stock_zh_index_daily_em(symbol="sh000300", start_date=start_date, end_date=end_date)
            if not df.empty:
                bars: list[KlineBar] = []
                previous_close: float | None = None
                for _, row in df.iterrows():
                    close = _safe_float(row.get("close"))
                    pct_change = None
                    if close is not None and previous_close not in (None, 0):
                        pct_change = (close / previous_close - 1) * 100
                    bars.append(
                        KlineBar(
                            trade_date=str(row.get("date", "")).strip(),
                            close=close,
                            pct_change=pct_change,
                            amount=_safe_float(row.get("amount")),
                        )
                    )
                    previous_close = close
                return bars[-60:], "akshare"
        except Exception:
            pass
    try:
        return _benchmark_from_eastmoney(start_date, end_date)[-60:], "eastmoney"
    except Exception:
        return [], "unavailable"


def _components_from_eastmoney(code: str) -> list[BoardComponent]:
    rows: list[BoardComponent] = []
    page = 1
    while True:
        payload = _request_json(
            BOARD_COMPONENT_URL,
            {
                "pn": str(page),
                "pz": "200",
                "po": "1",
                "np": "1",
                "ut": BOARD_UT,
                "fltt": "2",
                "invt": "2",
                "fid": "f12",
                "fs": f"b:{code} f:!50",
                "fields": "f12,f14,f3,f6,f8",
            },
        )
        diff = payload.get("data", {}).get("diff", []) or []
        if not diff:
            break
        for item in diff:
            stock_code = str(item.get("f12", "")).strip()
            stock_name = str(item.get("f14", "")).strip()
            if not stock_code or not stock_name:
                continue
            rows.append(
                BoardComponent(
                    stock_code=stock_code,
                    stock_name=stock_name,
                    pct_change=_safe_float(item.get("f3")),
                    amount=_safe_float(item.get("f6")),
                    turnover_rate=_safe_float(item.get("f8")),
                    is_limit_up=False,
                )
            )
        page += 1
    return rows


def _components_from_akshare(entry: BoardRegistryEntry) -> list[BoardComponent]:
    if ak is None:
        return []
    resolved_code = _resolve_eastmoney_code(entry)
    if not resolved_code:
        return []
    if entry.board_type == "concept":
        df = ak.stock_board_concept_cons_em(symbol=resolved_code)
    else:
        df = ak.stock_board_industry_cons_em(symbol=resolved_code)
    if df.empty:
        return []
    rows: list[BoardComponent] = []
    for _, row in df.iterrows():
        stock_code = str(row.get("代码", "")).strip()
        stock_name = str(row.get("名称", "")).strip()
        if not stock_code or not stock_name:
            continue
        rows.append(
            BoardComponent(
                stock_code=stock_code,
                stock_name=stock_name,
                pct_change=_safe_float(row.get("涨跌幅")),
                amount=_safe_float(row.get("成交额")),
                turnover_rate=_safe_float(row.get("换手率")),
                is_limit_up=False,
            )
        )
    return rows


def fetch_board_components(entry: BoardRegistryEntry) -> tuple[list[BoardComponent], str]:
    if ak is not None:
        try:
            rows = _components_from_akshare(entry)
            if rows:
                return rows, "akshare"
        except Exception:
            pass
    resolved_code = _resolve_eastmoney_code(entry)
    if resolved_code:
        try:
            rows = _components_from_eastmoney(resolved_code)
            if rows:
                return rows, "eastmoney"
        except Exception:
            pass
    rows = _components_from_ths(entry)
    return rows, "ths"


def _components_from_ths(entry: BoardRegistryEntry) -> list[BoardComponent]:
    if ak is None:
        return []
    code_map = _load_ths_board_code_map(entry.board_type)
    ths_code = code_map.get(entry.name)
    if not ths_code:
        return []
    base_url = THS_CONCEPT_DETAIL_URL.format(code=ths_code)
    if entry.board_type == "industry":
        base_url = THS_INDUSTRY_DETAIL_URL.format(code=ths_code)

    first_page = _request_text(base_url, encoding="gbk")
    debug_html_dir = os.getenv("SECTOR_STRENGTH_DEBUG_HTML_DIR")
    if debug_html_dir:
        debug_dir = Path(debug_html_dir)
        debug_dir.mkdir(parents=True, exist_ok=True)
        debug_name = f"{entry.board_type}_{ths_code}_{_normalize_name(entry.name)}.html"
        (debug_dir / debug_name).write_text(first_page, encoding="utf-8", errors="ignore")
    page_count = _parse_ths_page_count(first_page)
    seen_codes: set[str] = set()
    components: list[BoardComponent] = []
    for page in range(1, page_count + 1):
        page_url = base_url if page == 1 else f"{base_url}page/{page}/"
        html = first_page if page == 1 else _request_text(page_url, encoding="gbk")
        rows = _parse_ths_table_rows(html)
        if not rows:
            continue
        for row in rows[1:]:
            if len(row) < 11:
                continue
            stock_code = row[1].strip()
            stock_name = row[2].strip()
            if not stock_code or not stock_name or stock_code in seen_codes:
                continue
            seen_codes.add(stock_code)
            components.append(
                BoardComponent(
                    stock_code=stock_code,
                    stock_name=stock_name,
                    pct_change=_parse_pct(row[4]),
                    amount=_parse_cn_amount(row[10]),
                    turnover_rate=_parse_pct(row[7]),
                    is_limit_up=False,
                )
            )
    return components


@lru_cache(maxsize=8)
def _load_fund_flow_table(board_type: str, window_days: int) -> tuple[dict[str, float | None], str]:
    label = FUND_FLOW_PERIOD_LABELS[window_days]
    if ak is not None:
        try:
            if board_type == "concept":
                df = ak.stock_fund_flow_concept(symbol=label)
            else:
                df = ak.stock_fund_flow_industry(symbol=label)
            flows: dict[str, float | None] = {}
            for _, row in df.iterrows():
                name = str(row.get("行业", "")).strip()
                if not name:
                    continue
                flows[name] = _parse_cn_amount_yi_default(row.get("净额"))
            if flows:
                return flows, "akshare"
        except Exception:
            pass
    return {}, "snapshot"


def fetch_board_fund_flow(candidate: BoardCandidate, entry: BoardRegistryEntry, window_days: int) -> BoardFundFlow | None:
    flows, source = _load_fund_flow_table(entry.board_type, window_days)
    if flows:
        value = flows.get(entry.name)
        if value is not None or entry.name in flows:
            return BoardFundFlow(window_days=window_days, main_net_inflow=value, source=source)
    if window_days in candidate.snapshot_flows:
        return BoardFundFlow(
            window_days=window_days,
            main_net_inflow=candidate.snapshot_flows.get(window_days),
            source=candidate.snapshot_flow_sources.get(window_days) or "snapshot",
        )
    return None


def _netflow_hist_from_dataframe(df: pd.DataFrame) -> list[NetFlowBar]:
    if df.empty or "日期" not in df.columns or "主力净流入-净额" not in df.columns:
        return []
    temp_df = df.copy()
    temp_df["日期"] = pd.to_datetime(temp_df["日期"], errors="coerce")
    temp_df = temp_df.dropna(subset=["日期"]).sort_values("日期")
    bars: list[NetFlowBar] = []
    for _, row in temp_df.tail(60).iterrows():
        trade_date = row["日期"].date().isoformat()
        bars.append(
            NetFlowBar(
                trade_date=trade_date,
                main_net_inflow=_safe_float(row.get("主力净流入-净额")),
            )
        )
    return bars


def _fetch_board_fund_flow_hist_from_eastmoney(entry: BoardRegistryEntry) -> list[NetFlowBar]:
    resolved_code = _resolve_eastmoney_code(entry)
    if not resolved_code:
        return []
    payload = _request_json(
        "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
        {
            "lmt": "0",
            "klt": "101",
            "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
            "secid": f"90.{resolved_code}",
        },
    )
    data = payload.get("data") or {}
    klines = data.get("klines") or []
    bars: list[NetFlowBar] = []
    for item in klines[-60:]:
        parts = str(item).split(",")
        if len(parts) < 2:
            continue
        bars.append(
            NetFlowBar(
                trade_date=parts[0],
                main_net_inflow=_safe_float(parts[1]),
            )
        )
    return bars


def fetch_board_fund_flow_hist_60(entry: BoardRegistryEntry) -> tuple[list[NetFlowBar], str]:
    if ak is not None:
        try:
            if entry.board_type == "concept":
                df = ak.stock_concept_fund_flow_hist(symbol=entry.name)
            else:
                df = ak.stock_sector_fund_flow_hist(symbol=entry.name)
            bars = _netflow_hist_from_dataframe(df)
            if bars:
                return bars, "akshare"
        except Exception:
            pass
    bars = _fetch_board_fund_flow_hist_from_eastmoney(entry)
    if bars:
        return bars, "eastmoney"
    return [], "eastmoney"


def _limit_up_codes_from_eastmoney(trade_date: str) -> set[str]:
    payload = _request_json(
        LIMIT_UP_POOL_URL,
        {
            "ut": LIMIT_UP_UT,
            "dpt": "wz.ztzt",
            "Pageindex": "0",
            "pagesize": "10000",
            "sort": "fbt:asc",
            "date": trade_date.replace("-", ""),
        },
    )
    pool = payload.get("data", {}).get("pool", []) or []
    codes: set[str] = set()
    for item in pool:
        code = str(item.get("c", "")).strip()
        if code:
            codes.add(code)
    return codes


@lru_cache(maxsize=4)
def fetch_limit_up_codes(trade_date: str) -> tuple[set[str], str]:
    compact = trade_date.replace("-", "")
    if ak is not None:
        try:
            df = ak.stock_zt_pool_em(date=compact)
            if not df.empty:
                return {str(code).strip() for code in df["代码"].tolist() if str(code).strip()}, "akshare"
        except Exception:
            pass
    return _limit_up_codes_from_eastmoney(trade_date), "eastmoney"


def apply_limit_up_flags(components: list[BoardComponent], limit_up_codes: set[str]) -> list[BoardComponent]:
    flagged: list[BoardComponent] = []
    for item in components:
        flagged.append(
            BoardComponent(
                stock_code=item.stock_code,
                stock_name=item.stock_name,
                pct_change=item.pct_change,
                amount=item.amount,
                turnover_rate=item.turnover_rate,
                is_limit_up=item.stock_code in limit_up_codes,
            )
        )
    return flagged


def fetch_live_candidate_pool(limit: int, board_type: str = "concept") -> list[BoardCandidate]:
    candidates: list[BoardCandidate] = []
    if ak is not None:
        try:
            if board_type == "industry":
                df = ak.stock_fund_flow_industry(symbol="即时")
            else:
                df = ak.stock_fund_flow_concept(symbol="即时")
            for index, (_, row) in enumerate(df.head(limit).iterrows(), start=1):
                name = str(row.get("行业", "")).strip()
                if not name:
                    continue
                candidates.append(
                    BoardCandidate(
                        name=name,
                        code=None,
                        board_type=board_type,
                        rank_no=index,
                        trade_date=datetime.now().date().isoformat(),
                        pool_source=f"akshare:stock_fund_flow_{board_type}:即时",
                        pool_window_days=1,
                    )
                )
        except Exception:
            pass
    if candidates:
        return candidates

    lookup = load_board_registry_lookup()
    fallback: list[BoardCandidate] = []
    registry_entries = [entry for entry in lookup.by_code.values() if entry.board_type == board_type]
    for index, entry in enumerate(registry_entries[:limit], start=1):
        fallback.append(
            BoardCandidate(
                name=entry.name,
                code=entry.code,
                board_type=entry.board_type,
                rank_no=index,
                trade_date=datetime.now().date().isoformat(),
                pool_source="registry-fallback",
                pool_window_days=1,
            )
        )
    return fallback


def fetch_board_market_snapshot(
    candidate: BoardCandidate,
    lookup: BoardRegistryLookup | None,
    benchmark_kline_60: list[KlineBar],
    benchmark_source: str,
) -> BoardMarketSnapshot:
    data_sources: dict[str, str] = {"benchmark": benchmark_source}
    entry, warnings = resolve_candidate(candidate, lookup)
    if entry is None:
        return BoardMarketSnapshot(
            candidate=candidate,
            resolved_name=None,
            resolved_code=None,
            board_type=None,
            trade_date=candidate.trade_date,
            board_kline_60=[],
            benchmark_kline_60=benchmark_kline_60,
            fund_flow_hist_60=[],
            fund_flow_1d=None,
            fund_flow_3d=None,
            fund_flow_5d=None,
            fund_flow_10d=None,
            components=[],
            data_sources=data_sources,
            warnings=warnings,
        )

    board_kline_60: list[KlineBar] = []
    try:
        board_kline_60, kline_source = fetch_board_kline_60(entry)
        data_sources["board_kline"] = kline_source
    except Exception as exc:
        warnings.append(f"{candidate.name}: 获取板块 K 线失败 {exc}")

    trade_date = candidate.trade_date
    if board_kline_60:
        trade_date = board_kline_60[-1].trade_date

    flow_hist_60: list[NetFlowBar] = []
    try:
        flow_hist_60, flow_hist_source = fetch_board_fund_flow_hist_60(entry)
        if flow_hist_60:
            data_sources["flow_hist_60"] = flow_hist_source
    except Exception as exc:
        warnings.append(f"{candidate.name}: 获取日级资金流失败 {exc}")

    flow_1d: BoardFundFlow | None = None
    try:
        flow_1d = fetch_board_fund_flow(candidate, entry, 1)
    except Exception as exc:
        warnings.append(f"{candidate.name}: 获取 1 日资金流失败 {exc}")

    flow_3d: BoardFundFlow | None = None
    try:
        flow_3d = fetch_board_fund_flow(candidate, entry, 3)
    except Exception as exc:
        warnings.append(f"{candidate.name}: 获取 3 日资金流失败 {exc}")

    flow_5d: BoardFundFlow | None = None
    try:
        flow_5d = fetch_board_fund_flow(candidate, entry, 5)
    except Exception as exc:
        warnings.append(f"{candidate.name}: 获取 5 日资金流失败 {exc}")

    flow_10d: BoardFundFlow | None = None
    try:
        flow_10d = fetch_board_fund_flow(candidate, entry, 10)
    except Exception as exc:
        warnings.append(f"{candidate.name}: 获取 10 日资金流失败 {exc}")

    if flow_1d is not None:
        data_sources["flow_1d"] = flow_1d.source
    if flow_3d is not None:
        data_sources["flow_3d"] = flow_3d.source
    if flow_5d is not None:
        data_sources["flow_5d"] = flow_5d.source
    if flow_10d is not None:
        data_sources["flow_10d"] = flow_10d.source

    components: list[BoardComponent] = []
    try:
        components, component_source = fetch_board_components(entry)
        data_sources["components"] = component_source
    except Exception as exc:
        warnings.append(f"{candidate.name}: 获取成分股失败 {exc}")

    if trade_date:
        try:
            limit_up_codes, limit_up_source = fetch_limit_up_codes(trade_date)
            data_sources["limit_up"] = limit_up_source
            components = apply_limit_up_flags(components, limit_up_codes)
        except Exception as exc:
            warnings.append(f"{candidate.name}: 获取涨停池失败 {exc}")
    else:
        warnings.append(f"{candidate.name}: 缺少交易日，无法判定涨停")

    if not board_kline_60:
        warnings.append(f"{candidate.name}: 缺少近 60 日板块 K 线")
    if not components:
        warnings.append(f"{candidate.name}: 缺少成分股数据")
    if not flow_hist_60:
        warnings.append(f"{candidate.name}: 缺少日级主力净流入历史")
    if flow_1d is None:
        warnings.append(f"{candidate.name}: 缺少 1 日主力净流入")
    if flow_3d is None:
        warnings.append(f"{candidate.name}: 缺少 3 日主力净流入")
    if flow_5d is None:
        warnings.append(f"{candidate.name}: 缺少 5 日主力净流入")
    if flow_10d is None:
        warnings.append(f"{candidate.name}: 缺少 10 日主力净流入")

    return BoardMarketSnapshot(
        candidate=candidate,
        resolved_name=entry.name,
        resolved_code=entry.code,
        board_type=entry.board_type,
        trade_date=trade_date,
        board_kline_60=board_kline_60,
        benchmark_kline_60=benchmark_kline_60,
        fund_flow_hist_60=flow_hist_60,
        fund_flow_1d=flow_1d,
        fund_flow_3d=flow_3d,
        fund_flow_5d=flow_5d,
        fund_flow_10d=flow_10d,
        components=components,
        data_sources=data_sources,
        warnings=warnings,
    )
