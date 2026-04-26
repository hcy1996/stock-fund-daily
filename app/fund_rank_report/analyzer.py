from __future__ import annotations

from app.models import FundRankRecord
from app.fund_rank_report.dedupe import (
    dedupe_period_records,
    fund_share_class,
    normalize_fund_base_name,
    pick_representative_record,
)


PERIOD_ORDER = ["day", "week", "month", "quarter", "half_year", "year"]
PERIOD_LABELS = {
    "day": "今日",
    "week": "近一周",
    "month": "近一月",
    "quarter": "近三月",
    "half_year": "近六月",
    "year": "近一年",
}
REPEAT_LEVELS = {
    1: ("普通", "repeat-normal"),
    2: ("活跃", "repeat-active"),
    3: ("走强", "repeat-rising"),
    4: ("强势", "repeat-strong"),
    5: ("热门", "repeat-hot"),
    6: ("核心", "repeat-core"),
}
STRONG_REPEAT_LEVEL = REPEAT_LEVELS[6]
HIGH_REPEAT_MIN_APPEARANCE = 3


def build_repeat_level(appearance_count: int) -> dict[str, str | int]:
    label, css_class = REPEAT_LEVELS.get(appearance_count, STRONG_REPEAT_LEVEL)
    if appearance_count <= 0:
        label, css_class = REPEAT_LEVELS[1]
    return {
        "count": max(appearance_count, 1),
        "label": label,
        "css_class": css_class,
    }


def fund_return_by_period(record: FundRankRecord, period: str) -> float | None:
    if period == "day":
        return record.daily_growth_pct
    if period == "week":
        return record.weekly_growth_pct
    if period == "month":
        return record.monthly_growth_pct
    if period == "quarter":
        return record.quarter_growth_pct
    if period == "half_year":
        return record.half_year_growth_pct
    if period == "year":
        return record.year_growth_pct
    return None


def serialize_rank_record(record: FundRankRecord, period: str) -> dict:
    return {
        "fund_code": record.fund_code,
        "fund_name": record.fund_name,
        "share_class": fund_share_class(record.fund_name) or "-",
        "rank_no": record.rank_no,
        "return_pct": fund_return_by_period(record, period),
    }


def build_report_payload(
    raw_period_records: dict[str, list[FundRankRecord]],
    *,
    requested_top_n: int,
    warnings: list[str] | None = None,
) -> dict:
    warnings = list(warnings or [])
    deduped_period_records: dict[str, list[FundRankRecord]] = {}
    grouped_funds: dict[str, dict[str, object]] = {}

    for period in PERIOD_ORDER:
        records = dedupe_period_records(raw_period_records.get(period, []))
        deduped_period_records[period] = records
        if not records:
            warnings.append(f"{PERIOD_LABELS[period]}排行榜抓取为空。")
        for record in records:
            key = normalize_fund_base_name(record.fund_name)
            bucket = grouped_funds.setdefault(
                key,
                {
                    "records": [],
                    "records_by_period": {},
                },
            )
            bucket["records"].append(record)
            bucket["records_by_period"][period] = record

    occurrence_counts = {
        key: len(bucket["records_by_period"])
        for key, bucket in grouped_funds.items()
    }

    snapshot_dates = [
        record.snapshot_date
        for records in deduped_period_records.values()
        for record in records
        if record.snapshot_date
    ]
    snapshot_date = max(snapshot_dates) if snapshot_dates else ""
    trade_date = snapshot_date or "unknown"

    period_sections: list[dict] = []
    for period in PERIOD_ORDER:
        records = deduped_period_records[period]
        serialized_records = []
        for record in records:
            key = normalize_fund_base_name(record.fund_name)
            appearance_count = occurrence_counts.get(key, 1)
            repeat_level = build_repeat_level(appearance_count)
            item = serialize_rank_record(record, period)
            item.update(
                {
                    "appearance_count": appearance_count,
                    "appearance_rate": round(appearance_count / len(PERIOD_ORDER), 4),
                    "repeat_label": repeat_level["label"],
                    "repeat_class": repeat_level["css_class"],
                }
            )
            serialized_records.append(item)
        period_sections.append(
            {
                "period": period,
                "label": PERIOD_LABELS[period],
                "record_count": len(serialized_records),
                "records": serialized_records,
            }
        )

    repeat_rows: list[dict] = []
    for key, bucket in grouped_funds.items():
        records = list(bucket["records"])
        records_by_period = dict(bucket["records_by_period"])
        representative = pick_representative_record(records)
        appearance_count = len(records_by_period)
        appearance_rate = round(appearance_count / len(PERIOD_ORDER), 4)
        repeat_level = build_repeat_level(appearance_count)
        avg_return_values = [
            value
            for value in (
                fund_return_by_period(record, period)
                for period, record in records_by_period.items()
            )
            if value is not None
        ]
        average_return_pct = (
            round(sum(avg_return_values) / len(avg_return_values), 4)
            if avg_return_values
            else None
        )
        stage_details = {
            period: {
                "label": PERIOD_LABELS[period],
                "rank_no": record.rank_no,
                "return_pct": fund_return_by_period(record, period),
                "fund_code": record.fund_code,
                "fund_name": record.fund_name,
            }
            for period, record in records_by_period.items()
        }
        repeat_rows.append(
            {
                "fund_code": representative.fund_code,
                "fund_name": representative.fund_name,
                "base_name": key,
                "share_class": fund_share_class(representative.fund_name) or "-",
                "appearance_periods": [PERIOD_LABELS[period] for period in PERIOD_ORDER if period in stage_details],
                "appearance_count": appearance_count,
                "appearance_rate": appearance_rate,
                "repeat_label": repeat_level["label"],
                "repeat_class": repeat_level["css_class"],
                "best_rank": min(record.rank_no or 9999 for record in records),
                "average_return_pct": average_return_pct,
                "stages": stage_details,
            }
        )

    repeat_rows = [
        item for item in repeat_rows if item["appearance_count"] >= HIGH_REPEAT_MIN_APPEARANCE
    ]
    repeat_rows.sort(
        key=lambda item: (
            -item["appearance_count"],
            item["best_rank"],
            -(item["average_return_pct"] if item["average_return_pct"] is not None else -9999),
            item["fund_name"],
            item["fund_code"],
        )
    )

    return {
        "trade_date": trade_date,
        "snapshot_date": snapshot_date,
        "requested_top_n": requested_top_n,
        "warnings": list(dict.fromkeys(warnings)),
        "overview": {
            "period_count": len(PERIOD_ORDER),
            "raw_record_count": sum(len(items) for items in raw_period_records.values()),
            "deduped_record_count": sum(len(items) for items in deduped_period_records.values()),
            "unique_fund_count": len(grouped_funds),
            "high_repeat_count": len(repeat_rows),
            "strong_repeat_count": sum(1 for row in repeat_rows if row["appearance_count"] >= 5),
        },
        "period_sections": period_sections,
        "repeat_rows": repeat_rows,
    }


def build_summary_text(payload: dict) -> str:
    top_repeat = payload["repeat_rows"][:10]
    lines = [
        f"基金排行榜报告 | {payload['trade_date']}",
        f"阶段数: {payload['overview']['period_count']}",
        f"唯一基金数: {payload['overview']['unique_fund_count']}",
        f"重复基金数: {payload['overview']['high_repeat_count']}",
        "高频基金 Top10:",
    ]
    if not top_repeat:
        lines.append(f"- 暂无出现次数大于等于 {HIGH_REPEAT_MIN_APPEARANCE} 次的基金")
        if payload["warnings"]:
            lines.append("告警:")
            for warning in payload["warnings"]:
                lines.append(f"- {warning}")
        return "\n".join(lines)
    for item in top_repeat:
        lines.append(
            f"- {item['fund_name']}({item['fund_code']}) | "
            f"{item['appearance_count']} 次 | "
            f"{'、'.join(item['appearance_periods'])}"
        )
    if payload["warnings"]:
        lines.append("告警:")
        for warning in payload["warnings"]:
            lines.append(f"- {warning}")
    return "\n".join(lines)


def build_sector_frequency_rows(
    classification_map: dict[str, dict],
) -> list[dict]:
    buckets: dict[str, dict] = {}
    for fund_code, classification in classification_map.items():
        primary_sector = str(classification.get("primary_sector", "")).strip()
        sub_sector = str(classification.get("sub_sector", "")).strip()
        fund_name = str(classification.get("fund_name", "")).strip() or fund_code
        if not primary_sector and not sub_sector:
            continue
        key = sub_sector or primary_sector or "-"
        bucket = buckets.setdefault(
            key,
            {
                "primary_sector": primary_sector or "-",
                "sub_sector": key,
                "fund_count": 0,
                "fund_names": [],
                "fund_codes": [],
                "primary_sector_counts": {},
            },
        )
        bucket["fund_count"] += 1
        bucket["fund_names"].append(fund_name)
        bucket["fund_codes"].append(fund_code)
        sector_key = primary_sector or "-"
        bucket["primary_sector_counts"][sector_key] = (
            bucket["primary_sector_counts"].get(sector_key, 0) + 1
        )

    rows = sorted(
        buckets.values(),
        key=lambda item: (
            -item["fund_count"],
            item["sub_sector"],
        ),
    )
    for index, item in enumerate(rows, start=1):
        primary_sector_counts = item.pop("primary_sector_counts", {})
        item["primary_sector"] = sorted(
            primary_sector_counts.items(),
            key=lambda pair: (-pair[1], pair[0]),
        )[0][0]
        item["rank_no"] = index
    return rows


def build_json_payload(payload: dict) -> dict:
    return payload
