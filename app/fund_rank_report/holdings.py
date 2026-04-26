from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess

from app.fund_rank_report.analyzer import PERIOD_ORDER
from app.models import FundHoldingRecord, FundRankRecord
from app.sources.fund_holdings import load_or_fetch_holdings_records


@dataclass(slots=True)
class ReportFundHoldings:
    fund_code: str
    fund_name: str
    report_date: str
    holdings: list[FundHoldingRecord]


def collect_rank_funds(
    period_records: dict[str, list[FundRankRecord]],
) -> list[FundRankRecord]:
    unique: dict[str, FundRankRecord] = {}
    for period in PERIOD_ORDER:
        for record in period_records.get(period, []):
            unique.setdefault(record.fund_code, record)
    return list(unique.values())


def load_rank_fund_holdings(
    period_records: dict[str, list[FundRankRecord]],
    raw_dir: Path,
    user_agent: str,
    *,
    refresh: bool = False,
) -> tuple[list[ReportFundHoldings], list[str]]:
    bundles: list[ReportFundHoldings] = []
    fetch_failed: list[str] = []

    for fund in collect_rank_funds(period_records):
        try:
            records, warning = load_or_fetch_holdings_records(
                raw_dir,
                fund.fund_code,
                user_agent,
                refresh=refresh,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            fetch_failed.append(f"{fund.fund_name}({fund.fund_code})[{exc}]")
            continue
        if not records:
            continue
        bundles.append(
            ReportFundHoldings(
                fund_code=fund.fund_code,
                fund_name=records[0].fund_name or fund.fund_name,
                report_date=records[0].report_date,
                holdings=records,
            )
        )

    warnings: list[str] = []
    if fetch_failed:
        warnings.append(
            "基金持仓补抓失败共 "
            f"{len(fetch_failed)} 只：{ '、'.join(fetch_failed[:5]) }"
            + (" 等" if len(fetch_failed) > 5 else "")
        )
    return bundles, warnings


def serialize_holding_record(record: FundHoldingRecord) -> dict:
    return {
        "fund_code": record.fund_code,
        "fund_name": record.fund_name,
        "report_date": record.report_date,
        "stock_code": record.stock_code,
        "stock_name": record.stock_name,
        "net_value_ratio": record.net_value_ratio,
        "shares_wan": record.shares_wan,
        "market_value_wan": record.market_value_wan,
        "rank_no": record.rank_no,
    }


def serialize_holding_bundle(bundle: ReportFundHoldings) -> dict:
    return {
        "fund_code": bundle.fund_code,
        "fund_name": bundle.fund_name,
        "report_date": bundle.report_date,
        "holdings": [serialize_holding_record(record) for record in bundle.holdings],
    }
