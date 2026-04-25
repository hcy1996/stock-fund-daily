# 股票基金日报

本项目用于每天 21:00 生成并发送一封股票/基金日报邮件，核心内容是概念板块资金流入 Top10 与热点板块关联 ETF/基金。

当前日报默认按双数据源并排展示：

- 东方财富 `1/3/5/10` 日公开接口 + 东方财富 `20` 日历史累计
- 同花顺 `1/3/5/10/20` 日公开页面
- 邮件正文直接展示双榜单和差异摘要
- 若某一数据源抓取失败，邮件仍会发送，并在顶部显式告警

## 目录

- `scripts/fetch_market_data.sh`: 抓取东方财富和同花顺原始数据
- `python3 -m app.cli enrich-raw`: 基于 `raw` 数据补充东方财富 `20 日累计` 和 Top10 概念成分股
- `scripts/run_daily.sh`: 一次性跑完整链路
- `app/`: 解析、落库、分析、渲染、发送邮件
- `data/`: SQLite、原始抓取数据、基金映射
- `output/`: 生成的 HTML 日报
- `ops/com.codex.stock-daily-report.plist.example`: macOS `launchd` 模板

## 快速开始

1. 复制配置模板：

```bash
cp config.example.json config.json
```

2. 填写 `config.json` 里的 SMTP 和收件人。

3. 手动抓数：

```bash
./scripts/fetch_market_data.sh
```

4. 生成日报但不发送：

```bash
python3 -m app.cli run-once --dry-run
```

5. 生成并发送：

```bash
python3 -m app.cli run-once
```

也可以直接跑整条 shell 链路：

```bash
./scripts/run_daily.sh
```

## GitHub 定时执行

仓库已可直接接入 GitHub Actions，workflow 文件为 `.github/workflows/stock-daily-report.yml`。

- 默认 cron: `0 13 * * 1-5`
- 含义: 每个工作日 `21:00 Asia/Shanghai` 执行
- 支持 `workflow_dispatch` 手动触发，且可选 `dry_run`

需要配置以下 GitHub Secrets：

- `STOCK_REPORT_SMTP_HOST`
- `STOCK_REPORT_SMTP_PORT`
- `STOCK_REPORT_SMTP_USERNAME`
- `STOCK_REPORT_SMTP_PASSWORD`
- `STOCK_REPORT_SMTP_SENDER`
- `STOCK_REPORT_RECIPIENTS_JSON`，示例：`["a@example.com","b@example.com"]`

可选 GitHub Variables：

- `STOCK_REPORT_NAME`
- `STOCK_REPORT_SMTP_USE_SSL`
- `STOCK_REPORT_SMTP_STARTTLS`
- `STOCK_REPORT_USER_AGENT`

注意：

- `config.json`、`data/raw/`、`data/*.db`、`output/*.html` 已加入 `.gitignore`
- GitHub runner 默认不保留本地 SQLite 和原始抓取数据，因此邮件发送日志不会自动跨天保留
- 每次执行完成后会上传当次生成的 HTML 报告 artifact，便于回看

## 常用命令

```bash
python3 -m app.cli enrich-raw
python3 -m app.cli ingest
python3 -m app.cli report
python3 -m app.cli run-once --dry-run
python3 -m app.cli run-once
python3 -m app.cli schedule
```

## 数据源

- 东方财富：`1/3/5/10` 日公开接口
- 东方财富历史累计：基于板块历史资金流接口汇总 `20` 个交易日
- 同花顺：`1/3/5/10/20` 日公开页面
- 同花顺概念详情页：用于 Top10 概念成分股补充

## 已知限制

- 东方财富主榜接口 `push2.eastmoney.com` 在不同网络环境下可能被服务端直接断开连接；遇到这种情况，日报会自动降级为“同花顺可用数据 + 顶部告警”
- 当前环境里 Python 对部分站点存在 DNS 解析差异，因此东方财富 `20` 日历史累计和同花顺成分股派生能力可能受网络环境影响
- Top10 概念成分股当前取同花顺概念详情页首页前十；若概念名无法匹配，会在报告中标注缺失
- 基金关联首版以 ETF/指数基金优先，依赖 `data/fund_links.json` 关键词映射
