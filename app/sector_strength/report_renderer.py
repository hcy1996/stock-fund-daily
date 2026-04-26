from __future__ import annotations

from html import escape

from app.sector_strength.scoring_model import WEIGHTS


GRADE_LEGEND = (
    ("S", "极强", ">= 85", "高景气强趋势"),
    ("A", "强势", "75 - 84.99", "趋势占优可跟踪"),
    ("B", "偏强", "60 - 74.99", "有结构亮点"),
    ("C", "震荡", "45 - 59.99", "观察等待确认"),
    ("D", "弱势", "< 45", "风险收益比偏弱"),
    ("NA", "待定", "null", "关键指标不足"),
)


def _fmt_num(value: float | None, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "null"
    return f"{value:.{digits}f}{suffix}"


def _fmt_ratio(value: float | None) -> str:
    if value is None:
        return "null"
    return f"{value * 100:.2f}%"


def _fmt_raw_score(value: float | None) -> str:
    if value is None:
        return "null"
    return f"{value:.4f}"


def _score_points(value: float | None) -> float | None:
    if value is None:
        return None
    return value * 100


def _grade_class(code: str) -> str:
    return f"grade-{code.lower()}"


def _metric(label: str, value: str) -> str:
    return (
        "<div class='metric'>"
        f"<span class='metric-label'>{escape(label)}</span>"
        f"<strong class='metric-value'>{escape(value)}</strong>"
        "</div>"
    )


def _render_grade_legend() -> str:
    items = []
    for code, label, score_range, desc in GRADE_LEGEND:
        items.append(
            f"""
            <div class="legend-card {_grade_class(code)}">
              <div class="legend-head">
                <span class="grade-badge">{escape(code)}</span>
                <strong>{escape(label)}</strong>
              </div>
              <div class="legend-range">{escape(score_range)}</div>
              <div class="legend-desc">{escape(desc)}</div>
            </div>
            """
        )
    return "<section class='legend-grid'>" + "".join(items) + "</section>"


def _render_overview_cards(payload: dict) -> str:
    results = payload["results"]
    graded = [item for item in results if item.score.total_score is not None]
    buy_count = sum(1 for item in results if item.score.suggestion == "买入")
    watch_count = sum(1 for item in results if item.score.suggestion == "观察")
    avoid_count = sum(1 for item in results if item.score.suggestion == "回避")
    top_score = graded[0].score.total_score if graded else None
    top_name = graded[0].indicators.board_name if graded else "无"
    cards = [
        ("候选池板块数", str(payload["candidate_pool"]["size"])),
        ("概念 / 行业", f"{payload['candidate_pool']['concept_size']} / {payload['candidate_pool']['industry_size']}"),
        ("可评分板块数", str(len(graded))),
        ("最高分板块", f"{top_name} / {_fmt_num(top_score)}"),
        ("买入 / 观察 / 回避", f"{buy_count} / {watch_count} / {avoid_count}"),
    ]
    return "<section class='overview-grid'>" + "".join(_metric(label, value) for label, value in cards) + "</section>"


def _render_ranking_table(payload: dict) -> str:
    rows = []
    for index, item in enumerate(payload["results"], start=1):
        leader = item.indicators.leader_candidate.stock_name if item.indicators.leader_candidate else "-"
        detail = (
            f"资 {_fmt_raw_score(item.score.sub_scores.get('fund_score'))} / "
            f"RS {_fmt_raw_score(item.score.sub_scores.get('RS_score'))} / "
            f"趋 {_fmt_raw_score(item.score.sub_scores.get('trend_score'))} / "
            f"广 {_fmt_raw_score(item.score.sub_scores.get('breadth_score'))} / "
            f"量 {_fmt_raw_score(item.score.sub_scores.get('volume_score'))} / "
            f"龙 {_fmt_raw_score(item.score.sub_scores.get('leader_score'))} / "
            f"罚 {_fmt_raw_score(item.score.risk_penalty)}"
        )
        rows.append(
            f"""
            <tr>
              <td>{index}</td>
              <td>{escape(item.indicators.board_name)}</td>
              <td>{escape(item.score.grade_code)}</td>
              <td>{escape(_fmt_num(item.score.total_score))}</td>
              <td>{escape(item.score.suggestion)}</td>
              <td>{escape(_fmt_num(item.indicators.rs_20d))}</td>
              <td>{escape(_fmt_num(item.indicators.inflow_10d_ratio, suffix='%'))}</td>
              <td>{escape(detail)}</td>
              <td>{escape(leader)}</td>
            </tr>
            """
        )
    return f"""
    <section class="panel">
      <div class="panel-head">
        <h2>板块评分排名</h2>
        <p>按总分从高到低排序</p>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>排名</th>
              <th>板块</th>
              <th>等级</th>
              <th>评分</th>
              <th>建议</th>
              <th>RS20</th>
              <th>10日净流入占比</th>
              <th>评分详情</th>
              <th>龙头候选</th>
            </tr>
          </thead>
          <tbody>
            {''.join(rows)}
          </tbody>
        </table>
      </div>
    </section>
    """


def _render_focus_cards(payload: dict) -> str:
    focus_names = set(payload["focus_board_names"])
    results = payload["results"] if not focus_names else [
        item for item in payload["results"] if item.indicators.board_name in focus_names
    ]
    if not results:
        results = payload["results"][:10]

    cards = []
    for item in results:
        indicators = item.indicators
        score = item.score
        leader = indicators.leader_candidate
        leader_text = "-"
        if leader is not None:
            leader_text = (
                f"{leader.stock_name}({leader.stock_code}) / "
                f"{_fmt_num(leader.pct_change, suffix='%')} / "
                f"{_fmt_num(leader.turnover_rate, suffix='%')}"
            )
        risk_html = ""
        if score.risk_flags:
            risk_html = "<div class='note danger'><strong>风险提示：</strong>" + "；".join(
                escape(flag) for flag in score.risk_flags
            ) + "</div>"
        warning_html = ""
        if indicators.warnings:
            warning_html = "<div class='note warn'><strong>数据告警：</strong>" + "；".join(
                escape(flag) for flag in indicators.warnings
            ) + "</div>"
        breakdown_rows = []
        breakdown_labels = {
            "fund_score": "资金持续性",
            "RS_score": "相对强度RS",
            "trend_score": "趋势结构",
            "breadth_score": "板块广度",
            "volume_score": "成交活跃度",
            "leader_score": "龙头质量",
        }
        for key in [
            "fund_score",
            "RS_score",
            "trend_score",
            "breadth_score",
            "volume_score",
            "leader_score",
        ]:
            raw_score = score.sub_scores.get(key)
            contribution = None
            if raw_score is not None:
                contribution = raw_score * WEIGHTS[key] * 100
            breakdown_rows.append(
                f"""
                <tr>
                  <td>{escape(breakdown_labels[key])}</td>
                  <td>{escape(_fmt_raw_score(raw_score))}</td>
                  <td>{escape(f"{WEIGHTS[key] * 100:.0f}%")}</td>
                  <td>{escape(_fmt_num(contribution))}</td>
                </tr>
                """
            )
        breakdown_rows.append(
            f"""
            <tr>
              <td>风险惩罚</td>
              <td>{escape(_fmt_raw_score(score.risk_penalty))}</td>
              <td>-</td>
              <td>{escape(_fmt_num(_score_points(score.risk_penalty)))} </td>
            </tr>
            """
        )
        cards.append(
            f"""
            <article class="board-card">
              <div class="board-card-head">
                <div>
                  <h3>{escape(indicators.board_name)}</h3>
                  <p>{escape(indicators.board_code or '-')} / {escape(indicators.board_type or '-')}</p>
                </div>
                <div class="score-box {_grade_class(score.grade_code)}">
                  <span class="grade-badge">{escape(score.grade_code)}</span>
                  <strong>{escape(_fmt_num(score.total_score))}</strong>
                  <small>{escape(score.grade_label)} / {escape(score.suggestion)}</small>
                </div>
              </div>
              <p class="grade-desc">{escape(score.grade_description)}</p>
              <div class="metric-grid">
                {_metric('5日涨幅', _fmt_num(indicators.board_return_5d, suffix='%'))}
                {_metric('20日涨幅', _fmt_num(indicators.board_return_20d, suffix='%'))}
                {_metric('RS_5日', _fmt_num(indicators.rs_5d))}
                {_metric('RS_20日', _fmt_num(indicators.rs_20d))}
                {_metric('量比20日', _fmt_num(indicators.amount_ratio_20d))}
                {_metric('5日净流入占比', _fmt_num(indicators.inflow_5d_ratio, suffix='%'))}
                {_metric('10日净流入占比', _fmt_num(indicators.inflow_10d_ratio, suffix='%'))}
                {_metric('上涨家数占比', _fmt_ratio(indicators.up_ratio))}
                {_metric('>3%占比', _fmt_ratio(indicators.gt3_ratio))}
                {_metric('>5%占比', _fmt_ratio(indicators.gt5_ratio))}
                {_metric('涨停密度', _fmt_ratio(indicators.limit_up_density))}
                {_metric('龙头候选', leader_text)}
              </div>
              <div class="metric-grid metric-grid-compact">
                {_metric('MA5', _fmt_num(indicators.ma5))}
                {_metric('MA10', _fmt_num(indicators.ma10))}
                {_metric('MA20', _fmt_num(indicators.ma20))}
                {_metric('MA60', _fmt_num(indicators.ma60))}
                {_metric('风险惩罚', _fmt_num(_score_points(score.risk_penalty)))}
                {_metric('龙头索引', 'null' if score.leader_stock_index is None else str(score.leader_stock_index))}
              </div>
              <div class="breakdown">
                <div class="breakdown-head">
                  <strong>打分拆解</strong>
                  <span>基准分 {_fmt_num(_score_points(score.weighted_score))} / 最终分 {_fmt_num(score.total_score)}</span>
                </div>
                <div class="table-wrap">
                  <table class="breakdown-table">
                    <thead>
                      <tr>
                        <th>维度</th>
                        <th>子分(0~1)</th>
                        <th>权重</th>
                        <th>贡献分</th>
                      </tr>
                    </thead>
                    <tbody>
                      {''.join(breakdown_rows)}
                    </tbody>
                  </table>
                </div>
              </div>
              {risk_html}
              {warning_html}
            </article>
            """
        )
    return f"""
    <section class="panel">
      <div class="panel-head">
        <h2>板块指标明细</h2>
        <p>重点板块优先展示；无输入时展示前 10 名</p>
      </div>
      <div class="board-list">
        {''.join(cards)}
      </div>
    </section>
    """


def _render_warning_block(payload: dict) -> str:
    if not payload["warnings"]:
        return ""
    items = "".join(f"<li>{escape(item)}</li>" for item in payload["warnings"])
    return f"""
    <section class="panel warning-panel">
      <div class="panel-head">
        <h2>全局告警</h2>
        <p>候选池和数据源降级信息</p>
      </div>
      <ul class="warning-list">{items}</ul>
    </section>
    """


def render_html_report(payload: dict) -> str:
    title = "A股板块波段强度评分报告"
    focus_text = "、".join(payload["focus_inputs"]) if payload["focus_inputs"] else "候选池全部板块"
    return f"""
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
      <meta charset="utf-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1" />
      <title>{escape(title)}</title>
      <style>
        :root {{
          --bg: #f4f1e8;
          --panel: #fffdf9;
          --ink: #1f2937;
          --muted: #6b7280;
          --line: #e5ded0;
          --accent: #124559;
          --accent-soft: #d9ebe8;
          --danger: #b42318;
          --warn: #b54708;
          --s: #7c2d12;
          --a: #0f766e;
          --b: #2563eb;
          --c: #a16207;
          --d: #7f1d1d;
          --na: #6b7280;
        }}
        * {{ box-sizing: border-box; }}
        body {{
          margin: 0;
          font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
          color: var(--ink);
          background:
            radial-gradient(circle at top left, #fff4d6 0, transparent 32%),
            radial-gradient(circle at top right, #dbeafe 0, transparent 28%),
            linear-gradient(180deg, #f8f6ef 0%, var(--bg) 100%);
        }}
        .container {{ max-width: 1320px; margin: 0 auto; padding: 32px 20px 56px; }}
        .hero {{
          background: linear-gradient(135deg, rgba(18,69,89,0.96), rgba(27,94,32,0.84));
          color: white;
          border-radius: 24px;
          padding: 28px;
          box-shadow: 0 20px 60px rgba(18,69,89,0.16);
          margin-bottom: 24px;
        }}
        .hero h1 {{ margin: 0 0 10px; font-size: 34px; }}
        .hero p {{ margin: 6px 0; opacity: 0.92; }}
        .overview-grid, .legend-grid {{
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
          gap: 14px;
          margin-bottom: 24px;
        }}
        .metric, .legend-card, .panel, .board-card {{
          background: var(--panel);
          border: 1px solid var(--line);
          border-radius: 18px;
        }}
        .metric {{
          padding: 16px 18px;
          display: flex;
          flex-direction: column;
          gap: 8px;
        }}
        .metric-label {{ font-size: 12px; color: var(--muted); }}
        .metric-value {{ font-size: 22px; line-height: 1.2; }}
        .legend-card {{ padding: 16px; }}
        .legend-head {{
          display: flex;
          align-items: center;
          gap: 10px;
          margin-bottom: 8px;
        }}
        .legend-range {{ font-weight: 700; margin-bottom: 4px; }}
        .legend-desc {{ color: var(--muted); font-size: 13px; }}
        .grade-badge {{
          display: inline-flex;
          align-items: center;
          justify-content: center;
          min-width: 30px;
          height: 30px;
          padding: 0 8px;
          border-radius: 999px;
          color: white;
          font-weight: 800;
          font-size: 13px;
        }}
        .grade-s .grade-badge {{ background: var(--s); }}
        .grade-a .grade-badge {{ background: var(--a); }}
        .grade-b .grade-badge {{ background: var(--b); }}
        .grade-c .grade-badge {{ background: var(--c); }}
        .grade-d .grade-badge {{ background: var(--d); }}
        .grade-na .grade-badge {{ background: var(--na); }}
        .panel {{ padding: 20px; margin-bottom: 24px; }}
        .panel-head {{
          display: flex;
          justify-content: space-between;
          gap: 12px;
          align-items: flex-end;
          margin-bottom: 16px;
          flex-wrap: wrap;
        }}
        .panel-head h2 {{ margin: 0; font-size: 24px; }}
        .panel-head p {{ margin: 0; color: var(--muted); }}
        .table-wrap {{ overflow: auto; }}
        table {{ width: 100%; border-collapse: collapse; min-width: 860px; }}
        th, td {{
          padding: 12px 10px;
          border-bottom: 1px solid var(--line);
          text-align: left;
          white-space: nowrap;
          font-size: 14px;
        }}
        thead th {{
          font-size: 12px;
          letter-spacing: 0.04em;
          color: var(--muted);
          text-transform: uppercase;
        }}
        tbody tr:hover {{ background: rgba(18,69,89,0.04); }}
        .board-list {{
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
          gap: 18px;
        }}
        .board-card {{ padding: 18px; }}
        .board-card-head {{
          display: flex;
          justify-content: space-between;
          gap: 12px;
          align-items: flex-start;
          margin-bottom: 10px;
        }}
        .board-card-head h3 {{ margin: 0 0 4px; font-size: 22px; }}
        .board-card-head p {{ margin: 0; color: var(--muted); font-size: 13px; }}
        .score-box {{
          min-width: 112px;
          padding: 10px 12px;
          border-radius: 16px;
          color: white;
          display: flex;
          flex-direction: column;
          align-items: flex-end;
          gap: 4px;
        }}
        .score-box.grade-s {{ background: linear-gradient(135deg, var(--s), #ea580c); }}
        .score-box.grade-a {{ background: linear-gradient(135deg, var(--a), #14b8a6); }}
        .score-box.grade-b {{ background: linear-gradient(135deg, var(--b), #60a5fa); }}
        .score-box.grade-c {{ background: linear-gradient(135deg, var(--c), #f59e0b); }}
        .score-box.grade-d {{ background: linear-gradient(135deg, var(--d), #ef4444); }}
        .score-box.grade-na {{ background: linear-gradient(135deg, var(--na), #9ca3af); }}
        .score-box strong {{ font-size: 26px; line-height: 1; }}
        .score-box small {{ opacity: 0.92; }}
        .grade-desc {{ margin: 0 0 14px; color: var(--muted); }}
        .metric-grid {{
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 10px;
          margin-bottom: 12px;
        }}
        .metric-grid .metric {{ padding: 12px 14px; }}
        .metric-grid-compact {{ grid-template-columns: repeat(5, minmax(0, 1fr)); }}
        .note {{
          margin-top: 10px;
          padding: 12px 14px;
          border-radius: 14px;
          font-size: 13px;
          line-height: 1.6;
        }}
        .note.warn {{ background: #fff7ed; color: var(--warn); }}
        .note.danger {{ background: #fff1f2; color: var(--danger); }}
        .warning-panel {{ background: #fffaf2; }}
        .warning-list {{
          margin: 0;
          padding-left: 18px;
          color: var(--warn);
          line-height: 1.7;
        }}
        .breakdown {{
          margin-top: 12px;
          padding: 12px 14px;
          background: rgba(18,69,89,0.035);
          border-radius: 14px;
          border: 1px solid rgba(18,69,89,0.08);
        }}
        .breakdown-head {{
          display: flex;
          justify-content: space-between;
          gap: 12px;
          flex-wrap: wrap;
          margin-bottom: 10px;
          font-size: 13px;
          color: var(--muted);
        }}
        .breakdown-table {{
          min-width: 0;
        }}
        .breakdown-table th,
        .breakdown-table td {{
          padding: 8px 6px;
          font-size: 13px;
        }}
        @media (max-width: 900px) {{
          .hero h1 {{ font-size: 28px; }}
          .metric-grid, .metric-grid-compact {{ grid-template-columns: 1fr; }}
          .board-card-head {{ flex-direction: column; }}
          .score-box {{ align-items: flex-start; }}
        }}
      </style>
    </head>
    <body>
      <main class="container">
        <section class="hero">
          <h1>{escape(title)}</h1>
          <p>生成时间：{escape(payload["generated_at"])}</p>
          <p>候选池：{escape(str(payload["candidate_pool"]["trade_date"]))} / {escape(str(payload["candidate_pool"]["source"]))} / window={escape(str(payload["candidate_pool"]["window_days"]))}</p>
          <p>分析范围：{escape(focus_text)}</p>
        </section>
        {_render_overview_cards(payload)}
        {_render_grade_legend()}
        {_render_warning_block(payload)}
        {_render_ranking_table(payload)}
        {_render_focus_cards(payload)}
      </main>
    </body>
    </html>
    """
