# A股板块波段强度评分 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 Python CLI 项目中新增一个独立的板块波段强度评分命令，不影响原日报链路。

**Architecture:** 复用现有 SQLite 候选池和输出目录，在 `app/sector_strength/` 下新增抓数、指标、评分、编排四层。抓数优先走 AkShare，失败再走东方财富公开接口，最终输出终端摘要和 JSON / 文本文件。

**Tech Stack:** Python 3、SQLite、AkShare、pandas、标准库 `json` / `urllib` / `concurrent.futures`

---

### Task 1: 搭建模块骨架和 CLI 入口

**Files:**
- Create: `app/sector_strength/__init__.py`
- Create: `app/sector_strength/data_fetcher.py`
- Create: `app/sector_strength/indicator_calculator.py`
- Create: `app/sector_strength/scoring_model.py`
- Create: `app/sector_strength/main.py`
- Modify: `app/cli.py`

- [ ] 新建独立包结构，定义数据对象和主入口函数。
- [ ] 在 `app/cli.py` 新增 `sector-strength` 子命令，支持重复传入 `--board`。
- [ ] 保持原有子命令参数和行为不变。

### Task 2: 实现抓数与指标计算

**Files:**
- Modify: `app/sector_strength/data_fetcher.py`
- Modify: `app/sector_strength/indicator_calculator.py`

- [ ] 实现候选池读取：优先 SQLite 最近交易日前 `50`，空时走实时兜底。
- [ ] 实现板块名录解析：优先 AkShare，失败回退东方财富公开接口。
- [ ] 实现板块 60 日 K、沪深 300 K、5/10 日资金流、成分股、涨停池抓取。
- [ ] 对齐字段，缺失数据统一返回 `None`。
- [ ] 实现 5/20 日涨幅、RS、MA、广度、活跃度、龙头候选等指标函数，并给函数写注释或 docstring。

### Task 3: 实现评分、输出和文档

**Files:**
- Modify: `app/sector_strength/scoring_model.py`
- Modify: `app/sector_strength/main.py`
- Modify: `README.md`

- [ ] 实现六个子分、风险惩罚、操作建议。
- [ ] 输出终端排名表、关注板块详细信息。
- [ ] 保存 `output/sector-strength/<trade_date>/result.json` 和 `summary.txt`。
- [ ] 更新 README，补安装依赖、命令示例、输出说明、数据源说明。
- [ ] 跑语法检查和真实 CLI 样例验证。

### Task 4: 验证

**Files:**
- Modify: `README.md`

- [ ] 运行：`python3 -m py_compile app/*.py app/sources/*.py app/sector_strength/*.py`
- [ ] 运行：`python3 -m app.cli sector-strength --help`
- [ ] 运行真实样例命令，确认生成 JSON 和文本摘要。
- [ ] 若实时抓数受网络环境影响，记录实际失败信息和降级行为。
