from __future__ import annotations

from html import escape
import re


_STAGE_ORDER_INDEX = {
    "day": 0,
    "week": 1,
    "month": 2,
    "quarter": 3,
    "half_year": 4,
    "year": 5,
}
EASTMONEY_FUND_DETAIL_URL = "https://fund.eastmoney.com/{fund_code}.html"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}%"


def _fmt_rate(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.1f}%"


def _render_repeat_badge(label: str, css_class: str, count: int) -> str:
    return (
        f"<span class='repeat-badge {escape(css_class)}'>"
        f"{escape(label)} · {count}次"
        "</span>"
    )


def _render_fund_link(fund_name: str, fund_code: str) -> str:
    code = fund_code.strip()
    if not re.fullmatch(r"\d{6}", code):
        return (
            f"<div class='fund-name'>{escape(fund_name)}</div>"
            f"<div class='fund-code'>{escape(fund_code)}</div>"
        )
    url = EASTMONEY_FUND_DETAIL_URL.format(fund_code=code)
    return (
        f"<a class='fund-link' href='{escape(url)}' target='_blank' rel='noreferrer noopener'>"
        f"<div class='fund-name'>{escape(fund_name)}</div>"
        f"<div class='fund-code'>{escape(fund_code)}</div>"
        "</a>"
    )


def _render_sector_classification(classification: dict | None) -> str:
    if not classification:
        return "-"

    primary_sector = str(classification.get("primary_sector", "")).strip()
    sub_sector = str(classification.get("sub_sector", "")).strip()
    if not primary_sector and not sub_sector:
        return "-"

    if primary_sector and sub_sector:
        return f"{escape(primary_sector)}/{escape(sub_sector)}"
    return escape(primary_sector or sub_sector or "-")


def _render_warnings(payload: dict) -> str:
    if not payload["warnings"]:
        return ""
    items = "".join(f"<li>{escape(item)}</li>" for item in payload["warnings"])
    return f"<section class='warning-card'><h2>告警</h2><ul>{items}</ul></section>"


def _render_period_section(section: dict, classification_map: dict[str, dict]) -> str:
    rows = []
    for item in section["records"]:
        classification = classification_map.get(item["fund_code"])
        rows.append(
            f"""
            <tr>
              <td>{item['rank_no'] or '-'}</td>
              <td>{_render_fund_link(item['fund_name'], item['fund_code'])}</td>
              <td>{_render_sector_classification(classification)}</td>
              <td>{escape(item['share_class'])}</td>
              <td>{_fmt_pct(item['return_pct'])}</td>
              <td>{_render_repeat_badge(item['repeat_label'], item['repeat_class'], item['appearance_count'])}</td>
            </tr>
            """
        )
    body = "\n".join(rows) if rows else "<tr><td colspan='7'>暂无数据</td></tr>"
    return f"""
    <section class="table-card">
      <div class="section-head">
        <h3>{escape(section['label'])}</h3>
        <span class="source">去重后 {section['record_count']} 条</span>
      </div>
      <div class="table-scroll table-scroll-period">
        <table>
          <thead>
            <tr>
              <th>排名</th>
              <th>基金</th>
              <th>板块分类</th>
              <th>份额</th>
              <th>涨幅</th>
              <th>重复程度</th>
            </tr>
          </thead>
          <tbody>
            {body}
          </tbody>
        </table>
      </div>
    </section>
    """


def _render_repeat_rows(payload: dict) -> str:
    rows = []
    classification_map = payload.get("fund_sector_classifications", {})
    for item in payload["repeat_rows"]:
        stage_text = "、".join(item["appearance_periods"])
        stage_rank_text = " / ".join(
            f"{stage['label']}#{stage['rank_no'] or '-'}({_fmt_pct(stage['return_pct'])})"
            for _, stage in sorted(
                item["stages"].items(),
                key=lambda pair: _STAGE_ORDER_INDEX.get(pair[0], 999),
            )
        )
        classification = classification_map.get(item["fund_code"])
        rows.append(
            f"""
            <tr>
              <td>{_render_fund_link(item['fund_name'], item['fund_code'])}</td>
              <td>{_render_sector_classification(classification)}</td>
              <td>{_render_repeat_badge(item['repeat_label'], item['repeat_class'], item['appearance_count'])}</td>
              <td>{_fmt_rate(item['appearance_rate'])}</td>
              <td>{escape(stage_text)}</td>
              <td>{escape(stage_rank_text)}</td>
            </tr>
            """
        )
    body = "\n".join(rows) if rows else "<tr><td colspan='7'>暂无数据</td></tr>"
    return f"""
    <section class="table-card">
      <div class="section-head">
        <h2>高频重复基金榜</h2>
      </div>
      <div class="table-scroll table-scroll-repeat">
        <table>
          <thead>
            <tr>
              <th>基金</th>
              <th>板块分类</th>
              <th>出现次数</th>
              <th>重复出现率</th>
              <th>出现阶段</th>
              <th>各阶段排名和涨幅</th>
            </tr>
          </thead>
          <tbody>
            {body}
          </tbody>
        </table>
      </div>
    </section>
    """


def _render_sector_frequency_rows(payload: dict) -> str:
    rows = []
    for item in payload.get("sector_frequency_rows", []):
        fund_names = "、".join(item["fund_names"][:4])
        if len(item["fund_names"]) > 4:
            fund_names += " 等"
        rows.append(
            f"""
            <tr>
              <td>{item['rank_no']}</td>
              <td>{escape(item['sub_sector'])}</td>
              <td>{escape(item['primary_sector'])}</td>
              <td>{item['fund_count']}只基金</td>
              <td>{escape(fund_names or '-')}</td>
            </tr>
            """
        )
    body = "\n".join(rows) if rows else "<tr><td colspan='5'>暂无数据</td></tr>"
    return f"""
    <section class="table-card">
      <div class="section-head">
        <h2>高频板块排行</h2>
        <span class="source">按基金板块分类聚合</span>
      </div>
      <div class="table-scroll table-scroll-sector">
        <table>
          <thead>
            <tr>
              <th>排名</th>
              <th>细分赛道</th>
              <th>大类参考</th>
              <th>涉及基金数</th>
              <th>代表基金</th>
            </tr>
          </thead>
          <tbody>
            {body}
          </tbody>
        </table>
      </div>
    </section>
    """


def render_fund_rank_report_html(payload: dict, report_name: str) -> str:
    classification_map = payload.get("fund_sector_classifications", {})
    period_sections_html = "".join(
        _render_period_section(section, classification_map)
        for section in payload["period_sections"]
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(report_name)} | {escape(payload['trade_date'])}</title>
  <style>
    :root {{
      --bg: #f5f7fb;
      --card: #ffffff;
      --text: #162033;
      --muted: #5f6b84;
      --border: #dbe3f0;
      --normal: #eef2f7;
      --active: #d7ebff;
      --rising: #d8f2df;
      --strong: #ffe4b8;
      --hot: #ffd8b5;
      --core: #ffd0d0;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); }}
    main {{ max-width: 1480px; margin: 0 auto; padding: 24px; }}
    h1, h2, h3 {{ margin: 0; }}
    .page-head {{ margin-bottom: 16px; }}
    .page-date {{ margin-bottom: 6px; color: var(--muted); font-size: 13px; }}
    .metric-card, .table-card, .warning-card {{ background: var(--card); border: 1px solid var(--border); border-radius: 16px; box-shadow: 0 10px 30px rgba(22, 32, 51, 0.06); }}
    .metric-card {{ padding: 14px; }}
    .metric-label {{ color: var(--muted); font-size: 12px; margin-bottom: 6px; }}
    .metric-card strong {{ font-size: 22px; }}
    .warning-card {{ padding: 14px 16px; margin-bottom: 16px; }}
    .warning-card ul {{ margin: 10px 0 0; padding-left: 18px; color: #8d4b00; font-size: 13px; }}
    .table-card {{ padding: 14px; margin-bottom: 16px; overflow: hidden; background: linear-gradient(180deg, #ffffff 0%, #fbfdff 100%); }}
    .section-head {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 10px; padding-bottom: 8px; border-bottom: 1px solid #edf2f8; }}
    .source {{ color: var(--muted); font-size: 12px; }}
    .period-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; align-items: start; }}
    table {{ width: 100%; border-collapse: collapse; }}
    .table-scroll {{ overflow: auto; border: 1px solid var(--border); border-radius: 12px; background: #fcfdff; }}
    .table-scroll-period {{ max-height: 360px; }}
    .table-scroll-repeat {{ max-height: 420px; }}
    .table-scroll-sector {{ max-height: 360px; }}
    th, td {{ padding: 9px 8px; border-top: 1px solid var(--border); text-align: left; vertical-align: top; font-size: 13px; line-height: 1.35; }}
    thead th {{ position: sticky; top: 0; z-index: 1; border-top: 0; color: var(--muted); font-size: 12px; background: #f7faff; box-shadow: inset 0 -1px 0 #e6edf6; }}
    tbody tr:first-child td {{ border-top: 0; }}
    tbody tr:nth-child(even) {{ background: #fafcff; }}
    tbody tr:hover {{ background: #f3f8ff; }}
    .repeat-badge {{ display: inline-flex; align-items: center; padding: 3px 8px; border-radius: 999px; font-size: 12px; font-weight: 600; white-space: nowrap; }}
    .repeat-normal {{ background: var(--normal); }}
    .repeat-active {{ background: var(--active); }}
    .repeat-rising {{ background: var(--rising); }}
    .repeat-strong {{ background: var(--strong); }}
    .repeat-hot {{ background: var(--hot); }}
    .repeat-core {{ background: var(--core); }}
    .fund-link {{ display: inline-grid; gap: 2px; color: #1d4ed8; text-decoration: none; }}
    .fund-link:hover .fund-name {{ text-decoration: underline; }}
    .fund-name {{ font-weight: 600; color: #1e293b; }}
    .fund-code {{ color: var(--muted); font-size: 12px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
    @media (max-width: 900px) {{
      main {{ padding: 16px; }}
      .period-grid {{ grid-template-columns: 1fr; }}
      .table-scroll-period,
      .table-scroll-repeat {{ max-height: 320px; }}
    }}
  </style>
</head>
<body>
  <main>
    <section class="page-head">
      <div>
        <div class="page-date">快照日期：{escape(payload['snapshot_date'] or payload['trade_date'])}</div>
        <h1>{escape(report_name)}</h1>
      </div>
    </section>
    {_render_warnings(payload)}
    {_render_sector_frequency_rows(payload)}
    {_render_repeat_rows(payload)}
    <section>
      <div class="section-head">
        <h2>不同阶段基金排行榜</h2>
      </div>
      <div class="period-grid">
        {period_sections_html}
      </div>
    </section>
  </main>
</body>
</html>
"""
