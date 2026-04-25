from __future__ import annotations

import sqlite3
from dataclasses import asdict
from pathlib import Path

from app.models import SectorFlowRecord


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sector_flow_snapshots (
            trade_date TEXT NOT NULL,
            window_days INTEGER NOT NULL,
            source TEXT NOT NULL,
            sector_code TEXT NOT NULL,
            sector_name TEXT NOT NULL,
            latest_index_value REAL,
            pct_change REAL,
            main_net_inflow REAL,
            main_net_inflow_ratio REAL,
            super_order_inflow REAL,
            super_order_ratio REAL,
            large_order_inflow REAL,
            large_order_ratio REAL,
            medium_order_inflow REAL,
            medium_order_ratio REAL,
            small_order_inflow REAL,
            small_order_ratio REAL,
            leader_stock_name TEXT,
            leader_stock_code TEXT,
            leader_stock_pct_change REAL,
            rank_no INTEGER,
            raw_payload TEXT,
            PRIMARY KEY (trade_date, window_days, sector_code)
        );

        CREATE TABLE IF NOT EXISTS email_send_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sent_at TEXT NOT NULL,
            subject TEXT NOT NULL,
            recipients TEXT NOT NULL,
            status TEXT NOT NULL,
            detail TEXT
        );
        """
    )
    conn.commit()


def upsert_sector_flows(conn: sqlite3.Connection, records: list[SectorFlowRecord]) -> int:
    if not records:
        return 0

    conn.executemany(
        """
        INSERT INTO sector_flow_snapshots (
            trade_date, window_days, source, sector_code, sector_name, latest_index_value,
            pct_change, main_net_inflow, main_net_inflow_ratio, super_order_inflow,
            super_order_ratio, large_order_inflow, large_order_ratio, medium_order_inflow,
            medium_order_ratio, small_order_inflow, small_order_ratio, leader_stock_name,
            leader_stock_code, leader_stock_pct_change, rank_no, raw_payload
        ) VALUES (
            :trade_date, :window_days, :source, :sector_code, :sector_name, :latest_index_value,
            :pct_change, :main_net_inflow, :main_net_inflow_ratio, :super_order_inflow,
            :super_order_ratio, :large_order_inflow, :large_order_ratio, :medium_order_inflow,
            :medium_order_ratio, :small_order_inflow, :small_order_ratio, :leader_stock_name,
            :leader_stock_code, :leader_stock_pct_change, :rank_no, :raw_payload
        )
        ON CONFLICT(trade_date, window_days, sector_code) DO UPDATE SET
            source=excluded.source,
            sector_name=excluded.sector_name,
            latest_index_value=excluded.latest_index_value,
            pct_change=excluded.pct_change,
            main_net_inflow=excluded.main_net_inflow,
            main_net_inflow_ratio=excluded.main_net_inflow_ratio,
            super_order_inflow=excluded.super_order_inflow,
            super_order_ratio=excluded.super_order_ratio,
            large_order_inflow=excluded.large_order_inflow,
            large_order_ratio=excluded.large_order_ratio,
            medium_order_inflow=excluded.medium_order_inflow,
            medium_order_ratio=excluded.medium_order_ratio,
            small_order_inflow=excluded.small_order_inflow,
            small_order_ratio=excluded.small_order_ratio,
            leader_stock_name=excluded.leader_stock_name,
            leader_stock_code=excluded.leader_stock_code,
            leader_stock_pct_change=excluded.leader_stock_pct_change,
            rank_no=excluded.rank_no,
            raw_payload=excluded.raw_payload
        """,
        [asdict(record) for record in records],
    )
    conn.commit()
    return len(records)


def latest_trade_date(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT trade_date FROM sector_flow_snapshots ORDER BY trade_date DESC LIMIT 1"
    ).fetchone()
    return row["trade_date"] if row else None


def get_window_records(
    conn: sqlite3.Connection,
    trade_date: str,
    window_days: int,
    limit: int | None = None,
) -> list[sqlite3.Row]:
    sql = """
        SELECT *
        FROM sector_flow_snapshots
        WHERE trade_date = ? AND window_days = ?
        ORDER BY COALESCE(rank_no, 9999), COALESCE(main_net_inflow, -999999999999) DESC
    """
    params: list[object] = [trade_date, window_days]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return list(conn.execute(sql, params))


def get_recent_daily_rows(conn: sqlite3.Connection, limit_days: int) -> list[sqlite3.Row]:
    trade_dates = [
        row["trade_date"]
        for row in conn.execute(
            """
            SELECT DISTINCT trade_date
            FROM sector_flow_snapshots
            WHERE window_days = 1
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            (limit_days,),
        )
    ]
    if not trade_dates:
        return []

    placeholders = ",".join("?" for _ in trade_dates)
    return list(
        conn.execute(
            f"""
            SELECT *
            FROM sector_flow_snapshots
            WHERE window_days = 1 AND trade_date IN ({placeholders})
            ORDER BY trade_date DESC, COALESCE(rank_no, 9999)
            """,
            trade_dates,
        )
    )


def log_email_send(
    conn: sqlite3.Connection,
    sent_at: str,
    subject: str,
    recipients: list[str],
    status: str,
    detail: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO email_send_logs (sent_at, subject, recipients, status, detail)
        VALUES (?, ?, ?, ?, ?)
        """,
        (sent_at, subject, ",".join(recipients), status, detail),
    )
    conn.commit()
