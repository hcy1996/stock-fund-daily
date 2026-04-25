from __future__ import annotations

import json
from pathlib import Path

from app.models import MatchedFund


def load_fund_catalog(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def match_funds(
    sector_name: str,
    catalog: list[dict],
    limit: int,
) -> list[MatchedFund]:
    matches: list[MatchedFund] = []
    seen: set[str] = set()

    for item in catalog:
        keywords = item.get("keywords", [])
        if not any(keyword in sector_name for keyword in keywords):
            continue
        for fund in item.get("funds", []):
            code = fund["code"]
            if code in seen:
                continue
            seen.add(code)
            matches.append(
                MatchedFund(
                    fund_code=code,
                    fund_name=fund["name"],
                    fund_type=fund["type"],
                    note=item.get("note"),
                )
            )
            if len(matches) >= limit:
                return matches
    return matches
