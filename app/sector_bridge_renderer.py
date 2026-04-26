from __future__ import annotations

from html import escape


def _fmt_num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "null"
    return f"{value:.{digits}f}"


def _fmt_pct_value(value: float | None) -> str:
    if value is None:
        return "null"
    return f"{value:.2f}%"


def _render_summary_cards(bridge_payload: dict) -> str:
    cards = []
    for item in bridge_payload.get("summary_cards", []):
        cards.append(
            f"""
            <div class="summary-card">
              <h3>{escape(str(item.get('label', '')))}</h3>
              <div class="bridge-stat">{escape(str(item.get('value', 'null')))}</div>
            </div>
            """
        )
    return "<div class='summary-grid'>" + "".join(cards) + "</div>"


def _render_fund_list(related_funds: list[dict]) -> str:
    if not related_funds:
        return "<p class='empty-text'>暂无关联基金</p>"
    items = []
    for item in related_funds:
        reason_labels = []
        if "catalog_match" in item.get("reasons", []):
            reason_labels.append("主题匹配")
        if "holding_overlap" in item.get("reasons", []):
            reason_labels.append("持仓重叠")
        overlaps = "、".join(item.get("overlap_stocks", []))
        detail = " / ".join(reason_labels) if reason_labels else "规则关联"
        if overlaps:
            detail += f" / 重叠持仓: {overlaps}"
        items.append(
            f"<li><strong>{escape(str(item.get('fund_name', '-')))}</strong>"
            f"（{escape(str(item.get('fund_code', '-')))} / {escape(str(item.get('fund_type') or '-'))}）"
            f"<span class='bridge-fund-detail'>{escape(detail)}</span></li>"
        )
    return "<ul>" + "".join(items) + "</ul>"


def _render_focus_sector_cards(bridge_payload: dict) -> str:
    cards = []
    for item in bridge_payload.get("focus_sector_cards", []):
        leader = item.get("leader_candidate") or {}
        risk_flags = item.get("risk_flags", [])
        warnings = item.get("warnings", [])
        cards.append(
            f"""
            <div class="component-card bridge-sector-card">
              <div class="component-head">{escape(str(item.get('sector_name', '-')))}</div>
              <p class="fund-meta">等级={escape(str(item.get('grade_code') or 'NA'))} / 评分={escape(_fmt_num(item.get('score')))} / 建议={escape(str(item.get('suggestion') or '观察'))}</p>
              <p class="fund-meta">RS20={escape(_fmt_num(item.get('rs_20d')))} / 10日净流入占比={escape(_fmt_pct_value(item.get('inflow_10d_ratio')))} / 量比20日={escape(_fmt_num(item.get('amount_ratio_20d')))}</p>
              <p class="fund-meta">子分: 资 {escape(_fmt_num((item.get('sub_scores') or {}).get('fund_score'), 4))} / RS {escape(_fmt_num((item.get('sub_scores') or {}).get('RS_score'), 4))} / 趋 {escape(_fmt_num((item.get('sub_scores') or {}).get('trend_score'), 4))} / 广 {escape(_fmt_num((item.get('sub_scores') or {}).get('breadth_score'), 4))} / 量 {escape(_fmt_num((item.get('sub_scores') or {}).get('volume_score'), 4))} / 龙 {escape(_fmt_num((item.get('sub_scores') or {}).get('leader_score'), 4))}</p>
              <p class="fund-meta">龙头: {escape(str(leader.get('stock_name') or '-'))} / 涨幅={escape(_fmt_pct_value(leader.get('pct_change')))} / 换手率={escape(_fmt_pct_value(leader.get('turnover_rate')))}</p>
              {_render_fund_list(item.get('related_funds', []))}
              {"<p class='note'>风险提示：" + escape("；".join(risk_flags)) + "</p>" if risk_flags else ""}
              {"<p class='note'>数据告警：" + escape("；".join(warnings)) + "</p>" if warnings else ""}
            </div>
            """
        )
    if not cards:
        return "<p class='empty-text'>暂无可展示的板块强度解释层</p>"
    return "<div class='bridge-sector-list'>" + "".join(cards) + "</div>"


def _render_fund_to_sector_links(bridge_payload: dict) -> str:
    rows = []
    for item in bridge_payload.get("fund_to_sector_links", [])[:12]:
        sector_text = "；".join(
            f"{sector['sector_name']}({sector.get('grade_code') or 'NA'} / {_fmt_num(sector.get('score'))})"
            for sector in item.get("sectors", [])[:4]
        )
        rows.append(
            f"""
            <tr>
              <td>{escape(str(item.get('fund_name') or '-'))}</td>
              <td>{escape(str(item.get('fund_code') or '-'))}</td>
              <td>{escape(str(item.get('fund_type') or '-'))}</td>
              <td>{escape(sector_text or '暂无')}</td>
            </tr>
            """
        )
    if not rows:
        return "<p class='empty-text'>暂无基金到板块映射</p>"
    return f"""
    <div class="bridge-fund-table-wrap">
    <table>
      <thead>
        <tr>
          <th>基金</th>
          <th>代码</th>
          <th>类型</th>
          <th>关联强势板块</th>
        </tr>
      </thead>
      <tbody>
        {''.join(rows)}
      </tbody>
    </table>
    </div>
    """


def render_sector_bridge_section(bridge_payload: dict) -> str:
    if not bridge_payload.get("available"):
        warnings = "；".join(bridge_payload.get("warnings", []))
        return f"""
        <section id="sector-bridge" class="section-card anchor-section">
          <div class="section-head">
            <h2>板块强度解释层</h2>
            <span class="source">规则层</span>
          </div>
          <p class="empty-text">板块强度结果本次不可用。</p>
          {f"<p class='note'>{escape(warnings)}</p>" if warnings else ""}
        </section>
        """

    return f"""
    <section id="sector-bridge" class="section-card anchor-section">
      <div class="section-head">
        <h2>板块强度解释层</h2>
        <span class="source">基金主视角 + 板块强度规则桥接</span>
      </div>
      {_render_summary_cards(bridge_payload)}
      <div class="bridge-two-col">
        <div class="bridge-column">
          <div class="section-head">
            <h2>板块解读</h2>
          </div>
          {_render_focus_sector_cards(bridge_payload)}
        </div>
        <div class="bridge-column">
          <div class="section-head">
            <h2>基金解读</h2>
          </div>
          {_render_fund_to_sector_links(bridge_payload)}
        </div>
      </div>
    </section>
    """
