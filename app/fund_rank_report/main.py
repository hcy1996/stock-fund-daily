from __future__ import annotations

import json
from pathlib import Path

from app.config import AppConfig
from app.fund_rank_report.ai_classifier import (
    resolve_fund_sector_classifications,
    serialize_classification_record,
)
from app.fund_rank_report.analyzer import (
    build_sector_frequency_rows,
    build_json_payload,
    build_report_payload,
    build_repeat_level,
    build_summary_text,
)
from app.fund_rank_report.fetcher import fetch_period_rank_records
from app.fund_rank_report.holdings import load_rank_fund_holdings, serialize_holding_bundle
from app.fund_rank_report.renderer import render_fund_rank_report_html
from app.storage import connect, init_db, upsert_fund_holdings


def _report_output_dir(output_root: Path, trade_date: str) -> Path:
    return output_root / "fund-rank" / trade_date


def run_fund_rank_report(
    config: AppConfig,
    *,
    top_n: int = 300,
    refresh_ranks: bool = False,
    refresh_holdings: bool = False,
) -> tuple[Path, Path, Path, str]:
    raw_period_records, warnings = fetch_period_rank_records(
        config.storage.raw_dir,
        config.sources.user_agent,
        top_n=top_n,
        refresh=refresh_ranks,
    )
    holding_bundles, holding_warnings = load_rank_fund_holdings(
        raw_period_records,
        config.storage.raw_dir,
        config.sources.user_agent,
        refresh=refresh_holdings,
    )
    warnings.extend(holding_warnings)

    conn = connect(config.storage.db_path)
    init_db(conn)
    try:
        upsert_fund_holdings(
            conn,
            [record for bundle in holding_bundles for record in bundle.holdings],
        )
        classifications, classification_warnings = resolve_fund_sector_classifications(
            conn,
            config.ai,
            config.storage.raw_dir,
            holding_bundles,
        )
    finally:
        conn.close()
    warnings.extend(classification_warnings)

    payload = build_report_payload(
        raw_period_records,
        requested_top_n=top_n,
        warnings=warnings,
    )
    payload["fund_holdings"] = {
        bundle.fund_code: serialize_holding_bundle(bundle)
        for bundle in holding_bundles
    }
    payload["fund_sector_classifications"] = {
        bundle.fund_code: serialize_classification_record(classifications[(bundle.fund_code, bundle.report_date)])
        for bundle in holding_bundles
        if (bundle.fund_code, bundle.report_date) in classifications
    }
    payload["sector_frequency_rows"] = build_sector_frequency_rows(
        payload["fund_sector_classifications"]
    )
    if not any(section["records"] for section in payload["period_sections"]):
        raise RuntimeError("所有阶段基金排行榜都为空，无法生成报告。")

    trade_date = payload["trade_date"]
    output_dir = _report_output_dir(config.storage.output_dir, trade_date)
    output_dir.mkdir(parents=True, exist_ok=True)

    report_name = f"{config.meta.report_name} - 基金排行榜"
    html = render_fund_rank_report_html(payload, report_name)
    html_path = output_dir / "report.html"
    json_path = output_dir / "result.json"
    summary_path = output_dir / "summary.txt"
    summary_text = build_summary_text(payload)

    html_path.write_text(html, encoding="utf-8")
    json_path.write_text(
        json.dumps(build_json_payload(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary_path.write_text(summary_text, encoding="utf-8")
    return json_path, summary_path, html_path, summary_text


__all__ = [
    "build_repeat_level",
    "run_fund_rank_report",
]
