from __future__ import annotations

import re

from app.models import FundRankRecord


_SHARE_CLASS_PATTERN = re.compile(r"(A类|C类|A|C)$")


def normalize_fund_base_name(name: str) -> str:
    return _SHARE_CLASS_PATTERN.sub("", name.strip()).strip()


def fund_share_class(name: str) -> str | None:
    clean_name = name.strip()
    if clean_name.endswith("C类") or clean_name.endswith("C"):
        return "C"
    if clean_name.endswith("A类") or clean_name.endswith("A"):
        return "A"
    return None


def share_class_priority(name: str) -> int:
    share_class = fund_share_class(name)
    if share_class == "C":
        return 0
    if share_class == "A":
        return 1
    return 2


def pick_preferred_share_class(fund_names: list[str]) -> str:
    if not fund_names:
        return ""
    return min(fund_names, key=lambda item: (share_class_priority(item), item))


def pick_representative_record(records: list[FundRankRecord]) -> FundRankRecord:
    return min(
        records,
        key=lambda item: (
            share_class_priority(item.fund_name),
            item.rank_no or 9999,
            item.fund_name,
            item.fund_code,
        ),
    )


def dedupe_period_records(records: list[FundRankRecord]) -> list[FundRankRecord]:
    grouped: dict[str, list[FundRankRecord]] = {}
    for record in records:
        grouped.setdefault(normalize_fund_base_name(record.fund_name), []).append(record)
    deduped = [pick_representative_record(items) for items in grouped.values()]
    deduped.sort(key=lambda item: (item.rank_no or 9999, item.fund_name, item.fund_code))
    return deduped
