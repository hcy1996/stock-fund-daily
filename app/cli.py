from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

from app.analyzer import build_report_payload
from app.ai_summary import (
    build_ai_prompt,
    build_ai_prompt_payload,
    build_weekly_ai_prompt,
    request_ai_text,
)
from app.config import PROJECT_ROOT, load_config
from app.emailer import send_html_email
from app.fetch_status import fetch_succeeded, load_fetch_status
from app.fund_rank_report import run_fund_rank_report
from app.fund_matcher import load_fund_catalog, match_funds
from app.report_history import (
    archive_raw_snapshot,
    build_analysis_snapshot,
    load_recent_analysis_snapshots,
    save_analysis_artifacts,
)
from app.raw_enricher import (
    eastmoney_window_rollup_path,
    enrich_raw_data,
    load_eastmoney_20d_rollup,
    load_eastmoney_window_rollup,
)
from app.report_renderer import render_html, save_report
from app.sector_bridge import build_sector_bridge_payload
from app.sector_bridge_ai import request_sector_bridge_ai_summary
from app.sector_strength import get_sector_strength_analysis_json, run_sector_strength_analysis
from app.sources.fund_holdings import (
    collect_rank_funds,
    fetch_rank_fund_holdings,
    holdings_raw_path,
    parse_holdings_file,
)
from app.sources.fund_rank import (
    EASTMONEY_FUND_RANK_PERIODS,
    EASTMONEY_FUND_RANK_SOURCE,
    expected_rank_files,
    parse_rank_file,
)
from app.sources.eastmoney import expected_raw_files, parse_window_file
from app.sources.tonghuashun import parse_20d_file, parse_board_file
from app.storage import (
    connect,
    init_db,
    latest_trade_date,
    log_email_send,
    upsert_fund_holdings,
    upsert_fund_ranks,
    upsert_sector_flows,
)
from app.scheduler import run_forever

STATS_TOP_N = 50


def _upsert_source_records(conn, records, top_n: int) -> int:
    if not records:
        return 0
    return upsert_sector_flows(conn, records[:top_n])


def ingest_raw(config) -> int:
    conn = connect(config.storage.db_path)
    init_db(conn)
    fetch_status = load_fetch_status(config.storage.raw_dir)

    total = 0
    stats_top_n = max(config.report.top_n, STATS_TOP_N)
    for window_days, path in expected_raw_files(config.storage.raw_dir).items():
        eastmoney_records = []
        if fetch_succeeded(fetch_status, "eastmoney", window_days):
            eastmoney_records = parse_window_file(path, window_days)
        if len(eastmoney_records) < config.report.top_n:
            eastmoney_records = load_eastmoney_window_rollup(
                eastmoney_window_rollup_path(config.storage.raw_dir, window_days)
            )
        total += _upsert_source_records(
            conn,
            eastmoney_records,
            stats_top_n,
        )

        if fetch_succeeded(fetch_status, "tonghuashun", window_days):
            tonghuashun_path = config.storage.raw_dir / "tonghuashun" / f"{window_days}d.html"
            total += _upsert_source_records(
                conn,
                parse_board_file(tonghuashun_path, window_days),
                stats_top_n,
            )

    eastmoney_20d_records = load_eastmoney_20d_rollup(config.storage.raw_dir / "eastmoney" / "20d.json")
    total += _upsert_source_records(conn, eastmoney_20d_records, stats_top_n)

    if fetch_succeeded(fetch_status, "tonghuashun", 20):
        total += _upsert_source_records(
            conn,
            parse_20d_file(config.storage.raw_dir / "tonghuashun" / "20d.html"),
            stats_top_n,
        )

    for period, path in expected_rank_files(config.storage.raw_dir).items():
        status_days = int(EASTMONEY_FUND_RANK_PERIODS[period]["status_days"])
        if fetch_succeeded(fetch_status, EASTMONEY_FUND_RANK_SOURCE, status_days):
            total += upsert_fund_ranks(
                conn,
                parse_rank_file(path, period)[: config.report.top_n],
            )

    for fund in collect_rank_funds(config.storage.raw_dir, config.report.top_n):
        total += upsert_fund_holdings(
            conn,
            parse_holdings_file(holdings_raw_path(config.storage.raw_dir, fund.fund_code), fund.fund_code),
        )

    trade_date = latest_trade_date(conn)
    if trade_date:
        archive_raw_snapshot(config.storage.raw_dir, trade_date)
    conn.close()
    return total


def generate_report(config) -> tuple[Path, str, str]:
    conn = connect(config.storage.db_path)
    init_db(conn)
    payload = build_report_payload(
        conn=conn,
        top_n=config.report.top_n,
        stats_top_n=STATS_TOP_N,
        funds_per_sector=config.report.funds_per_sector,
        fund_catalog=load_fund_catalog(PROJECT_ROOT / "data" / "fund_links.json"),
        match_funds_fn=match_funds,
        raw_dir=config.storage.raw_dir,
    )
    bridge_ai_prompt = None
    bridge_ai_summary = None
    bridge_ai_warning = None
    try:
        _trade_date, sector_strength_payload, _sector_strength_summary = get_sector_strength_analysis_json(
            config,
            board_queries=payload.get("focus_names", []),
            candidate_limit=config.report.top_n,
        )
        payload["sector_strength"] = sector_strength_payload
        payload["sector_bridge"] = build_sector_bridge_payload(payload, sector_strength_payload)
        if payload["sector_bridge"].get("available"):
            bridge_ai_summary, bridge_ai_warning, bridge_ai_prompt = request_sector_bridge_ai_summary(
                config.ai,
                payload,
                payload["sector_bridge"],
            )
    except Exception as exc:
        payload["sector_strength"] = None
        payload["sector_bridge"] = {
            "available": False,
            "warnings": [f"板块强度整合失败: {exc}"],
            "summary_cards": [],
            "focus_sector_cards": [],
            "top_ranked_sectors": [],
            "fund_to_sector_links": [],
        }
    payload["sector_bridge_ai_summary"] = bridge_ai_summary
    payload["sector_bridge_ai_warning"] = bridge_ai_warning

    daily_ai_prompt = build_ai_prompt(payload)
    ai_summary, ai_warning = request_ai_text(
        config.ai,
        daily_ai_prompt,
        request_label="daily-ai-summary",
    )
    payload["ai_summary"] = ai_summary
    payload["ai_summary_warning"] = ai_warning
    if ai_warning:
        print(f"AI warning: {ai_warning}")

    recent_snapshots = load_recent_analysis_snapshots(
        limit_days=6,
        exclude_trade_dates={payload["trade_date"]},
    )
    snapshot = build_analysis_snapshot(
        trade_date=payload["trade_date"],
        daily_ai_input=build_ai_prompt_payload(payload),
        daily_ai_summary=ai_summary,
        daily_ai_warning=ai_warning,
        weekly_ai_summary=None,
        weekly_ai_warning=None,
        bridge_ai_prompt=bridge_ai_prompt,
        bridge_ai_summary=bridge_ai_summary,
        bridge_ai_warning=bridge_ai_warning,
    )
    weekly_history = recent_snapshots + [snapshot]
    weekly_ai_prompt = build_weekly_ai_prompt(weekly_history)
    weekly_ai_summary, weekly_ai_warning = request_ai_text(
        config.ai,
        weekly_ai_prompt,
        request_label="weekly-ai-summary",
    )
    payload["weekly_ai_summary"] = weekly_ai_summary
    payload["weekly_ai_warning"] = weekly_ai_warning
    if weekly_ai_warning:
        print(f"Weekly AI warning: {weekly_ai_warning}")

    snapshot["weekly_ai_summary"] = weekly_ai_summary
    snapshot["weekly_ai_warning"] = weekly_ai_warning
    save_analysis_artifacts(
        output_dir=config.storage.output_dir,
        trade_date=payload["trade_date"],
        snapshot=snapshot,
        daily_ai_prompt=daily_ai_prompt,
        daily_ai_summary=ai_summary,
        daily_ai_warning=ai_warning,
        weekly_ai_prompt=weekly_ai_prompt,
        weekly_ai_summary=weekly_ai_summary,
        weekly_ai_warning=weekly_ai_warning,
        bridge_ai_prompt=bridge_ai_prompt,
        bridge_ai_summary=bridge_ai_summary,
        bridge_ai_warning=bridge_ai_warning,
    )

    html = render_html(payload, config.meta.report_name, raw_dir=config.storage.raw_dir)
    report_path = save_report(config.storage.output_dir, payload["trade_date"], html)
    subject = f"{config.meta.report_name} | {payload['trade_date']}"
    conn.close()
    return report_path, subject, html


def _send_logged_html_email(
    config,
    *,
    subject: str,
    html: str,
    detail: str,
) -> None:
    if config.using_example_config:
        raise RuntimeError("当前仍在使用 config.example.json。请先复制并填写 config.json。")

    conn = connect(config.storage.db_path)
    init_db(conn)
    now = datetime.now(ZoneInfo(config.schedule.timezone)).isoformat()
    try:
        send_html_email(config.smtp, config.recipients, subject, html)
        log_email_send(conn, now, subject, config.recipients, "success", detail)
        print("Email sent.")
    except Exception as exc:
        log_email_send(conn, now, subject, config.recipients, "failed", str(exc))
        raise
    finally:
        conn.close()


def cmd_ingest(args) -> int:
    config = load_config(args.config)
    count = ingest_raw(config)
    print(f"Ingested {count} records into {config.storage.db_path}")
    return 0


def cmd_report(args) -> int:
    config = load_config(args.config)
    if args.ingest:
        ingest_raw(config)
    report_path, subject, _ = generate_report(config)
    print(subject)
    print(report_path)
    return 0


def cmd_enrich_raw(args) -> int:
    config = load_config(args.config)
    stats = enrich_raw_data(
        raw_dir=config.storage.raw_dir,
        user_agent=config.sources.user_agent,
        top_sector_count=config.report.top_n,
    )
    print(
        "Enriched raw data: "
        f"eastmoney_fallback_daily={stats['eastmoney_fallback_daily_count']}, "
        f"20d_rollup={stats['rollup_count']}, "
        f"component_sectors={stats['component_sector_count']}, "
        f"component_unmatched={stats['component_unmatched_count']}"
    )
    return 0


def cmd_fetch_fund_holdings(args) -> int:
    config = load_config(args.config)
    updated = fetch_rank_fund_holdings(
        raw_dir=config.storage.raw_dir,
        user_agent=config.sources.user_agent,
        top_n=config.report.top_n,
    )
    print(f"Fetched fund holdings: {updated}")
    return 0


def cmd_run_once(args) -> int:
    config = load_config(args.config)
    ingest_raw(config)
    report_path, subject, html = generate_report(config)
    print(report_path)

    if args.dry_run:
        print("Dry run complete. Email not sent.")
        return 0

    _send_logged_html_email(
        config,
        subject=subject,
        html=html,
        detail=str(report_path),
    )
    return 0


def cmd_schedule(args) -> int:
    config = load_config(args.config)
    run_forever(config)
    return 0


def cmd_sector_strength(args) -> int:
    config = load_config(args.config)
    try:
        json_path, summary_path, html_path, summary_text = run_sector_strength_analysis(
            config,
            board_queries=args.board,
            candidate_limit=args.candidate_limit,
        )
    except Exception as exc:
        print(f"sector-strength failed: {exc}")
        return 1
    print(summary_text)
    print(f"JSON: {json_path}")
    print(f"SUMMARY: {summary_path}")
    print(f"HTML: {html_path}")
    return 0


def cmd_fund_rank_report(args) -> int:
    config = load_config(args.config)
    try:
        json_path, summary_path, html_path, summary_text = run_fund_rank_report(
            config,
            top_n=args.top_n,
            refresh_ranks=args.refresh_ranks,
            refresh_holdings=args.refresh_holdings,
        )
    except Exception as exc:
        print(f"fund-rank-report failed: {exc}")
        return 1
    print(summary_text)
    print(f"JSON: {json_path}")
    print(f"SUMMARY: {summary_path}")
    print(f"HTML: {html_path}")
    if args.send_email:
        trade_date = html_path.parent.name
        subject = f"{config.meta.report_name} - 基金排行榜 | {trade_date}"
        html = html_path.read_text(encoding="utf-8")
        _send_logged_html_email(
            config,
            subject=subject,
            html=html,
            detail=str(html_path),
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="股票基金日报工具")
    parser.add_argument("--config", help="配置文件路径，默认读取 config.json")

    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest", help="读取 raw 数据并写入 SQLite")
    ingest_parser.set_defaults(func=cmd_ingest)

    enrich_parser = subparsers.add_parser("enrich-raw", help="基于 raw 数据补充 20 日累计和概念成分股")
    enrich_parser.set_defaults(func=cmd_enrich_raw)

    fund_holdings_parser = subparsers.add_parser("fetch-fund-holdings", help="抓取基金排行榜基金持仓")
    fund_holdings_parser.set_defaults(func=cmd_fetch_fund_holdings)

    report_parser = subparsers.add_parser("report", help="生成 HTML 日报")
    report_parser.add_argument("--ingest", action="store_true", help="生成前先 ingest")
    report_parser.set_defaults(func=cmd_report)

    run_once_parser = subparsers.add_parser("run-once", help="抓数后的完整单次执行")
    run_once_parser.add_argument("--dry-run", action="store_true", help="只生成日报，不发邮件")
    run_once_parser.set_defaults(func=cmd_run_once)

    schedule_parser = subparsers.add_parser("schedule", help="常驻调度进程")
    schedule_parser.set_defaults(func=cmd_schedule)

    sector_strength_parser = subparsers.add_parser("sector-strength", help="A股板块波段强度评分")
    sector_strength_parser.add_argument(
        "--board",
        action="append",
        help="板块名称或 BK 代码，可重复传入；不传则分析候选池全部板块",
    )
    sector_strength_parser.add_argument(
        "--candidate-limit",
        type=int,
        default=50,
        help="每类候选池板块数量，默认 50；默认会合并概念和行业两类候选池",
    )
    sector_strength_parser.set_defaults(func=cmd_sector_strength)

    fund_rank_report_parser = subparsers.add_parser(
        "fund-rank-report",
        help="生成独立基金排行榜报告",
    )
    fund_rank_report_parser.add_argument(
        "--top-n",
        type=int,
        default=300,
        help="每个阶段抓取前 N 条基金，默认 300",
    )
    fund_rank_report_parser.add_argument(
        "--refresh-ranks",
        action="store_true",
        help="显式刷新基金排行榜 raw，默认只读本地缓存",
    )
    fund_rank_report_parser.add_argument(
        "--refresh-holdings",
        action="store_true",
        help="显式刷新基金持仓 raw，默认只读本地缓存",
    )
    fund_rank_report_parser.add_argument(
        "--send-email",
        action="store_true",
        help="生成后发送基金排行榜邮件",
    )
    fund_rank_report_parser.set_defaults(func=cmd_fund_rank_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
