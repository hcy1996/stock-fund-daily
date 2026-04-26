# Fund Rank Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an independent fund ranking report that fetches six Eastmoney ranking windows, applies A/C dedupe, analyzes cross-window repeat frequency, and outputs standalone HTML/JSON/summary artifacts.

**Architecture:** Add a new `app/fund_rank_report/` package that owns fetch, dedupe, analysis, and rendering. Keep existing daily-report flow untouched and expose the feature through a new CLI command that writes to `output/fund-rank/<trade_date>/`.

**Tech Stack:** Python 3, stdlib dataclasses/json/pathlib, existing Eastmoney sources, existing config/CLI structure, standalone HTML rendering.

---

### Task 1: Extend fund-rank source definitions

**Files:**
- Modify: `app/models.py`
- Modify: `app/sources/fund_rank.py`

- [ ] **Step 1: Add the new ranking-period metadata shape**

Update `FundRankRecord` so one record can carry all six return windows needed by the new report.

- [ ] **Step 2: Verify the red state with a targeted import smoke check**

Run:

```bash
python3 -c "from app.sources.fund_rank import EASTMONEY_FUND_RANK_PERIODS; print(sorted(EASTMONEY_FUND_RANK_PERIODS))"
```

Expected before code changes: missing the new periods for `quarter`, `half_year`, `year`.

- [ ] **Step 3: Implement new period config and parse fields**

Add `quarter`, `half_year`, `year` period definitions and parse `近3月 / 近6月 / 近1年` values into `FundRankRecord`.

- [ ] **Step 4: Verify green with the same smoke check**

Run:

```bash
python3 -c "from app.sources.fund_rank import EASTMONEY_FUND_RANK_PERIODS; print(sorted(EASTMONEY_FUND_RANK_PERIODS))"
```

Expected: includes `day`, `half_year`, `month`, `quarter`, `week`, `year`.

### Task 2: Build fund-rank report analysis package

**Files:**
- Create: `app/fund_rank_report/__init__.py`
- Create: `app/fund_rank_report/dedupe.py`
- Create: `app/fund_rank_report/analyzer.py`

- [ ] **Step 1: Verify the red state for new package imports**

Run:

```bash
python3 -c "from app.fund_rank_report.dedupe import dedupe_period_records"
```

Expected: import failure because module does not exist yet.

- [ ] **Step 2: Implement stage-1 dedupe helpers**

Add helpers for:

- base fund-name normalization
- share-class preference
- period-level A/C dedupe

- [ ] **Step 3: Implement stage-1 cross-period analysis**

Add helpers for:

- stage labels
- repeat counts / rates
- per-fund stage summaries
- color-level mapping

- [ ] **Step 4: Verify green with an inline behavior check**

Run:

```bash
python3 -c "from app.fund_rank_report.dedupe import pick_preferred_share_class; print(pick_preferred_share_class(['某基金A','某基金C']))"
```

Expected: outputs the `C` choice.

### Task 3: Build fetch/output orchestration

**Files:**
- Create: `app/fund_rank_report/fetcher.py`
- Create: `app/fund_rank_report/main.py`

- [ ] **Step 1: Verify the red state for command entrypoint**

Run:

```bash
python3 -c "from app.fund_rank_report.main import run_fund_rank_report"
```

Expected: import failure because module does not exist yet.

- [ ] **Step 2: Implement direct ranking fetch functions**

Add code that:

- fetches all six windows
- requests `pn=150`
- parses payloads with the shared source parser

- [ ] **Step 3: Implement report payload builder**

Compose:

- per-period deduped tables
- repeated-fund ranking
- warnings
- trade date / snapshot date

- [ ] **Step 4: Verify green with a no-network unit smoke**

Run:

```bash
python3 -c "from app.fund_rank_report.main import build_repeat_level; print(build_repeat_level(5))"
```

Expected: returns the highest repeat label.

### Task 4: Build standalone renderer

**Files:**
- Create: `app/fund_rank_report/renderer.py`

- [ ] **Step 1: Verify red by importing the missing renderer**

Run:

```bash
python3 -c "from app.fund_rank_report.renderer import render_fund_rank_report_html"
```

Expected: import failure because module does not exist yet.

- [ ] **Step 2: Implement standalone HTML rendering**

Render:

- overview cards
- six period tables
- repeated-fund table
- repeat color tags

- [ ] **Step 3: Verify green with a simple render smoke**

Run:

```bash
python3 -c "from app.fund_rank_report.renderer import render_fund_rank_report_html; print(render_fund_rank_report_html({'trade_date':'2026-04-26','period_sections':[],'repeat_rows':[],'warnings':[]})[:15])"
```

Expected: output starts with `<!DOCTYPE html>` or `<html`.

### Task 5: Wire CLI and artifact writing

**Files:**
- Modify: `app/cli.py`

- [ ] **Step 1: Verify red for the new CLI command**

Run:

```bash
python3 -m app.cli --help
```

Expected before changes: no `fund-rank-report` subcommand.

- [ ] **Step 2: Add CLI subcommand and artifact output**

Expose:

- `python3 -m app.cli fund-rank-report`
- optional `--top-n` display limit for page rendering

- [ ] **Step 3: Verify green for CLI registration**

Run:

```bash
python3 -m app.cli --help
```

Expected: includes `fund-rank-report`.

### Task 6: End-to-end verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add command documentation**

Document the new independent report command and stage-1 scope.

- [ ] **Step 2: Run focused module verification**

Run:

```bash
python3 -m py_compile app/*.py app/sources/*.py app/fund_rank_report/*.py
```

Expected: exit `0`.

- [ ] **Step 3: Run the CLI help verification**

Run:

```bash
python3 -m app.cli fund-rank-report --help
```

Expected: prints the new command help and exits `0`.

- [ ] **Step 4: Run a live small-sample smoke if network is available**

Run:

```bash
python3 -m app.cli fund-rank-report --top-n 20
```

Expected: writes `output/fund-rank/<trade_date>/` artifacts or reports a source warning clearly.
