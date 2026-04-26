# Fund Rank Cache AI Classification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `fund-rank-report` read local rank/holding raw data by default, only refresh when explicitly requested, and persist AI sector classifications keyed by `fund_code + report_date`.

**Architecture:** Reuse existing raw files and SQLite patterns. Add a local-first fetch layer for rank and holdings, then add a classification pipeline that reads latest holdings, batches uncached funds through AI, and stores both SQLite rows and JSON debug snapshots.

**Tech Stack:** Python 3, sqlite3, pathlib, json, existing OpenAI-compatible request helpers

---

### Task 1: Local-first fund rank and holdings fetch

**Files:**
- Modify: `app/fund_rank_report/fetcher.py`
- Modify: `app/sources/fund_holdings.py`
- Modify: `app/fund_rank_report/main.py`
- Modify: `app/cli.py`

- [ ] Add explicit refresh flags for rank and holdings.
- [ ] Make rank fetch read local raw files first and only hit source when refresh is enabled or local file missing and refresh fallback is allowed.
- [ ] Make holdings fetch read local holding raw first and only hit source when refresh is enabled or local file missing and refresh fallback is allowed.
- [ ] Surface clear warnings when local files are missing and refresh is disabled.

### Task 2: Persist fund sector classification

**Files:**
- Modify: `app/models.py`
- Modify: `app/storage.py`

- [ ] Add a dataclass for AI fund sector classification row.
- [ ] Add SQLite table and CRUD helpers keyed by `fund_code + report_date`.
- [ ] Keep query helpers focused on “load cached rows by fund/report_date” and “upsert new rows”.

### Task 3: Add AI classification pipeline

**Files:**
- Create: `app/fund_rank_report/holdings.py`
- Create: `app/fund_rank_report/ai_classifier.py`
- Modify: `app/fund_rank_report/main.py`

- [ ] Build a helper that collects unique funds from rank records and resolves latest holdings plus holding `report_date`.
- [ ] Build a helper that serializes per-fund AI input using fund code, fund name, and top holdings.
- [ ] Reuse existing AI request style, but return structured JSON-like sector classification payload.
- [ ] Only request AI for uncached `fund_code + report_date`; otherwise reuse SQLite cache.

### Task 4: Save JSON debug snapshots and expose results

**Files:**
- Create: `data/raw/eastmoney/fund_rank_ai_classification/` (runtime output)
- Modify: `app/fund_rank_report/main.py`
- Modify: `app/fund_rank_report/analyzer.py`

- [ ] Save one JSON debug snapshot per classified fund using `fund_code__report_date.json`.
- [ ] Include classification results and warnings in report JSON payload.
- [ ] Keep HTML wiring optional for now; JSON and pipeline correctness first.

### Task 5: Verify end-to-end

**Files:**
- Verify only

- [ ] Run inline failing checks for local-first rank behavior.
- [ ] Run inline failing checks for local-first holdings behavior.
- [ ] Run inline failing checks for cache hit vs AI miss behavior.
- [ ] Run `python3 -m py_compile app/*.py app/sources/*.py app/fund_rank_report/*.py`.
- [ ] Run `python3 -m app.cli fund-rank-report` once with local-first defaults and confirm no source fetch is required when raw files exist.
