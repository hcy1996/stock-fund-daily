from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
import sqlite3

from app.models import SectorFlowRecord, WindowSection
from app.raw_enricher import load_tonghuashun_top_components
from app.storage import get_recent_daily_rows, get_window_records, latest_trade_date


def _row_to_record(row: sqlite3.Row) -> SectorFlowRecord:
    return SectorFlowRecord(**dict(row))


def _build_rolling_window_20(conn: sqlite3.Connection, latest_date: str, top_n: int) -> tuple[list[SectorFlowRecord], int]:
    rows = get_recent_daily_rows(conn, 20)
    if not rows:
        return [], 0

    dates = sorted({row["trade_date"] for row in rows}, reverse=True)
    latest_map: dict[str, sqlite3.Row] = {}
    totals: dict[str, float] = defaultdict(float)
    names: dict[str, str] = {}

    for row in rows:
        sector_code = row["sector_code"]
        names[sector_code] = row["sector_name"]
        if row["main_net_inflow"] is not None:
            totals[sector_code] += float(row["main_net_inflow"])
        if row["trade_date"] == latest_date and sector_code not in latest_map:
            latest_map[sector_code] = row

    ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)[:top_n]
    records: list[SectorFlowRecord] = []
    for index, (sector_code, total_inflow) in enumerate(ranked, start=1):
        latest_row = latest_map.get(sector_code)
        records.append(
            SectorFlowRecord(
                trade_date=latest_date,
                window_days=20,
                source="local_rollup",
                sector_code=sector_code,
                sector_name=names[sector_code],
                latest_index_value=float(latest_row["latest_index_value"]) if latest_row and latest_row["latest_index_value"] is not None else None,
                pct_change=float(latest_row["pct_change"]) if latest_row and latest_row["pct_change"] is not None else None,
                main_net_inflow=total_inflow,
                leader_stock_name=latest_row["leader_stock_name"] if latest_row else None,
                leader_stock_code=latest_row["leader_stock_code"] if latest_row else None,
                rank_no=index,
            )
        )
    return records, len(dates)


def build_report_payload(
    conn: sqlite3.Connection,
    top_n: int,
    funds_per_sector: int,
    fund_catalog: list[dict],
    match_funds_fn,
    raw_dir: Path,
) -> dict:
    trade_date = latest_trade_date(conn)
    if not trade_date:
        raise RuntimeError("数据库里还没有可用的板块快照，请先抓数并 ingest。")

    windows: dict[int, WindowSection] = {}
    source_labels = {
        "eastmoney": "东方财富",
        "eastmoney_history": "东方财富历史累计",
        "tonghuashun": "同花顺",
        "local_rollup": "本地累计",
    }

    for window_days, title in ((1, "当日 Top10"), (3, "近 3 日 Top10"), (5, "近 5 日 Top10"), (10, "近 10 日 Top10")):
        records = [_row_to_record(row) for row in get_window_records(conn, trade_date, window_days, top_n)]
        source = records[0].source if records else "eastmoney"
        windows[window_days] = WindowSection(
            title=title,
            source_label=source_labels.get(source, source),
            records=records,
        )

    top20 = [_row_to_record(row) for row in get_window_records(conn, trade_date, 20, top_n)]
    if top20:
        windows[20] = WindowSection(
            title="近 20 日 Top10",
            source_label=source_labels.get(top20[0].source, top20[0].source),
            records=top20,
        )
    else:
        rollup_records, lookback_days = _build_rolling_window_20(conn, trade_date, top_n)
        note = f"当前为本地累计 {lookback_days}/20 个交易日口径" if lookback_days < 20 else None
        windows[20] = WindowSection(
            title="近 20 日 Top10",
            source_label="本地累计",
            records=rollup_records,
            note=note,
        )

    appearances: Counter[str] = Counter()
    sector_windows: dict[str, set[int]] = defaultdict(set)
    sector_records: dict[str, SectorFlowRecord] = {}

    for window_days, section in windows.items():
        for record in section.records:
            appearances[record.sector_name] += 1
            sector_windows[record.sector_name].add(window_days)
            sector_records.setdefault(record.sector_name, record)

    persistent_hot = [
        name
        for name, seen in sector_windows.items()
        if {1, 3, 5, 10}.issubset(seen) or len(seen.intersection({1, 3, 5, 10, 20})) >= 3
    ]
    persistent_hot = persistent_hot[:8]

    emerging = [
        name
        for name, seen in sector_windows.items()
        if 1 in seen and 3 in seen and 10 not in seen and 20 not in seen
    ][:8]

    weakening = [
        name
        for name, seen in sector_windows.items()
        if (10 in seen or 20 in seen) and 1 not in seen
    ][:8]

    focus_names = list(
        dict.fromkeys(
            persistent_hot
            + emerging
            + [record.sector_name for record in windows[1].records[:top_n]]
        )
    )
    related_funds = {
        sector_name: match_funds_fn(sector_name, fund_catalog, funds_per_sector)
        for sector_name in focus_names
    }
    top_components, unmatched_component_sectors = load_tonghuashun_top_components(
        raw_dir / "tonghuashun" / "components_top10.json"
    )

    return {
        "trade_date": trade_date,
        "windows": windows,
        "persistent_hot": persistent_hot,
        "emerging": emerging,
        "weakening": weakening,
        "leaders": [record.sector_name for record in windows[1].records[:top_n]],
        "related_funds": related_funds,
        "focus_records": {name: sector_records[name] for name in focus_names if name in sector_records},
        "top_components": {
            record.sector_name: top_components.get(record.sector_name, [])
            for record in windows[1].records[:top_n]
        },
        "component_unmatched_sectors": unmatched_component_sectors,
        "debug_windows": {window: [asdict(record) for record in section.records] for window, section in windows.items()},
    }
