from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

from app.analyzer import build_report_payload
from app.ai_summary import build_ai_summary
from app.config import PROJECT_ROOT, load_config
from app.emailer import send_html_email
from app.fetch_status import fetch_succeeded, load_fetch_status
from app.fund_matcher import load_fund_catalog, match_funds
from app.raw_enricher import (
    eastmoney_window_rollup_path,
    enrich_raw_data,
    load_eastmoney_20d_rollup,
    load_eastmoney_window_rollup,
)
from app.report_renderer import render_html, save_report
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
    payload["ai_summary"] = build_ai_summary(config.ai, payload)
    html = render_html(payload, config.meta.report_name, raw_dir=config.storage.raw_dir)
    report_path = save_report(config.storage.output_dir, payload["trade_date"], html)
    subject = f"{config.meta.report_name} | {payload['trade_date']}"
    conn.close()
    return report_path, subject, html


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

    if config.using_example_config:
        raise RuntimeError("当前仍在使用 config.example.json。请先复制并填写 config.json。")

    conn = connect(config.storage.db_path)
    init_db(conn)
    now = datetime.now(ZoneInfo(config.schedule.timezone)).isoformat()
    try:
        send_html_email(config.smtp, config.recipients, subject, html)
        log_email_send(conn, now, subject, config.recipients, "success", str(report_path))
        print("Email sent.")
    except Exception as exc:
        log_email_send(conn, now, subject, config.recipients, "failed", str(exc))
        raise
    finally:
        conn.close()
    return 0


def cmd_schedule(args) -> int:
    config = load_config(args.config)
    run_forever(config)
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
