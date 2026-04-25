from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class SectorFlowRecord:
    trade_date: str
    window_days: int
    source: str
    sector_code: str
    sector_name: str
    latest_index_value: float | None = None
    pct_change: float | None = None
    main_net_inflow: float | None = None
    main_net_inflow_ratio: float | None = None
    super_order_inflow: float | None = None
    super_order_ratio: float | None = None
    large_order_inflow: float | None = None
    large_order_ratio: float | None = None
    medium_order_inflow: float | None = None
    medium_order_ratio: float | None = None
    small_order_inflow: float | None = None
    small_order_ratio: float | None = None
    leader_stock_name: str | None = None
    leader_stock_code: str | None = None
    leader_stock_pct_change: float | None = None
    rank_no: int | None = None
    raw_payload: str | None = None


@dataclass(slots=True)
class MatchedFund:
    fund_code: str
    fund_name: str
    fund_type: str
    note: str | None = None


@dataclass(slots=True)
class SectorComponentRecord:
    sector_name: str
    stock_code: str
    stock_name: str
    latest_price: float | None = None
    pct_change: float | None = None
    rank_no: int | None = None
    raw_payload: str | None = None


@dataclass(slots=True)
class WindowSection:
    title: str
    source_label: str
    records: list[SectorFlowRecord]
    note: str | None = None


JsonDict = dict[str, Any]
