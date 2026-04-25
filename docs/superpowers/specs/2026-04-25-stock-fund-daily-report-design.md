# 股票基金日报推送设计

**日期**: 2026-04-25

## 目标

每天 21:00 生成并发送一封股票/基金日报邮件，内容包含：

- 概念板块资金流入 `当日 Top10`
- 概念板块资金流入 `近 3 日 Top10`
- 概念板块资金流入 `近 5 日 Top10`
- 概念板块资金流入 `近 10 日 Top10`
- 概念板块资金流入 `近 20 日 Top10`
- 热门板块分析
- 每个热点板块关联 ETF/基金列表

## 数据源结论

### 主源：东方财富

公开页面：`https://data.eastmoney.com/bkzj/gn.html`

页面前端脚本 `https://data.eastmoney.com/newstatic/js/bkzj/list.js` 暴露出公开接口：

- `https://push2.eastmoney.com/api/qt/clist/get`

已确认可直接返回 JSON，且支持概念板块资金字段：

- `1 日`
- `3 日`
- `5 日`
- `10 日`

概念板块筛选参数：

- `fs=m:90+t:3`

已确认 `statvalues` 仅包含 `1/3/5/10`，未发现 `20 日` 公开字段。

### 20 日补源：同花顺

候选页面：

- `https://data.10jqka.com.cn/funds/gnzjl/board/20/field/tradezdf/order/desc/page/1/free/1/`

验证结果：

- 直接请求 `ajax` 路径会被拦或跳转
- 普通页面路径在带浏览器 UA 时可返回完整 HTML
- 同花顺存在一定反爬和页面结构变动风险

因此 `20 日` 设计为：

1. 优先尝试同花顺 HTML 解析
2. 若抓取失败，使用本地累计历史计算的 `20 个交易日累计净流入 Top10`
3. 报表中明确标记当前 `20 日` 来源

## 总体方案

采用 `定时抓数 + 本地落库 + 分析 + HTML 邮件`。

系统拆分为 6 个模块：

1. `source adapters`
   - 东方财富概念板块资金流
   - 同花顺 20 日概念板块资金流补源
2. `storage`
   - SQLite 保存每日抓取快照、关联基金、发送记录
3. `analyzer`
   - 生成各周期 Top10
   - 计算持续热门、趋势升温、新热点
4. `fund matcher`
   - 根据板块关键词和人工覆盖表匹配 ETF/基金
5. `report renderer`
   - 生成 HTML 邮件
6. `scheduler/runner`
   - 每日 21:00 运行

## 数据模型

### `sector_flow_snapshots`

每条记录表示某日、某周期、某板块的一次资金流快照。

字段：

- `trade_date`
- `window_days`
- `source`
- `sector_code`
- `sector_name`
- `latest_index_value`
- `pct_change`
- `main_net_inflow`
- `main_net_inflow_ratio`
- `super_order_inflow`
- `super_order_ratio`
- `large_order_inflow`
- `large_order_ratio`
- `medium_order_inflow`
- `medium_order_ratio`
- `small_order_inflow`
- `small_order_ratio`
- `leader_stock_name`
- `leader_stock_code`
- `leader_stock_pct_change`
- `rank_no`
- `raw_payload`

### `sector_fund_links`

板块与基金关联表。

字段：

- `sector_name`
- `fund_code`
- `fund_name`
- `fund_type`
- `match_source`
- `priority`
- `note`

### `email_send_logs`

记录发送时间、收件人、主题、状态、异常信息。

## 热门分析口径

### 持续热门

满足以下条件之一：

- 同时进入 `1/3/5/10` 四个窗口 Top10
- 在 `3/5/10/20` 中至少进入三个窗口 Top10

### 新热点

满足以下条件：

- `1 日` 和 `3 日` 进入 Top10
- `10 日` 或 `20 日` 未进入 Top10

### 趋势强化

满足以下条件：

- `3 日` 排名优于 `10 日`
- 且 `3 日` 主力净流入显著为正

### 趋势钝化

满足以下条件：

- `10 日` 或 `20 日` 仍在 Top10
- 但 `1 日` 已跌出 Top10 或资金明显转弱

## 基金关联策略

先做 `ETF/指数基金优先`，主动基金只做补充。

匹配顺序：

1. 人工覆盖映射表
2. 基金名称关键词匹配
3. 指数名称关键词匹配

每个热点板块默认展示：

- `2-3` 只最相关 ETF/指数基金

## 邮件结构

1. 顶部摘要
   - 日期
   - 今日最强板块
   - 持续热门板块
   - 新热点板块
2. 当日 Top10
3. 近 3 日 Top10
4. 近 5 日 Top10
5. 近 10 日 Top10
6. 近 20 日 Top10
7. 热门分析
8. 热点板块关联 ETF/基金
9. 数据来源与说明

## 调度与运行

默认时区：

- `Asia/Shanghai`

默认发送时间：

- `21:00`

运行方式优先级：

1. 手动执行 CLI
2. 常驻调度进程
3. macOS `launchd` 模板

## 错误处理

- 主源东方财富失败：本次任务直接失败并记录日志
- 同花顺 `20 日` 失败：退回本地累计 20 个交易日口径
- 基金关联失败：邮件继续发送，仅该板块基金区域标记“暂无匹配”
- 邮件发送失败：写日志并保留 HTML 产物

## MVP 范围

首版交付：

- 东方财富 `1/3/5/10` 直连抓取
- 同花顺 `20 日` HTML 抓取尝试
- SQLite 落库
- 热门分析
- HTML 邮件生成与 SMTP 发送
- CLI 手动运行
- 调度入口

暂不做：

- Web 管理后台
- 用户多账户订阅
- 消息机器人多渠道
- 大规模告警和监控

## 明确不做

- 不做股票买卖建议
- 不做收益承诺
- 不做自动下单

## TODO

- TODO: 确认同花顺 `20 日` 页面表格是否稳定出现在 HTML 中，还是需额外跟随页面 JS 请求
- TODO: 确认首版基金池范围是只保留 ETF/指数基金，还是允许补充少量主动基金
