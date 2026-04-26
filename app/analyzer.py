from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import re
import sqlite3

from app.models import FundHoldingRecord, FundRankRecord, FundRankSection, SectorFlowRecord, WindowSection
from app.raw_enricher import load_tonghuashun_top_components
from app.sources.fund_rank import EASTMONEY_FUND_RANK_PERIODS
from app.storage import (
    get_fund_rank_records,
    get_latest_fund_holdings,
    get_window_records,
    latest_fund_rank_snapshot_date,
    latest_trade_date,
)


WINDOW_DAYS = (1, 3, 5, 10, 20)
WINDOW_TITLES = {
    1: "当日 Top10",
    3: "近 3 日 Top10",
    5: "近 5 日 Top10",
    10: "近 10 日 Top10",
    20: "近 20 日 Top10",
}
WINDOW_WARNING_LABELS = {
    1: "当日",
    3: "近 3 日",
    5: "近 5 日",
    10: "近 10 日",
    20: "近 20 日",
}
SOURCE_LABELS = {
    "eastmoney": "东方财富",
    "tonghuashun": "同花顺",
}
SECTION_SOURCE_LABELS = {
    "eastmoney": {
        1: "东方财富",
        3: "东方财富",
        5: "东方财富",
        10: "东方财富",
        20: "东方财富历史累计",
    },
    "tonghuashun": {
        1: "同花顺",
        3: "同花顺",
        5: "同花顺",
        10: "同花顺",
        20: "同花顺",
    },
}
SOURCE_RECORD_NAMES = {
    "eastmoney": {
        1: "eastmoney",
        3: "eastmoney",
        5: "eastmoney",
        10: "eastmoney",
        20: "eastmoney_history",
    },
    "tonghuashun": {
        1: "tonghuashun",
        3: "tonghuashun",
        5: "tonghuashun",
        10: "tonghuashun",
        20: "tonghuashun",
    },
}
_SECTOR_NAME_NORMALIZE_PATTERN = re.compile(r"[\s()（）\-_/.]+")
_GENERIC_SUFFIXES = (
    "概念股",
    "概念",
    "板块",
    "行业",
    "指数",
    "产业链",
    "产业",
    "Ⅱ",
    "I",
)
SMART_GROUP_RULES = (
    ("电池新能源链", ("电池", "锂", "盐湖提锂", "铜箔", "回收", "PVDF", "能源金属")),
    ("化工材料链", ("化肥", "化工", "磷", "氟", "草甘膦", "钛白粉")),
    ("半导体光刻链", ("半导体", "中芯", "海思", "芯片", "光刻", "封装", "CPO")),
)


def _row_to_record(row: sqlite3.Row) -> SectorFlowRecord:
    return SectorFlowRecord(**dict(row))


def _row_to_fund_rank_record(row: sqlite3.Row) -> FundRankRecord:
    return FundRankRecord(**dict(row))


def _row_to_fund_holding_record(row: sqlite3.Row) -> FundHoldingRecord:
    return FundHoldingRecord(**dict(row))


def _normalize_sector_name(name: str) -> str:
    return _SECTOR_NAME_NORMALIZE_PATTERN.sub("", name)


def _sector_match_key(name: str) -> str:
    normalized = _normalize_sector_name(name)
    for suffix in _GENERIC_SUFFIXES:
        if normalized.endswith(suffix) and len(normalized) > len(suffix) + 1:
            normalized = normalized[: -len(suffix)]
    return normalized


def _longest_common_substring_length(a: str, b: str) -> int:
    if not a or not b:
        return 0
    dp = [0] * (len(b) + 1)
    longest = 0
    for char_a in a:
        prev = 0
        for index, char_b in enumerate(b, start=1):
            current = dp[index]
            if char_a == char_b:
                dp[index] = prev + 1
                longest = max(longest, dp[index])
            else:
                dp[index] = 0
            prev = current
    return longest


def _sector_similarity(name_a: str, name_b: str) -> float:
    key_a = _sector_match_key(name_a)
    key_b = _sector_match_key(name_b)
    if not key_a or not key_b:
        return 0.0
    if key_a == key_b:
        return 1.0
    if key_a in key_b or key_b in key_a:
        shorter_length = min(len(key_a), len(key_b))
        if shorter_length >= 2:
            return 0.92

    common_substring_length = _longest_common_substring_length(key_a, key_b)
    common_chars = len(set(key_a) & set(key_b))
    overlap_ratio = common_chars / max(1, min(len(set(key_a)), len(set(key_b))))
    substring_ratio = common_substring_length / max(1, min(len(key_a), len(key_b)))
    return max(overlap_ratio, substring_ratio)


def _format_pair_name(name_a: str, name_b: str) -> str:
    if name_a == name_b or _sector_match_key(name_a) == _sector_match_key(name_b):
        return name_a
    return f"{name_a} ≈ {name_b}"


def _match_group_label(sector_name: str) -> str | None:
    key = _sector_match_key(sector_name)
    best_label: str | None = None
    best_score = 0
    for label, keywords in SMART_GROUP_RULES:
        for keyword in keywords:
            if keyword in key:
                score = len(keyword)
                if score > best_score:
                    best_score = score
                    best_label = label
    return best_label


def _build_similar_pairs(
    eastmoney_records: list[SectorFlowRecord],
    tonghuashun_records: list[SectorFlowRecord],
) -> tuple[list[tuple[SectorFlowRecord, SectorFlowRecord, float]], list[SectorFlowRecord], list[SectorFlowRecord]]:
    matched_pairs: list[tuple[SectorFlowRecord, SectorFlowRecord, float]] = []
    used_tonghuashun_indices: set[int] = set()

    for eastmoney_record in eastmoney_records:
        best_index = -1
        best_score = 0.0
        for index, tonghuashun_record in enumerate(tonghuashun_records):
            if index in used_tonghuashun_indices:
                continue
            score = _sector_similarity(eastmoney_record.sector_name, tonghuashun_record.sector_name)
            if score > best_score:
                best_score = score
                best_index = index
        if best_index == -1 or best_score < 0.6:
            continue
        used_tonghuashun_indices.add(best_index)
        matched_pairs.append((eastmoney_record, tonghuashun_records[best_index], best_score))

    eastmoney_unmatched = [
        record
        for record in eastmoney_records
        if all(record is not pair[0] for pair in matched_pairs)
    ]
    tonghuashun_unmatched = [
        record
        for index, record in enumerate(tonghuashun_records)
        if index not in used_tonghuashun_indices
    ]
    return matched_pairs, eastmoney_unmatched, tonghuashun_unmatched


def _build_window_section(
    conn: sqlite3.Connection,
    trade_date: str,
    source_key: str,
    window_days: int,
    top_n: int,
) -> WindowSection:
    source_name = SOURCE_RECORD_NAMES[source_key][window_days]
    records = [
        _row_to_record(row)
        for row in get_window_records(
            conn,
            trade_date,
            window_days,
            source=source_name,
            limit=top_n,
        )
    ]
    note = None
    if not records:
        note = f"{SOURCE_LABELS[source_key]}当前窗口抓取失败或无可用数据，仅供另一数据源参考。"

    return WindowSection(
        title=WINDOW_TITLES[window_days],
        source_label=SECTION_SOURCE_LABELS[source_key][window_days],
        records=records,
        note=note,
    )


def _build_fund_rank_sections(
    conn: sqlite3.Connection,
    top_n: int,
) -> tuple[str | None, dict[str, FundRankSection], list[str]]:
    snapshot_date = latest_fund_rank_snapshot_date(conn)
    sections: dict[str, FundRankSection] = {}
    warnings: list[str] = []

    for period, config in EASTMONEY_FUND_RANK_PERIODS.items():
        records: list[FundRankRecord] = []
        if snapshot_date:
            records = [
                _row_to_fund_rank_record(row)
                for row in get_fund_rank_records(conn, snapshot_date, period, limit=top_n)
            ]
        note = None
        if not records:
            note = f"天天基金{config['title']}缺失，本次报告不展示该窗口。"
            warnings.append(f"基金排行榜缺失：{config['title']}。")
        sections[period] = FundRankSection(
            title=str(config["title"]),
            ranking_period=period,
            value_label=str(config["value_label"]),
            records=records,
            note=note,
        )

    return snapshot_date, sections, warnings


def _build_comparison(
    eastmoney_section: WindowSection,
    tonghuashun_section: WindowSection,
) -> dict:
    similar_pairs, eastmoney_unmatched, tonghuashun_unmatched = _build_similar_pairs(
        eastmoney_section.records,
        tonghuashun_section.records,
    )
    similar_pairs.sort(
        key=lambda item: (
            -item[2],
            min(item[0].rank_no or 999, item[1].rank_no or 999),
            item[0].sector_name,
        )
    )
    return {
        "similar": [
            _format_pair_name(eastmoney_record.sector_name, tonghuashun_record.sector_name)
            for eastmoney_record, tonghuashun_record, _ in similar_pairs[:8]
        ],
        "eastmoney_focus": [record.sector_name for record in eastmoney_unmatched[:8]],
        "tonghuashun_focus": [record.sector_name for record in tonghuashun_unmatched[:8]],
        "similar_count": len(similar_pairs),
    }


def _build_warning_messages(source_windows: dict[str, dict[int, WindowSection]]) -> list[str]:
    warnings: list[str] = []
    for source_key, label in SOURCE_LABELS.items():
        missing = [
            WINDOW_WARNING_LABELS[window_days]
            for window_days in WINDOW_DAYS
            if not (source_key == "eastmoney" and window_days == 20)
            if not source_windows[source_key][window_days].records
        ]
        if missing:
            warnings.append(f"{label}缺失窗口：{'、'.join(missing)}。本次邮件按现有可用数据展示。")
    return warnings


def _build_signal_summary(
    source_windows: dict[str, dict[int, WindowSection]],
    comparisons: dict[int, dict],
) -> dict:
    sector_heat: dict[str, int] = {}
    sector_occurrences: dict[str, list[str]] = {}
    sector_window_days: dict[str, set[int]] = {}
    sector_best_rank: dict[str, int] = {}

    for source_key, windows in source_windows.items():
        source_label = SOURCE_LABELS[source_key]
        for window_days, section in windows.items():
            window_label = WINDOW_WARNING_LABELS[window_days]
            for record in section.records:
                sector_name = record.sector_name
                sector_heat[sector_name] = sector_heat.get(sector_name, 0) + 1
                sector_occurrences.setdefault(sector_name, []).append(f"{source_label} {window_label}")
                sector_window_days.setdefault(sector_name, set()).add(window_days)
                rank_no = record.rank_no or 999
                sector_best_rank[sector_name] = min(sector_best_rank.get(sector_name, 999), rank_no)

    repeated_candidates = sorted(
        [name for name, heat in sector_heat.items() if heat >= 3],
        key=lambda name: (-sector_heat[name], -len(sector_window_days[name]), sector_best_rank[name], name),
    )
    repeated_hot = repeated_candidates[:12]
    persistent_candidates = sorted(
        [name for name, window_days in sector_window_days.items() if len(window_days) >= 3],
        key=lambda name: (-len(sector_window_days[name]), -sector_heat[name], sector_best_rank[name], name),
    )
    persistent_hot = persistent_candidates[:12]

    def build_grouped_focus(
        candidates: list[str],
        *,
        exclude: set[str] | None = None,
    ) -> tuple[list[str], dict[str, int], dict[str, list[str]]]:
        exclude = exclude or set()
        grouped: dict[str, dict] = {}
        singles: list[str] = []
        for sector_name in candidates:
            if sector_name in exclude:
                continue
            label = _match_group_label(sector_name)
            if not label:
                singles.append(sector_name)
                continue
            bucket = grouped.setdefault(
                label,
                {
                    "members": [],
                    "heat": 0,
                    "window_days": set(),
                    "best_rank": 999,
                },
            )
            bucket["members"].append(sector_name)
            bucket["heat"] += sector_heat[sector_name]
            bucket["window_days"].update(sector_window_days[sector_name])
            bucket["best_rank"] = min(bucket["best_rank"], sector_best_rank[sector_name])

        labels: list[str] = []
        label_heat: dict[str, int] = {}
        label_occurrences: dict[str, list[str]] = {}

        grouped_items = sorted(
            grouped.items(),
            key=lambda item: (-item[1]["heat"], -len(item[1]["window_days"]), item[1]["best_rank"], item[0]),
        )
        for label, bucket in grouped_items:
            members = list(dict.fromkeys(bucket["members"]))
            if len(members) < 2:
                singles.extend(members)
                continue
            labels.append(label)
            label_heat[label] = int(bucket["heat"])
            label_occurrences[label] = [f"归类：{'、'.join(members[:6])}"]

        singles = list(dict.fromkeys(singles))
        singles.sort(key=lambda name: (-sector_heat[name], -len(sector_window_days[name]), sector_best_rank[name], name))
        for name in singles:
            labels.append(name)
            label_heat[name] = sector_heat[name]
            label_occurrences[name] = [
                f"出现窗口：{len(sector_window_days[name])} 个",
                *sector_occurrences[name][:4],
            ]
        return labels[:10], label_heat, label_occurrences

    repeated_focus, repeated_focus_heat, repeated_focus_occurrences = build_grouped_focus(repeated_candidates)
    persistent_focus, persistent_focus_heat, persistent_focus_occurrences = build_grouped_focus(
        persistent_candidates,
        exclude=set(repeated_focus),
    )
    if not persistent_focus:
        persistent_focus, persistent_focus_heat, persistent_focus_occurrences = build_grouped_focus(persistent_candidates)
    consensus_hot = comparisons[1]["similar"][:8]
    divergence_hot = list(
        dict.fromkeys(
            comparisons[1]["eastmoney_focus"][:5]
            + comparisons[1]["tonghuashun_focus"][:5]
        )
    )[:8]

    conclusions: list[str] = []
    if consensus_hot:
        conclusions.append(f"当日双源接近热点：{'、'.join(consensus_hot[:5])}")
    if persistent_hot:
        conclusions.append(f"跨周期重复最强：{'、'.join(persistent_hot[:5])}")
    if divergence_hot:
        conclusions.append(f"当日分歧集中在：{'、'.join(divergence_hot[:5])}")

    return {
        "sector_heat": sector_heat,
        "sector_occurrences": sector_occurrences,
        "sector_window_days": {name: sorted(days) for name, days in sector_window_days.items()},
        "repeated_hot": repeated_hot,
        "repeated_focus": repeated_focus,
        "repeated_focus_heat": repeated_focus_heat,
        "repeated_focus_occurrences": repeated_focus_occurrences,
        "persistent_hot": persistent_hot,
        "persistent_focus": persistent_focus,
        "persistent_focus_heat": persistent_focus_heat,
        "persistent_focus_occurrences": persistent_focus_occurrences,
        "consensus_hot": consensus_hot,
        "divergence_hot": divergence_hot,
        "conclusions": conclusions,
    }


def _pick_primary_source(source_windows: dict[str, dict[int, WindowSection]]) -> str:
    if source_windows["eastmoney"][1].records:
        return "eastmoney"
    if source_windows["tonghuashun"][1].records:
        return "tonghuashun"
    if any(source_windows["eastmoney"][window_days].records for window_days in WINDOW_DAYS):
        return "eastmoney"
    return "tonghuashun"


def build_report_payload(
    conn: sqlite3.Connection,
    top_n: int,
    stats_top_n: int,
    funds_per_sector: int,
    fund_catalog: list[dict],
    match_funds_fn,
    raw_dir: Path,
) -> dict:
    trade_date = latest_trade_date(conn)
    if not trade_date:
        raise RuntimeError("数据库里还没有可用的板块快照，请先抓数并 ingest。")

    source_windows = {
        source_key: {
            window_days: _build_window_section(conn, trade_date, source_key, window_days, top_n)
            for window_days in WINDOW_DAYS
        }
        for source_key in SOURCE_LABELS
    }
    source_windows_stats = {
        source_key: {
            window_days: _build_window_section(conn, trade_date, source_key, window_days, stats_top_n)
            for window_days in WINDOW_DAYS
        }
        for source_key in SOURCE_LABELS
    }
    comparisons = {
        window_days: _build_comparison(
            source_windows_stats["eastmoney"][window_days],
            source_windows_stats["tonghuashun"][window_days],
        )
        for window_days in WINDOW_DAYS
    }
    warnings = _build_warning_messages(source_windows)
    fund_rank_snapshot_date, fund_rank_sections, fund_rank_warnings = _build_fund_rank_sections(conn, top_n)
    warnings.extend(fund_rank_warnings)
    fund_codes = list(
        dict.fromkeys(
            record.fund_code
            for section in fund_rank_sections.values()
            for record in section.records
        )
    )
    fund_holdings = {
        fund_code: [_row_to_fund_holding_record(row) for row in rows]
        for fund_code, rows in get_latest_fund_holdings(conn, fund_codes, limit_per_fund=10).items()
    }
    signal_summary = _build_signal_summary(source_windows_stats, comparisons)
    primary_source = _pick_primary_source(source_windows)

    focus_names = list(
        dict.fromkeys(
            [record.sector_name for record in source_windows["eastmoney"][1].records]
            + [record.sector_name for record in source_windows["tonghuashun"][1].records]
            + comparisons[1]["similar"]
        )
    )
    if not focus_names:
        focus_names = list(
            dict.fromkeys(
                [record.sector_name for record in source_windows[primary_source][3].records]
                + [record.sector_name for record in source_windows[primary_source][5].records]
            )
        )
    focus_names = focus_names[: top_n * 2]

    focus_records: dict[str, SectorFlowRecord] = {}
    for source_key in ("eastmoney", "tonghuashun"):
        for record in source_windows[source_key][1].records:
            focus_records.setdefault(record.sector_name, record)

    related_funds = {
        sector_name: match_funds_fn(sector_name, fund_catalog, funds_per_sector)
        for sector_name in focus_names
    }
    top_components, unmatched_component_sectors = load_tonghuashun_top_components(
        raw_dir / "tonghuashun" / "components_top10.json"
    )

    return {
        "trade_date": trade_date,
        "source_windows": source_windows,
        "comparisons": comparisons,
        "warnings": warnings,
        "fund_rank_snapshot_date": fund_rank_snapshot_date,
        "fund_rank_sections": fund_rank_sections,
        "fund_holdings": fund_holdings,
        "primary_source": primary_source,
        "leaders_by_source": {
            source_key: [record.sector_name for record in source_windows[source_key][1].records]
            for source_key in SOURCE_LABELS
        },
        "status_counts": {
            source_key: sum(
                1
                for window_days in WINDOW_DAYS
                if source_windows[source_key][window_days].records
            )
            for source_key in SOURCE_LABELS
        },
        "sector_heat": signal_summary["sector_heat"],
        "sector_occurrences": signal_summary["sector_occurrences"],
        "sector_window_days": signal_summary["sector_window_days"],
        "repeated_hot": signal_summary["repeated_hot"],
        "repeated_focus": signal_summary["repeated_focus"],
        "repeated_focus_heat": signal_summary["repeated_focus_heat"],
        "repeated_focus_occurrences": signal_summary["repeated_focus_occurrences"],
        "persistent_hot": signal_summary["persistent_hot"],
        "persistent_focus": signal_summary["persistent_focus"],
        "persistent_focus_heat": signal_summary["persistent_focus_heat"],
        "persistent_focus_occurrences": signal_summary["persistent_focus_occurrences"],
        "consensus_hot": signal_summary["consensus_hot"],
        "divergence_hot": signal_summary["divergence_hot"],
        "conclusions": signal_summary["conclusions"],
        "focus_names": focus_names,
        "related_funds": related_funds,
        "focus_records": focus_records,
        "top_components": {
            sector_name: top_components.get(sector_name, [])
            for sector_name in focus_names
        },
        "component_unmatched_sectors": unmatched_component_sectors,
        "debug_windows": {
            source_key: {
                window_days: [
                    asdict(record)
                    for record in source_windows[source_key][window_days].records
                ]
                for window_days in WINDOW_DAYS
            }
            for source_key in SOURCE_LABELS
        },
    }
