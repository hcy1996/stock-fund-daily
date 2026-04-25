from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

from app.analyzer import build_report_payload
from app.config import PROJECT_ROOT, load_config
from app.emailer import send_html_email
from app.fund_matcher import load_fund_catalog, match_funds
from app.raw_enricher import enrich_raw_data, load_eastmoney_20d_rollup
from app.report_renderer import render_html, save_report
from app.sources.eastmoney import expected_raw_files, parse_window_file
from app.sources.tonghuashun import parse_20d_file, parse_board_file
from app.storage import connect, init_db, log_email_send, upsert_sector_flows
from app.scheduler import run_forever


def ingest_raw(config) -> int:
    conn = connect(config.storage.db_path)
    init_db(conn)

    total = 0
    for window_days, path in expected_raw_files(config.storage.raw_dir).items():
        eastmoney_records = parse_window_file(path, window_days)
        tonghuashun_path = config.storage.raw_dir / "tonghuashun" / f"{window_days}d.html"
        tonghuashun_records = parse_board_file(tonghuashun_path, window_days)
        selected_records = eastmoney_records
        if len(eastmoney_records) < config.report.top_n and tonghuashun_records:
            selected_records = tonghuashun_records
        total += upsert_sector_flows(conn, selected_records)

    eastmoney_20d_records = load_eastmoney_20d_rollup(config.storage.raw_dir / "eastmoney" / "20d.json")
    if len(eastmoney_20d_records) >= config.report.top_n:
        total += upsert_sector_flows(conn, eastmoney_20d_records)
    elif config.sources.enable_tonghuashun_20d:
        total += upsert_sector_flows(
            conn,
            parse_20d_file(config.storage.raw_dir / "tonghuashun" / "20d.html"),
        )
    elif eastmoney_20d_records:
        total += upsert_sector_flows(conn, eastmoney_20d_records)

    conn.close()
    return total


def generate_report(config) -> tuple[Path, str, str]:
    conn = connect(config.storage.db_path)
    init_db(conn)
    payload = build_report_payload(
        conn=conn,
        top_n=config.report.top_n,
        funds_per_sector=config.report.funds_per_sector,
        fund_catalog=load_fund_catalog(PROJECT_ROOT / "data" / "fund_links.json"),
        match_funds_fn=match_funds,
        raw_dir=config.storage.raw_dir,
    )
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
        f"20d_rollup={stats['rollup_count']}, "
        f"component_sectors={stats['component_sector_count']}, "
        f"component_unmatched={stats['component_unmatched_count']}"
    )
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
