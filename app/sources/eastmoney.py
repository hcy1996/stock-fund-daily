from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.models import SectorFlowRecord


EASTMONEY_FETCH_TARGETS = {
    1: {
        "filename": "1d.json",
        "fid": "f62",
        "fields": "f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f204,f205,f124,f1,f13",
        "mapping": {
            "pct_change": "f3",
            "main_net_inflow": "f62",
            "main_net_inflow_ratio": "f184",
            "super_order_inflow": "f66",
            "super_order_ratio": "f69",
            "large_order_inflow": "f72",
            "large_order_ratio": "f75",
            "medium_order_inflow": "f78",
            "medium_order_ratio": "f81",
            "small_order_inflow": "f84",
            "small_order_ratio": "f87",
            "leader_stock_name": "f204",
            "leader_stock_code": "f205",
        },
    },
    3: {
        "filename": "3d.json",
        "fid": "f267",
        "fields": "f12,f14,f2,f127,f267,f268,f269,f270,f271,f272,f273,f274,f275,f276,f257,f258,f124,f1,f13",
        "mapping": {
            "pct_change": "f127",
            "main_net_inflow": "f267",
            "main_net_inflow_ratio": "f268",
            "super_order_inflow": "f269",
            "super_order_ratio": "f270",
            "large_order_inflow": "f271",
            "large_order_ratio": "f272",
            "medium_order_inflow": "f273",
            "medium_order_ratio": "f274",
            "small_order_inflow": "f275",
            "small_order_ratio": "f276",
            "leader_stock_name": "f257",
            "leader_stock_code": "f258",
        },
    },
    5: {
        "filename": "5d.json",
        "fid": "f164",
        "fields": "f12,f14,f2,f109,f164,f165,f166,f167,f168,f169,f170,f171,f172,f173,f257,f258,f124,f1,f13",
        "mapping": {
            "pct_change": "f109",
            "main_net_inflow": "f164",
            "main_net_inflow_ratio": "f165",
            "super_order_inflow": "f166",
            "super_order_ratio": "f167",
            "large_order_inflow": "f168",
            "large_order_ratio": "f169",
            "medium_order_inflow": "f170",
            "medium_order_ratio": "f171",
            "small_order_inflow": "f172",
            "small_order_ratio": "f173",
            "leader_stock_name": "f257",
            "leader_stock_code": "f258",
        },
    },
    10: {
        "filename": "10d.json",
        "fid": "f174",
        "fields": "f12,f14,f2,f160,f174,f175,f176,f177,f178,f179,f180,f181,f182,f183,f260,f261,f124,f1,f13",
        "mapping": {
            "pct_change": "f160",
            "main_net_inflow": "f174",
            "main_net_inflow_ratio": "f175",
            "super_order_inflow": "f176",
            "super_order_ratio": "f177",
            "large_order_inflow": "f178",
            "large_order_ratio": "f179",
            "medium_order_inflow": "f180",
            "medium_order_ratio": "f181",
            "small_order_inflow": "f182",
            "small_order_ratio": "f183",
            "leader_stock_name": "f260",
            "leader_stock_code": "f261",
        },
    },
}

EASTMONEY_PAGE_SIZE = 500


def build_url(window_days: int) -> str:
    target = EASTMONEY_FETCH_TARGETS[window_days]
    return (
        "https://push2.eastmoney.com/api/qt/clist/get"
        "?np=1&fltt=2&invt=2&ut=8dec03ba335b81bf4ebdf7b29ec27d15"
        f"&pn=1&pz={EASTMONEY_PAGE_SIZE}&po=1&fid={target['fid']}"
        "&fs=m:90+t:3"
        f"&fields={target['fields']}"
    )


def expected_raw_files(raw_dir: Path) -> dict[int, Path]:
    return {
        window_days: raw_dir / "eastmoney" / config["filename"]
        for window_days, config in EASTMONEY_FETCH_TARGETS.items()
    }


def _to_float(value: object) -> float | None:
    if value in ("", "-", None):
        return None
    return float(value)


def _trade_date_from_timestamp(timestamp: object) -> str:
    if timestamp in ("", "-", None):
        return datetime.now(tz=ZoneInfo("Asia/Shanghai")).date().isoformat()
    dt = datetime.fromtimestamp(int(timestamp), tz=ZoneInfo("Asia/Shanghai"))
    return dt.date().isoformat()


def parse_window_file(path: Path, window_days: int) -> list[SectorFlowRecord]:
    if not path.exists():
        return []

    payload = json.loads(path.read_text(encoding="utf-8"))
    diff = payload.get("data", {}).get("diff", [])
    mapping = EASTMONEY_FETCH_TARGETS[window_days]["mapping"]
    records: list[SectorFlowRecord] = []

    for index, item in enumerate(diff, start=1):
        records.append(
            SectorFlowRecord(
                trade_date=_trade_date_from_timestamp(item.get("f124")),
                window_days=window_days,
                source="eastmoney",
                sector_code=str(item.get("f12", "")),
                sector_name=str(item.get("f14", "")),
                latest_index_value=_to_float(item.get("f2")),
                pct_change=_to_float(item.get(mapping["pct_change"])),
                main_net_inflow=_to_float(item.get(mapping["main_net_inflow"])),
                main_net_inflow_ratio=_to_float(item.get(mapping["main_net_inflow_ratio"])),
                super_order_inflow=_to_float(item.get(mapping["super_order_inflow"])),
                super_order_ratio=_to_float(item.get(mapping["super_order_ratio"])),
                large_order_inflow=_to_float(item.get(mapping["large_order_inflow"])),
                large_order_ratio=_to_float(item.get(mapping["large_order_ratio"])),
                medium_order_inflow=_to_float(item.get(mapping["medium_order_inflow"])),
                medium_order_ratio=_to_float(item.get(mapping["medium_order_ratio"])),
                small_order_inflow=_to_float(item.get(mapping["small_order_inflow"])),
                small_order_ratio=_to_float(item.get(mapping["small_order_ratio"])),
                leader_stock_name=str(item.get(mapping["leader_stock_name"], "")) or None,
                leader_stock_code=str(item.get(mapping["leader_stock_code"], "")) or None,
                rank_no=index,
                raw_payload=json.dumps(item, ensure_ascii=False),
            )
        )
    return records
