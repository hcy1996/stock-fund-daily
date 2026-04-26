# 股票基金日报与板块强度整合 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把现有股票基金日报作为主报告，在同一份 HTML 中新增板块强度解释层和 AI 综合解读层。

**Architecture:** 复用现有日报 payload 和板块强度分析结果，在日报生成阶段同步构建一个桥接 payload。规则层完成基金-板块映射和结构化摘要，AI 只负责语义归并和综合解释，渲染层新增两个章节并保留独立板块强度页。

**Tech Stack:** Python 3、SQLite、AkShare、标准库 `json` / `pathlib` / `urllib`

---

### Task 1: 暴露板块强度结构化结果给日报主流程

**Files:**
- Modify: `app/sector_strength/main.py`

- [ ] 新增不写文件的板块强度分析入口，返回结构化 payload。
- [ ] 保留原 `run_sector_strength_analysis()` 行为不变，继续负责输出 JSON / 文本 / HTML。
- [ ] 让日报链路可以按重点板块名称直接拿到板块强度结果。

### Task 2: 新增日报-板块桥接层

**Files:**
- Create: `app/sector_bridge.py`

- [ ] 读取日报 payload 和板块强度 payload。
- [ ] 建立 `fund_to_sector_links`、`sector_to_fund_links`、`focus_sector_strength`、`sector_strength_summary`。
- [ ] 只输出规则层事实，不在这里生成自然语言长文案。

### Task 3: 新增 AI 桥接摘要

**Files:**
- Create: `app/sector_bridge_ai.py`
- Modify: `app/ai_summary.py`

- [ ] 构建板块强度桥接 AI prompt。
- [ ] 复用现有 AI 请求函数，生成“基金-板块-风险”综合摘要。
- [ ] 明确 AI 输入只包含结构化事实，不允许 AI 编造指标。

### Task 4: 在日报主流程接入板块强度与桥接层

**Files:**
- Modify: `app/cli.py`
- Modify: `app/analyzer.py`
- Modify: `app/report_history.py`

- [ ] 在日报生成阶段同步执行板块强度分析。
- [ ] 把桥接 payload 和 AI 桥接摘要挂到日报 payload。
- [ ] 把新增 AI bridge prompt / summary 一并存到输出和 history 快照。

### Task 5: 新增日报中的板块强度章节

**Files:**
- Create: `app/sector_bridge_renderer.py`
- Modify: `app/report_renderer.py`

- [ ] 新增“板块强度解释层”渲染。
- [ ] 新增“AI 综合解读”渲染。
- [ ] 保留现有日报结构，新增章节插入在基金视角之后。

### Task 6: 文档与验证

**Files:**
- Modify: `README.md`

- [ ] 更新 README，说明日报已集成板块强度和 AI 桥接。
- [ ] 运行：`python3 -m py_compile app/*.py app/sources/*.py app/sector_strength/*.py`
- [ ] 运行：`python3 -m app.cli report`
- [ ] 运行：`python3 -m app.cli run-once --dry-run`
- [ ] 确认日报 HTML 中新增板块强度章节，且独立板块强度页仍可生成。
