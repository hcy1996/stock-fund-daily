from __future__ import annotations

from collections import Counter, defaultdict
from html import escape
import json
from pathlib import Path
import re

from app.models import MatchedFund, SectorComponentRecord, SectorFlowRecord, WindowSection
from app.raw_enricher import TONGHUASHUN_CONCEPT_DETAIL_URL, TONGHUASHUN_CONCEPT_INDEX_URL
from app.sources.tonghuashun import (
    TONGHUASHUN_10D_URL,
    TONGHUASHUN_1D_URL,
    TONGHUASHUN_20D_URL,
    TONGHUASHUN_3D_URL,
    TONGHUASHUN_5D_URL,
)

WINDOW_LABELS = {
    1: "当日",
    3: "3日",
    5: "5日",
    10: "10日",
    20: "20日",
}
EASTMONEY_CONCEPT_LIST_URL = "https://data.eastmoney.com/bkzj/gn.html"
TONGHUASHUN_CONCEPT_LINK_PATTERN = re.compile(
    r'href="https?://q\.10jqka\.com\.cn/gn/detail/code/(\d+)/"[^>]*>([^<]+)</a>'
)
TONGHUASHUN_WINDOW_URLS = {
    1: TONGHUASHUN_1D_URL,
    3: TONGHUASHUN_3D_URL,
    5: TONGHUASHUN_5D_URL,
    10: TONGHUASHUN_10D_URL,
    20: TONGHUASHUN_20D_URL,
}
TONGHUASHUN_STOCK_URL = "https://stockpage.10jqka.com.cn/{stock_code}/"


def _window_display_title(window_days: int, trade_date: str) -> str:
    if window_days == 1:
        return trade_date
    return f"近 {window_days} 日"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}%"


def _fmt_amount(value: float | None) -> str:
    if value is None:
        return "-"
    abs_value = abs(value)
    if abs_value >= 100000000:
        return f"{value / 100000000:.2f} 亿"
    if abs_value >= 10000:
        return f"{value / 10000:.2f} 万"
    return f"{value:.2f}"


def _build_sector_heat(windows: dict[int, WindowSection]) -> tuple[dict[str, int], dict[str, list[int]]]:
    sector_heat: Counter[str] = Counter()
    sector_windows: dict[str, list[int]] = defaultdict(list)

    for window_days in (1, 3, 5, 10, 20):
        section = windows.get(window_days)
        if not section:
            continue
        for record in section.records:
            sector_heat[record.sector_name] += 1
            sector_windows[record.sector_name].append(window_days)

    return dict(sector_heat), dict(sector_windows)


def _heat_class(heat: int) -> str:
    return f"heat-{max(1, min(heat, 5))}"


def _row_heat_class(heat: int) -> str:
    return f"row-heat-{max(1, min(heat, 5))}"


def _block_heat_class(heat: int) -> str:
    return f"block-heat-{max(1, min(heat, 5))}"


def _clean_sector_name(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip()


def _build_sector_link_map(raw_dir: Path | None) -> dict[str, str]:
    if raw_dir is None:
        return {}

    sector_links: dict[str, str] = {}
    tonghuashun_dir = raw_dir / "tonghuashun"
    for filename in ("1d.html", "3d.html", "5d.html", "10d.html", "20d.html"):
        path = tonghuashun_dir / filename
        if not path.exists():
            continue
        html = path.read_text(encoding="gbk", errors="ignore")
        for concept_code, sector_name in TONGHUASHUN_CONCEPT_LINK_PATTERN.findall(html):
            clean_name = _clean_sector_name(sector_name)
            if not clean_name:
                continue
            sector_links.setdefault(
                clean_name,
                TONGHUASHUN_CONCEPT_DETAIL_URL.format(concept_code=concept_code),
            )

    components_path = tonghuashun_dir / "components_top10.json"
    if components_path.exists():
        payload = json.loads(components_path.read_text(encoding="utf-8"))
        for item in payload.get("sectors", []):
            sector_name = _clean_sector_name(str(item.get("sector_name", "")))
            concept_code = str(item.get("concept_code", "")).strip()
            if sector_name and concept_code:
                sector_links.setdefault(
                    sector_name,
                    TONGHUASHUN_CONCEPT_DETAIL_URL.format(concept_code=concept_code),
                )

    return sector_links


def _build_section_source_url(section: WindowSection, window_days: int) -> str | None:
    source = section.records[0].source if section.records else ""
    if source == "tonghuashun":
        return TONGHUASHUN_WINDOW_URLS.get(window_days)
    if source in {"eastmoney", "eastmoney_history", "local_rollup"}:
        return EASTMONEY_CONCEPT_LIST_URL
    return None


def _render_source_badge(label: str, source_url: str | None) -> str:
    if not source_url:
        return f"<span class='source'>{escape(label)}</span>"
    return (
        f"<a class='source source-link' href='{escape(source_url)}' target='_blank' rel='noreferrer noopener'>"
        f"{escape(label)}</a>"
    )


def _render_stock_link(text: str, stock_code: str) -> str:
    clean_code = stock_code.strip()
    if not clean_code or not re.fullmatch(r"\d{6}", clean_code):
        return escape(text)
    stock_url = TONGHUASHUN_STOCK_URL.format(stock_code=clean_code)
    return (
        f"<a class='stock-link' href='{escape(stock_url)}' target='_blank' rel='noreferrer noopener'>"
        f"{escape(text)}</a>"
    )


def _render_sector_chip(
    sector_name: str,
    sector_heat: dict[str, int],
    sector_windows: dict[str, list[int]],
    sector_links: dict[str, str],
    *,
    large: bool = False,
) -> str:
    heat = sector_heat.get(sector_name, 1)
    windows = " / ".join(WINDOW_LABELS[day] for day in sector_windows.get(sector_name, []))
    title = f"出现 {heat} 个模块：{windows}" if heat > 1 and windows else sector_name
    repeat_badge = f"<span class='chip-count'>{heat}</span>" if heat > 1 else ""
    large_class = " sector-chip-large" if large else ""
    source_url = sector_links.get(_clean_sector_name(sector_name))
    if source_url:
        return (
            f"<a class='sector-chip sector-chip-link {_heat_class(heat)}{large_class}' "
            f"href='{escape(source_url)}' target='_blank' rel='noreferrer noopener' title='{escape(title)}'>"
            f"{escape(sector_name)}{repeat_badge}</a>"
        )
    return (
        f"<span class='sector-chip {_heat_class(heat)}{large_class}' title='{escape(title)}'>"
        f"{escape(sector_name)}{repeat_badge}</span>"
    )


def _render_summary_group(
    title: str,
    items: list[str],
    sector_heat: dict[str, int],
    sector_windows: dict[str, list[int]],
    sector_links: dict[str, str],
) -> str:
    chips_html = (
        "".join(_render_sector_chip(item, sector_heat, sector_windows, sector_links) for item in items)
        if items
        else "<span class='summary-empty'>暂无</span>"
    )
    return f"""
    <div class="summary-card">
      <h3>{escape(title)}</h3>
      <div class="chip-row">
        {chips_html}
      </div>
    </div>
    """


def _render_outline(trade_date: str) -> str:
    links = [
        ("summary", "顶部总结"),
        ("window-1", trade_date),
        ("window-3", "近 3 日"),
        ("window-5", "近 5 日"),
        ("window-10", "近 10 日"),
        ("window-20", "近 20 日"),
        ("components", "成分股"),
        ("funds", "关联基金"),
    ]
    links_html = "\n".join(
        f"<a href='#{anchor}'>{escape(label)}</a>"
        for anchor, label in links
    )
    return f"""
    <details class="outline-card">
      <summary class="outline-toggle">
        <span class="outline-toggle-title">大纲</span>
        <span class="outline-toggle-state">
          <span class="outline-state-open">收起</span>
          <span class="outline-state-closed">展开</span>
        </span>
      </summary>
      <div class="outline-body">
        <div class="outline-head">
          <span>快速定位</span>
        </div>
        <nav class="outline-nav">
          {links_html}
        </nav>
      </div>
    </details>
    """


def _render_table(
    section_id: str,
    title: str,
    section: WindowSection,
    sector_heat: dict[str, int],
    sector_windows: dict[str, list[int]],
    sector_links: dict[str, str],
    source_url: str | None,
) -> str:
    rows = []
    for record in section.records:
        heat = sector_heat.get(record.sector_name, 1)
        row_class = f"repeat-row {_row_heat_class(heat)}" if heat > 1 else ""
        rows.append(
            f"""
            <tr class="{row_class}">
              <td>{record.rank_no or '-'}</td>
              <td>{_render_sector_chip(record.sector_name, sector_heat, sector_windows, sector_links)}</td>
              <td>{_fmt_amount(record.main_net_inflow)}</td>
              <td>{_fmt_pct(record.pct_change)}</td>
            </tr>
            """
        )

    note_html = f"<p class='note'>{escape(section.note)}</p>" if section.note else ""
    body = "\n".join(rows) if rows else "<tr><td colspan='4'>暂无数据</td></tr>"
    return f"""
    <section id="{section_id}" class="card window-card anchor-section">
      <div class="section-head">
        <h2>{escape(title)}</h2>
        {_render_source_badge(section.source_label, source_url)}
      </div>
      {note_html}
      <table>
        <thead>
          <tr>
            <th>排名</th>
            <th>板块</th>
            <th>主力净流入</th>
            <th>涨跌幅</th>
          </tr>
        </thead>
        <tbody>
          {body}
        </tbody>
      </table>
    </section>
    """


def _render_funds(
    sector_name: str,
    funds: list[MatchedFund],
    record: SectorFlowRecord | None,
    sector_heat: dict[str, int],
    sector_windows: dict[str, list[int]],
    sector_links: dict[str, str],
) -> str:
    heat = sector_heat.get(sector_name, 1)
    items = (
        "\n".join(
            f"<li><strong>{escape(fund.fund_name)}</strong>（{escape(fund.fund_code)} / {escape(fund.fund_type)}）</li>"
            for fund in funds
        )
        if funds
        else "<p class='empty'>暂无匹配 ETF/基金</p>"
    )
    return f"""
    <div class="fund-block {_block_heat_class(heat)}">
      <div class="block-head">
        {_render_sector_chip(sector_name, sector_heat, sector_windows, sector_links)}
      </div>
      <p class="meta">净流入：{_fmt_amount(record.main_net_inflow if record else None)}，涨跌幅：{_fmt_pct(record.pct_change if record else None)}</p>
      {"<ul>" + items + "</ul>" if funds else items}
    </div>
    """


def _render_components(
    sector_name: str,
    components: list[SectorComponentRecord],
    sector_heat: dict[str, int],
    sector_windows: dict[str, list[int]],
    sector_links: dict[str, str],
) -> str:
    if not components:
        return f"""
        <div class="component-block {_block_heat_class(sector_heat.get(sector_name, 1))}">
          <div class="block-head">
            {_render_sector_chip(sector_name, sector_heat, sector_windows, sector_links)}
          </div>
          <p class="empty">暂无成分股数据</p>
        </div>
        """

    rows = "\n".join(
        f"""
        <tr>
          <td>{component.rank_no or '-'}</td>
          <td>{_render_stock_link(component.stock_code, component.stock_code)}</td>
          <td>{_render_stock_link(component.stock_name, component.stock_code)}</td>
          <td>{_fmt_amount(component.latest_price)}</td>
          <td>{_fmt_pct(component.pct_change)}</td>
        </tr>
        """
        for component in components
    )
    return f"""
    <div class="component-block {_block_heat_class(sector_heat.get(sector_name, 1))}">
      <div class="block-head">
        {_render_sector_chip(sector_name, sector_heat, sector_windows, sector_links)}
      </div>
      <table>
        <thead>
          <tr>
            <th>排名</th>
            <th>代码</th>
            <th>成分股</th>
            <th>最新价</th>
            <th>涨跌幅</th>
          </tr>
        </thead>
        <tbody>
          {rows}
        </tbody>
      </table>
    </div>
    """


def render_html(payload: dict, report_name: str, raw_dir: Path | None = None) -> str:
    sector_heat, sector_windows = _build_sector_heat(payload["windows"])
    sector_links = _build_sector_link_map(raw_dir)
    repeated_names = sorted(
        [name for name, heat in sector_heat.items() if heat > 1],
        key=lambda name: (-sector_heat[name], sector_windows[name][0], name),
    )

    repeat_summary_html = (
        "".join(
            _render_sector_chip(name, sector_heat, sector_windows, sector_links, large=True)
            for name in repeated_names
        )
        if repeated_names
        else "<span class='summary-empty'>暂无跨模块重复热点</span>"
    )
    summary = f"""
    <section id="summary" class="hero anchor-section">
      <div class="hero-top">
        <div>
          <p class="eyebrow">股票基金日报</p>
          <h1>{escape(report_name)} | {escape(payload['trade_date'])}</h1>
          <p class="hero-desc">颜色越深，代表同一概念在更多模块重复出现，方便快速识别持续热点。</p>
        </div>
        <div class="hero-stats">
          <div class="stat-card">
            <span>重复热点</span>
            <strong>{len(repeated_names)}</strong>
          </div>
          <div class="stat-card">
            <span>持续热门</span>
            <strong>{len(payload['persistent_hot'])}</strong>
          </div>
          <div class="stat-card">
            <span>新热点</span>
            <strong>{len(payload['emerging'])}</strong>
          </div>
        </div>
      </div>
      <div class="summary-highlight">
        <div class="summary-highlight-head">
          <h2>重复热点</h2>
          <span>跨模块重复出现</span>
        </div>
        <div class="chip-row">
          {repeat_summary_html}
        </div>
      </div>
      <div class="summary-grid">
        {_render_summary_group(payload["trade_date"], payload["leaders"], sector_heat, sector_windows, sector_links)}
        {_render_summary_group("持续热门", payload["persistent_hot"], sector_heat, sector_windows, sector_links)}
        {_render_summary_group("新热点", payload["emerging"], sector_heat, sector_windows, sector_links)}
        {_render_summary_group("趋势钝化", payload["weakening"], sector_heat, sector_windows, sector_links)}
      </div>
    </section>
    """

    windows_html = "\n".join(
        _render_table(
            f"window-{window_days}",
            _window_display_title(window_days, payload["trade_date"]),
            payload["windows"][window_days],
            sector_heat,
            sector_windows,
            sector_links,
            _build_section_source_url(payload["windows"][window_days], window_days),
        )
        for window_days in (1, 3, 5, 10, 20)
    )

    funds_html = "\n".join(
        _render_funds(
            sector_name,
            funds,
            payload["focus_records"].get(sector_name),
            sector_heat,
            sector_windows,
            sector_links,
        )
        for sector_name, funds in payload["related_funds"].items()
    )
    component_note = ""
    if payload["component_unmatched_sectors"]:
        component_note = (
            "<p class='note'>以下概念暂未匹配到成分股页："
            + escape("、".join(payload["component_unmatched_sectors"]))
            + "</p>"
        )
    components_html = "\n".join(
        _render_components(sector_name, components, sector_heat, sector_windows, sector_links)
        for sector_name, components in payload["top_components"].items()
    )
    outline_html = _render_outline(payload["trade_date"])

    return f"""
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
      <meta charset="utf-8" />
      <title>{escape(report_name)}</title>
      <style>
        html {{ scroll-behavior:smooth; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif; background:#f8fafc; color:#1f2937; margin:0; }}
        .wrap {{ max-width: 1660px; margin: 0 auto; padding: 20px; }}
        .page-shell {{ position:relative; }}
        .main-column {{ min-width:0; }}
        .anchor-section {{ scroll-margin-top: 20px; }}
        .hero {{ background: linear-gradient(135deg, #1e3a8a, #1d4ed8); color:#fff; border-radius: 18px; padding: 20px; margin-bottom: 16px; box-shadow:0 16px 40px rgba(30, 64, 175, 0.18); }}
        .eyebrow {{ margin:0 0 8px; font-size:12px; letter-spacing:0.08em; text-transform:uppercase; opacity:0.78; }}
        .hero-top {{ display:grid; grid-template-columns: minmax(0, 1fr) 290px; gap:16px; align-items:start; margin-bottom:16px; }}
        .hero h1 {{ margin:0 0 10px; font-size: 34px; line-height:1.2; }}
        .hero-desc {{ margin:0; font-size:14px; line-height:1.6; max-width:760px; color:rgba(255,255,255,0.88); }}
        .hero-stats {{ display:grid; grid-template-columns: repeat(3, 1fr); gap:10px; }}
        .stat-card {{ background:rgba(255,255,255,0.14); border:1px solid rgba(255,255,255,0.16); border-radius:14px; padding:12px; }}
        .stat-card span {{ display:block; font-size:12px; color:rgba(255,255,255,0.78); }}
        .stat-card strong {{ display:block; margin-top:6px; font-size:28px; line-height:1; }}
        .summary-highlight {{ background:rgba(255,255,255,0.12); border:1px solid rgba(255,255,255,0.16); border-radius:16px; padding:14px; margin-bottom:14px; }}
        .summary-highlight-head, .outline-head {{ display:flex; justify-content:space-between; align-items:center; gap:10px; margin-bottom:10px; }}
        .summary-highlight-head h2, .outline-head h2 {{ margin:0; font-size:18px; }}
        .summary-highlight-head span {{ font-size:12px; color:rgba(255,255,255,0.78); }}
        .summary-grid {{ display:grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap:12px; }}
        .summary-card {{ background:rgba(255,255,255,0.1); border:1px solid rgba(255,255,255,0.12); border-radius:14px; padding:12px; }}
        .summary-card h3 {{ margin:0 0 10px; font-size:15px; }}
        .summary-empty {{ color:#cbd5e1; font-size:13px; }}
        .chip-row {{ display:flex; flex-wrap:wrap; gap:8px; }}
        .sector-chip {{ display:inline-flex; align-items:center; gap:6px; max-width:100%; padding:4px 10px; border-radius:999px; border:1px solid #cbd5e1; background:#f8fafc; color:#1f2937; font-size:12px; line-height:1.4; }}
        .sector-chip-link {{ text-decoration:none; }}
        .sector-chip-link:hover {{ transform:translateY(-1px); box-shadow:0 4px 12px rgba(15, 23, 42, 0.08); }}
        .sector-chip-large {{ padding:6px 12px; font-size:13px; }}
        .chip-count {{ display:inline-flex; align-items:center; justify-content:center; min-width:18px; height:18px; padding:0 5px; border-radius:999px; background:rgba(255,255,255,0.78); font-size:11px; font-weight:700; color:#1f2937; }}
        .heat-1 {{ border-color:#cbd5e1; background:#f8fafc; color:#334155; }}
        .heat-2 {{ border-color:#22c55e; background:#dcfce7; color:#166534; }}
        .heat-3 {{ border-color:#eab308; background:#fef9c3; color:#854d0e; }}
        .heat-4 {{ border-color:#f97316; background:#ffedd5; color:#9a3412; }}
        .heat-5 {{ border-color:#dc2626; background:#b91c1c; color:#fff; }}
        .window-grid {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap:14px; margin-bottom:16px; }}
        .card {{ background:#fff; border-radius:14px; padding:16px; margin-bottom:16px; box-shadow:0 8px 24px rgba(15, 23, 42, 0.06); }}
        .window-card {{ margin-bottom:0; }}
        .section-head {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; gap:10px; }}
        .section-head h2 {{ margin:0; font-size:18px; }}
        .source {{ color:#1e40af; font-size:12px; font-weight:700; white-space:nowrap; }}
        .source-link {{ text-decoration:none; }}
        .source-link:hover {{ text-decoration:underline; }}
        .stock-link {{ color:#1d4ed8; text-decoration:none; }}
        .stock-link:hover {{ text-decoration:underline; }}
        .note {{ color:#b45309; font-size:12px; margin:0 0 10px; line-height:1.5; }}
        table {{ width:100%; border-collapse: collapse; }}
        th, td {{ padding:8px 10px; border-bottom:1px solid #e5e7eb; text-align:left; font-size:13px; line-height:1.4; vertical-align:top; }}
        th {{ background:#f8fafc; }}
        .repeat-row td:first-child {{ font-weight:700; }}
        tr.row-heat-2 td {{ background:#f0fdf4; }}
        tr.row-heat-3 td {{ background:#fefce8; }}
        tr.row-heat-4 td {{ background:#fff7ed; }}
        tr.row-heat-5 td {{ background:#fef2f2; }}
        tr.row-heat-2 td:first-child {{ border-left:4px solid #22c55e; }}
        tr.row-heat-3 td:first-child {{ border-left:4px solid #eab308; }}
        tr.row-heat-4 td:first-child {{ border-left:4px solid #f97316; }}
        tr.row-heat-5 td:first-child {{ border-left:4px solid #dc2626; }}
        .component-grid {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap:12px; }}
        .component-block, .fund-block {{ border-radius:14px; padding:12px; border:1px solid #e5e7eb; background:#fff; }}
        .component-block.block-heat-2, .fund-block.block-heat-2 {{ border-color:#86efac; box-shadow: inset 5px 0 0 #22c55e; }}
        .component-block.block-heat-3, .fund-block.block-heat-3 {{ border-color:#fde047; box-shadow: inset 5px 0 0 #eab308; }}
        .component-block.block-heat-4, .fund-block.block-heat-4 {{ border-color:#fdba74; box-shadow: inset 5px 0 0 #f97316; }}
        .component-block.block-heat-5, .fund-block.block-heat-5 {{ border-color:#fca5a5; box-shadow: inset 5px 0 0 #dc2626; }}
        .block-head {{ display:flex; align-items:center; gap:8px; margin-bottom:10px; }}
        .fund-grid {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap:12px; }}
        .fund-block ul {{ margin:6px 0 0; padding-left:18px; }}
        .fund-block li {{ font-size:13px; line-height:1.5; }}
        .meta {{ color:#475569; font-size:12px; margin:0; line-height:1.5; }}
        .empty {{ color:#94a3b8; font-size:13px; }}
        .outline-card {{ position:fixed; top:20px; right:max(12px, calc((100vw - 1660px) / 2 + 20px)); width:156px; background:#fff; border-radius:14px; padding:10px; box-shadow:0 8px 24px rgba(15, 23, 42, 0.08); z-index:30; }}
        .outline-card:not([open]) {{ width:92px; }}
        .outline-card[open] {{ padding-bottom:12px; }}
        .outline-toggle {{ display:flex; align-items:center; justify-content:space-between; gap:8px; cursor:pointer; list-style:none; user-select:none; }}
        .outline-toggle::-webkit-details-marker {{ display:none; }}
        .outline-toggle-title {{ font-size:14px; font-weight:700; color:#0f172a; }}
        .outline-toggle-state {{ font-size:11px; color:#64748b; }}
        .outline-card[open] .outline-state-closed {{ display:none; }}
        .outline-card:not([open]) .outline-state-open {{ display:none; }}
        .outline-body {{ margin-top:10px; }}
        .outline-head span {{ display:block; font-size:11px; color:#64748b; }}
        .outline-nav {{ display:flex; flex-direction:column; gap:8px; }}
        .outline-nav a {{ display:flex; align-items:center; min-height:32px; padding:0 10px; border-radius:10px; background:#f8fafc; color:#334155; text-decoration:none; font-size:12px; transition:background 0.2s ease, color 0.2s ease; }}
        .outline-nav a:hover {{ background:#dbeafe; color:#1d4ed8; }}
        .footer {{ color:#64748b; font-size:12px; margin-top:12px; }}
        @media (max-width: 1100px) {{
          .outline-card {{ right:12px; top:12px; }}
        }}
        @media (max-width: 960px) {{
          .hero-top {{ grid-template-columns: 1fr; }}
          .summary-grid {{ grid-template-columns: 1fr; }}
          .outline-card {{ width:144px; }}
          .outline-card:not([open]) {{ width:84px; }}
        }}
        @media (max-width: 640px) {{
          .wrap {{ padding: 12px; }}
          .hero h1 {{ font-size: 28px; }}
          .hero-stats {{ grid-template-columns: 1fr; }}
        }}
      </style>
    </head>
    <body>
      <div class="wrap">
        <div class="page-shell">
          <main class="main-column">
            {summary}
            <div class="window-grid">
              {windows_html}
            </div>
            <section id="components" class="card anchor-section">
              <div class="section-head">
                <h2>{escape(payload["trade_date"])} 概念前十成分股</h2>
                {_render_source_badge("同花顺概念详情页", TONGHUASHUN_CONCEPT_INDEX_URL)}
              </div>
              {component_note}
              <div class="component-grid">
                {components_html}
              </div>
            </section>
            <section id="funds" class="card anchor-section">
              <div class="section-head">
                <h2>热点板块关联 ETF/基金</h2>
                <span class="source">ETF/指数基金优先</span>
              </div>
              <div class="fund-grid">
                {funds_html}
              </div>
            </section>
            <p class="footer">数据来源：东方财富公开接口、东方财富历史累计、同花顺公开页面。报告仅做信息整理，不构成投资建议。</p>
          </main>
          {outline_html}
        </div>
      </div>
    </body>
    </html>
    """


def save_report(output_dir: Path, trade_date: str, html: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"{trade_date}-daily-report.html"
    report_path.write_text(html, encoding="utf-8")
    return report_path
