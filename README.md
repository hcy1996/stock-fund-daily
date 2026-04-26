# 股票基金日报

本项目用于每天 21:00 生成并发送一封股票/基金日报邮件，核心内容是概念板块资金流入 Top10 与热点板块关联 ETF/基金。

当前日报默认按双数据源并排展示：

- 东方财富 `1/3/5/10` 日公开接口 + 东方财富 `20` 日历史累计
- 同花顺 `1/3/5/10/20` 日公开页面
- 邮件正文直接展示双榜单和差异摘要
- 天天基金开放式基金当日、近一周、近一月排行榜
- 日报正文集成板块强度解释层与基金-板块 AI 综合解读
- 若某一数据源抓取失败，邮件仍会发送，并在顶部显式告警

## 目录

- `scripts/fetch_market_data.sh`: 抓取东方财富和同花顺原始数据
- `python3 -m app.cli enrich-raw`: 基于 `raw` 数据补充东方财富 `20 日累计` 和 Top10 概念成分股
- `scripts/run_daily.sh`: 一次性跑完整链路
- `app/`: 解析、落库、分析、渲染、发送邮件
- `data/`: SQLite、原始抓取数据、基金映射
- `output/`: 生成的 HTML 日报
- `reports/history/`: 按交易日保存的 AI prompt / AI 分析 / 周综合分析快照
- `ops/com.codex.stock-daily-report.plist.example`: macOS `launchd` 模板

## 快速开始

1. 复制配置模板：

```bash
cp config.example.json config.json
```

2. 填写 `config.json` 里的 SMTP 和收件人。

2.5 安装依赖（新板块评分功能依赖 `akshare` 与 `pandas`）：

```bash
python3 -m pip install akshare pandas
```

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

## AI 归类参考

支持可选接入 OpenAI 兼容接口，为统计模块补两段 AI 内容：

- 默认关闭
- 开启后会在报告顶部增加 `AI 当日归类参考`
- 若板块强度整合成功，还会增加 `AI 基金-板块综合解读`
- 同时会基于最近 `7` 个交易日的历史快照生成 `AI 近一周综合分析`
- 只输出观察和风险提示，不应作为投资决策依据

启用后，程序会额外保留以下历史：

- `data/archive/raw/<YYYY-MM-DD>/`：当天抓取的原始文件快照，仅本地保留
- `output/<YYYY-MM-DD>-*.txt|json`：当天 AI prompt、AI 结果与分析快照
- `output/ai-calls/<YYYY-MM-DD>/`：每次 AI 调用的本地日志，包含 `prompt`、原始响应、提取文本、`response_id`
- `reports/history/<YYYY-MM-DD>/`：会进入仓库的轻量历史快照，供后续近一周综合分析复用

如果使用火山方舟兼容接口，拿到 AI 返回的 `response_id` 后，可用下面的方式回查历史响应详情：

```bash
curl --location "https://ark.cn-beijing.volces.com/api/v3/responses/<response_id>" \
  --header "Authorization: Bearer <api_key>" \
  --header "Content-Type: application/json"
```

说明：

- `response_id` 一般来自接口返回的顶层 `id`
- 该请求用于查询历史 AI 对话/响应详情，不会重新生成内容
- 本项目本地 `output/ai-calls/<YYYY-MM-DD>/` 日志里也会保留 `response_id`

配置方式：

```json
{
  "ai": {
    "enabled": true,
    "provider": "openai_compatible",
    "base_url": "https://api.openai.com/v1",
    "api_key": "YOUR_API_KEY",
    "model": "gpt-4.1-mini"
  }
}
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
- `STOCK_REPORT_AI_ENABLED`，示例：`true`
- `STOCK_REPORT_AI_PROVIDER`，示例：`openai_compatible`
- `STOCK_REPORT_AI_BASE_URL`
- `STOCK_REPORT_AI_API_KEY`
- `STOCK_REPORT_AI_MODEL`

可选 GitHub Variables：

- `STOCK_REPORT_NAME`
- `STOCK_REPORT_SMTP_USE_SSL`
- `STOCK_REPORT_SMTP_STARTTLS`
- `STOCK_REPORT_USER_AGENT`
- `STOCK_REPORT_ENABLE_TONGHUASHUN_20D`

注意：

- `config.json`、`data/raw/`、`data/*.db`、`output/*.html` 已加入 `.gitignore`
- GitHub runner 默认不保留本地 SQLite 和原始抓取数据，因此邮件发送日志不会自动跨天保留
- 每次执行完成后会上传当次生成的 HTML 报告 artifact，便于回看
- workflow 还会把当次报告提交到仓库 `reports/` 目录，并同步更新 `reports/latest.html`
- workflow 提交 `reports/` 时，也会一并保存 `reports/history/` 下的 AI 历史快照
- workflow 还会自动发布 GitHub Pages，默认首页为最新一期报告
- 基金排行榜报告会通过独立 workflow 在工作日 `22:30 Asia/Shanghai` 触发，单独发布到 `fund-rank/` 子路径，并发送邮件
- 节假日无法由 GitHub cron 直接识别；当前做法是工作日 cron 触发后先检查排行榜快照日期，若不是当天则自动跳过

如果需要通过网页直接访问 HTML 报告，还需要在仓库设置里确认：

1. `Settings > Pages > Build and deployment > Source` 设为 `GitHub Actions`
2. `Settings > Actions > General > Workflow permissions` 设为 `Read and write permissions`
3. 首次 workflow 成功后，可通过 `https://<owner>.github.io/<repo>/` 访问最新报告
4. 同时保留 `https://<owner>.github.io/<repo>/latest.html` 和 `https://<owner>.github.io/<repo>/<YYYY-MM-DD>-daily-report.html`
5. 基金排行榜单独页面路径为 `https://<owner>.github.io/<repo>/fund-rank/`
6. 同时保留 `https://<owner>.github.io/<repo>/fund-rank/latest.html` 和 `https://<owner>.github.io/<repo>/fund-rank/<YYYY-MM-DD>.html`

## 常用命令

```bash
python3 -m app.cli enrich-raw
python3 -m app.cli ingest
python3 -m app.cli report
python3 -m app.cli run-once --dry-run
python3 -m app.cli run-once
python3 -m app.cli schedule
python3 -m app.cli sector-strength --board 盐湖提锂
python3 -m app.cli sector-strength --board 盐湖提锂 --board 化肥
python3 -m app.cli fund-rank-report
```

## 独立基金排行榜报告

新增一个不影响现有日报链路的独立报告入口：

```bash
python3 -m app.cli fund-rank-report
```

发送邮件：

```bash
python3 -m app.cli fund-rank-report --send-email
```

第一阶段当前包含：

- 天天基金 `今日 / 近一周 / 近一月 / 近三月 / 近六月 / 近一年` 各前 `300`
- 阶段内 `A/C` 去重，优先保留 `C`
- 跨阶段重复出现率分析
- 独立 `HTML / JSON / summary` 输出

输出目录：

- `output/fund-rank/<trade_date>/report.html`
- `output/fund-rank/<trade_date>/result.json`
- `output/fund-rank/<trade_date>/summary.txt`

GitHub Actions：

- workflow：`.github/workflows/fund-rank-report.yml`
- cron：工作日 `22:30 Asia/Shanghai`，对应 `30 14 * * 1-5`
- 调度后会先检查当日基金排行榜快照日期；若不是当天，则自动跳过，不发邮件、不更新 Pages
- GitHub Pages 子路径：`/fund-rank/`

## A股板块波段强度评分

新增一个独立 CLI 功能，不影响现有日报链路。

功能入口：

```bash
python3 -m app.cli sector-strength --board 盐湖提锂
```

也可以同时分析多个输入板块：

```bash
python3 -m app.cli sector-strength --board 盐湖提锂 --board 化肥
```

如果不传 `--board`，程序会直接分析候选池前 `50` 个板块：

```bash
python3 -m app.cli sector-strength
```

候选池规则：

- 默认同时纳入 `概念板块 + 行业板块`
- 概念板块优先使用 SQLite 中最近一次抓取到的前 `N` 个板块
- 优先级：`1日榜 tonghuashun` -> `1日榜 eastmoney` -> `5日榜 tonghuashun` -> `5日榜 eastmoney`
- 行业板块默认实时抓取前 `N` 个候选
- `--candidate-limit` 表示“每类候选池数量”，所以默认总分析范围通常会大于 `50`
- 若本地没有候选池，则实时抓取概念资金流榜单兜底

数据源优先级：

- `AkShare`
- 东方财富公开接口
- 本地 SQLite 快照仅作为资金流候选池和缺失兜底，不会编造字段

输出内容：

- 板块评分排名表
- 每个板块的指标明细
- 六个子分、权重、风险惩罚、等级依据
- 龙头股候选
- 风险提示
- 操作建议：`买入 / 观察 / 回避`
- 可复用 JSON 结构

输出文件：

- `output/sector-strength/<trade_date>/result.json`
- `output/sector-strength/<trade_date>/summary.txt`
- `output/sector-strength/<trade_date>/report.html`

得分等级：

- `S`：`>= 85`，极强，高景气强趋势
- `A`：`75 - 84.99`，强势，趋势占优可跟踪
- `B`：`60 - 74.99`，偏强，有结构亮点
- `C`：`45 - 59.99`，震荡，观察等待确认
- `D`：`< 45`，弱势，风险收益比偏弱
- `NA`：关键指标不足，暂不评级

JSON 里会保留：

- 原始抓数快照标准化结果
- 5日/20日涨幅、RS、均线、广度、活跃度、资金流占比等指标
- 六个子分、风险惩罚、总分、等级、建议
- `warnings` 和 `missing_metrics`

说明：

- 缺失数据统一写成 `null`
- 如果输入板块不在候选池里，程序会尝试按公开板块列表解析后追加分析，并标注该板块不属于候选池
- 候选池当前主要来自概念板块榜单；行业板块支持解析和分析，但默认不主动纳入候选池

## 数据源

- 东方财富：`1/3/5/10` 日公开接口
- 东方财富历史累计：基于板块历史资金流接口汇总 `20` 个交易日
- 天天基金：开放式基金排行 `日增长率/近1周/近1月`
- 同花顺：`1/3/5/10/20` 日公开页面
- 同花顺概念详情页：用于 Top10 概念成分股补充

## 已知限制

- 东方财富主榜接口 `push2.eastmoney.com` 在不同网络环境下可能被服务端直接断开连接；遇到这种情况，日报会自动降级为“同花顺可用数据 + 顶部告警”
- 当前环境里 Python 对部分站点存在 DNS 解析差异，因此东方财富 `20` 日历史累计和同花顺成分股派生能力可能受网络环境影响
- Top10 概念成分股当前取同花顺概念详情页首页前十；若概念名无法匹配，会在报告中标注缺失
- 基金关联首版以 ETF/指数基金优先，依赖 `data/fund_links.json` 关键词映射
