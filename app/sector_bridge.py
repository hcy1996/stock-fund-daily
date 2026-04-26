from __future__ import annotations

from typing import Any


_NORMALIZE_DROP_CHARS = " ()（）-_/."


def _normalize_name(name: str) -> str:
    normalized = name
    for char in _NORMALIZE_DROP_CHARS:
        normalized = normalized.replace(char, "")
    return normalized.strip()


def _sector_strength_lookup(sector_strength_payload: dict) -> dict[str, dict]:
    lookup: dict[str, dict] = {}
    for item in sector_strength_payload.get("results", []):
        indicators = item.get("indicators", {})
        name = str(indicators.get("board_name", "")).strip()
        if not name:
            continue
        lookup.setdefault(name, item)
        lookup.setdefault(_normalize_name(name), item)
    return lookup


def _match_sector_item(lookup: dict[str, dict], sector_name: str) -> dict | None:
    direct = lookup.get(sector_name)
    if direct is not None:
        return direct
    return lookup.get(_normalize_name(sector_name))


def _collect_focus_sector_names(report_payload: dict) -> list[str]:
    names = list(report_payload.get("focus_names", []))
    if not names:
        names = list(report_payload.get("related_funds", {}).keys())
    if not names:
        names = list(report_payload.get("top_components", {}).keys())
    ordered: dict[str, None] = {}
    for name in names:
        clean_name = str(name).strip()
        if clean_name:
            ordered.setdefault(clean_name, None)
    return list(ordered.keys())


def _top_fund_meta(report_payload: dict) -> dict[str, dict[str, str | None]]:
    meta: dict[str, dict[str, str | None]] = {}
    for section in report_payload.get("fund_rank_sections", {}).values():
        for record in section.records[:10]:
            meta.setdefault(
                record.fund_code,
                {
                    "fund_code": record.fund_code,
                    "fund_name": record.fund_name,
                    "fund_type": None,
                },
            )
    return meta


def _holding_overlap_funds(report_payload: dict, sector_name: str) -> dict[str, dict[str, Any]]:
    components = report_payload.get("top_components", {}).get(sector_name, [])
    component_codes = {component.stock_code for component in components if component.stock_code}
    if not component_codes:
        return {}

    overlaps: dict[str, dict[str, Any]] = {}
    for fund_code, holdings in report_payload.get("fund_holdings", {}).items():
        overlap_stocks = [
            holding.stock_name
            for holding in holdings
            if holding.stock_code in component_codes and holding.stock_name
        ]
        if not overlap_stocks:
            continue
        overlaps[fund_code] = {
            "fund_code": fund_code,
            "fund_name": holdings[0].fund_name if holdings else fund_code,
            "fund_type": None,
            "report_date": holdings[0].report_date if holdings else None,
            "reasons": ["holding_overlap"],
            "overlap_stocks": overlap_stocks[:4],
        }
    return overlaps


def _catalog_funds(report_payload: dict, sector_name: str) -> dict[str, dict[str, Any]]:
    funds = report_payload.get("related_funds", {}).get(sector_name, [])
    items: dict[str, dict[str, Any]] = {}
    for fund in funds:
        items[fund.fund_code] = {
            "fund_code": fund.fund_code,
            "fund_name": fund.fund_name,
            "fund_type": fund.fund_type,
            "report_date": None,
            "reasons": ["catalog_match"],
            "overlap_stocks": [],
        }
    return items


def _merge_sector_funds(report_payload: dict, sector_name: str) -> list[dict]:
    merged = _catalog_funds(report_payload, sector_name)
    overlaps = _holding_overlap_funds(report_payload, sector_name)
    for fund_code, overlap in overlaps.items():
        current = merged.get(fund_code)
        if current is None:
            merged[fund_code] = overlap
            continue
        current["reasons"] = sorted(set(current["reasons"] + overlap["reasons"]))
        current["overlap_stocks"] = overlap["overlap_stocks"]
        current["report_date"] = overlap["report_date"]
        if not current.get("fund_name"):
            current["fund_name"] = overlap["fund_name"]
    return sorted(
        merged.values(),
        key=lambda item: (
            "catalog_match" not in item["reasons"],
            -len(item["overlap_stocks"]),
            item["fund_name"],
        ),
    )[:6]


def _build_focus_sector_cards(report_payload: dict, sector_strength_payload: dict) -> list[dict]:
    lookup = _sector_strength_lookup(sector_strength_payload)
    cards: list[dict] = []
    for sector_name in _collect_focus_sector_names(report_payload):
        matched = _match_sector_item(lookup, sector_name)
        if matched is None:
            continue
        indicators = matched.get("indicators", {})
        score = matched.get("score", {})
        cards.append(
            {
                "sector_name": indicators.get("board_name") or sector_name,
                "score": score.get("total_score"),
                "grade_code": score.get("grade_code"),
                "grade_label": score.get("grade_label"),
                "suggestion": score.get("suggestion"),
                "board_type": indicators.get("board_type"),
                "leader_candidate": indicators.get("leader_candidate"),
                "rs_20d": indicators.get("rs_20d"),
                "inflow_10d_ratio": indicators.get("inflow_10d_ratio"),
                "amount_ratio_20d": indicators.get("amount_ratio_20d"),
                "sub_scores": score.get("sub_scores", {}),
                "risk_flags": score.get("risk_flags", []),
                "warnings": indicators.get("warnings", []),
                "related_funds": _merge_sector_funds(report_payload, sector_name),
            }
        )
    return sorted(cards, key=lambda item: (item["score"] is None, -(item["score"] or -1), item["sector_name"]))


def _build_top_ranked_sectors(sector_strength_payload: dict) -> list[dict]:
    items: list[dict] = []
    for item in sector_strength_payload.get("results", []):
        indicators = item.get("indicators", {})
        score = item.get("score", {})
        items.append(
            {
                "sector_name": indicators.get("board_name"),
                "score": score.get("total_score"),
                "grade_code": score.get("grade_code"),
                "suggestion": score.get("suggestion"),
            }
        )
    return [item for item in items if item["sector_name"]][:8]


def _build_fund_to_sector_links(report_payload: dict, focus_sector_cards: list[dict]) -> list[dict]:
    fund_meta = _top_fund_meta(report_payload)
    links: dict[str, dict[str, Any]] = {}
    for card in focus_sector_cards:
        sector_name = card["sector_name"]
        for fund in card["related_funds"]:
            bucket = links.setdefault(
                fund["fund_code"],
                {
                    **fund_meta.get(
                        fund["fund_code"],
                        {
                            "fund_code": fund["fund_code"],
                            "fund_name": fund["fund_name"],
                            "fund_type": fund.get("fund_type"),
                        },
                    ),
                    "sectors": [],
                },
            )
            bucket["fund_name"] = bucket.get("fund_name") or fund["fund_name"]
            bucket["fund_type"] = bucket.get("fund_type") or fund.get("fund_type")
            bucket["sectors"].append(
                {
                    "sector_name": sector_name,
                    "score": card["score"],
                    "grade_code": card["grade_code"],
                    "reasons": fund["reasons"],
                    "overlap_stocks": fund["overlap_stocks"],
                }
            )
    return sorted(
        links.values(),
        key=lambda item: (-len(item["sectors"]), item["fund_name"] or item["fund_code"]),
    )


def _build_summary_cards(focus_sector_cards: list[dict]) -> list[dict]:
    valid_scores = [item["score"] for item in focus_sector_cards if item["score"] is not None]
    buy_count = sum(1 for item in focus_sector_cards if item["suggestion"] == "买入")
    watch_count = sum(1 for item in focus_sector_cards if item["suggestion"] == "观察")
    avoid_count = sum(1 for item in focus_sector_cards if item["suggestion"] == "回避")
    top_sector = focus_sector_cards[0]["sector_name"] if focus_sector_cards else "暂无"
    avg_score = round(sum(valid_scores) / len(valid_scores), 2) if valid_scores else None
    return [
        {"label": "关联强度板块数", "value": len(focus_sector_cards)},
        {"label": "最高相关主线", "value": top_sector},
        {"label": "平均板块分", "value": "null" if avg_score is None else f"{avg_score:.2f}"},
        {"label": "买入/观察/回避", "value": f"{buy_count}/{watch_count}/{avoid_count}"},
    ]


def build_sector_bridge_payload(report_payload: dict, sector_strength_payload: dict) -> dict:
    focus_sector_cards = _build_focus_sector_cards(report_payload, sector_strength_payload)
    return {
        "available": bool(focus_sector_cards),
        "trade_date": report_payload.get("trade_date"),
        "warnings": list(sector_strength_payload.get("warnings", [])),
        "summary_cards": _build_summary_cards(focus_sector_cards),
        "focus_sector_cards": focus_sector_cards,
        "top_ranked_sectors": _build_top_ranked_sectors(sector_strength_payload),
        "fund_to_sector_links": _build_fund_to_sector_links(report_payload, focus_sector_cards),
    }
