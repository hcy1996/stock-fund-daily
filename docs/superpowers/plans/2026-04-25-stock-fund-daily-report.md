# Stock Fund Daily Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local daily report tool that fetches concept-sector fund flows, analyzes hot sectors, matches related ETFs/funds, renders an HTML email, and sends it at 21:00.

**Architecture:** Use Python 3 standard library only. Fetch Eastmoney JSON for `1/3/5/10` windows, fetch Tonghuashun HTML for `20` day when available, persist snapshots to SQLite, analyze rankings into a daily digest, then render and send via SMTP.

**Tech Stack:** Python 3.14, `sqlite3`, `urllib`, `smtplib`, `email`, `zoneinfo`, `argparse`, `json`, `html`

---

### Task 1: Project Skeleton

**Files:**
- Create: `README.md`
- Create: `app/__init__.py`
- Create: `app/config.py`
- Create: `app/cli.py`
- Create: `config.example.json`

- [ ] Create basic project files and config format
- [ ] Add CLI entry for `fetch`, `report`, `send`, `run-once`, `schedule`
- [ ] Add README usage and config instructions
- [ ] Verify import path with `python3 -m py_compile app/*.py`

### Task 2: Source Adapters

**Files:**
- Create: `app/sources/__init__.py`
- Create: `app/sources/eastmoney.py`
- Create: `app/sources/tonghuashun.py`
- Create: `app/models.py`

- [ ] Implement Eastmoney adapter for `1/3/5/10`
- [ ] Implement Tonghuashun adapter for `20`
- [ ] Normalize source output into shared dataclasses
- [ ] Verify fetch commands return parseable records

### Task 3: Local Storage

**Files:**
- Create: `app/storage.py`

- [ ] Create SQLite schema bootstrap
- [ ] Add snapshot upsert methods
- [ ] Add fund link storage methods
- [ ] Add email send log storage methods
- [ ] Verify schema init and insert/read cycle

### Task 4: Analysis Engine

**Files:**
- Create: `app/analyzer.py`

- [ ] Generate per-window Top10 lists
- [ ] Compute persistent hot sectors
- [ ] Compute emerging sectors
- [ ] Compute weakening sectors
- [ ] Add `20` day local accumulation fallback

### Task 5: Fund Matching

**Files:**
- Create: `app/fund_matcher.py`
- Create: `data/fund_links.json`

- [ ] Add curated sector to ETF mapping file
- [ ] Add keyword fallback matcher
- [ ] Return top related funds per hot sector

### Task 6: HTML Report

**Files:**
- Create: `app/report_renderer.py`
- Create: `output/.gitkeep`

- [ ] Render daily digest HTML
- [ ] Include source labels and fallback labels
- [ ] Save local HTML artifact before sending

### Task 7: Email Delivery

**Files:**
- Create: `app/emailer.py`

- [ ] Implement SMTP send
- [ ] Support multiple recipients
- [ ] Log success and failure into SQLite

### Task 8: Scheduler

**Files:**
- Create: `app/scheduler.py`
- Create: `ops/com.codex.stock-daily-report.plist.example`

- [ ] Implement local scheduler loop for daily `21:00 Asia/Shanghai`
- [ ] Add one-shot runner for cron/launchd
- [ ] Add macOS launchd template without installing it

### Task 9: Verification

**Files:**
- Modify: `README.md`

- [ ] Run `python3 -m py_compile app/*.py app/sources/*.py`
- [ ] Run `python3 -m app.cli fetch`
- [ ] Run `python3 -m app.cli report`
- [ ] Run `python3 -m app.cli run-once --dry-run`
- [ ] Update README with exact commands and known limits

## Self-Review Notes

- Spec coverage complete for source fetch, storage, analysis, report, email, and schedule
- No placeholders remain except the explicit `20 日` source-stability TODO carried from spec
- No separate test file planned because local instructions say not to create test files
