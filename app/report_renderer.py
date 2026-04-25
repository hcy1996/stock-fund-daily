from __future__ import annotations

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
    3: "近 3 日",
    5: "近 5 日",
    10: "近 10 日",
    20: "近 20 日",
}
EASTMONEY_CONCEPT_LIST_URL = "https://data.eastmoney.com/bkzj/gn.html"
EASTMONEY_CONCEPT_DETAIL_URL = "https://data.eastmoney.com/bkzj/{sector_code}.html"
TONGHUASHUN_WINDOW_URLS = {
    1: TONGHUASHUN_1D_URL,
    3: TONGHUASHUN_3D_URL,
    5: TONGHUASHUN_5D_URL,
    10: TONGHUASHUN_10D_URL,
    20: TONGHUASHUN_20D_URL,
}
TONGHUASHUN_STOCK_URL = "https://stockpage.10jqka.com.cn/{stock_code}/"
TONGHUASHUN_CONCEPT_LINK_PATTERN = re.compile(
    r'href="https?://q\.10jqka\.com\.cn/gn/detail/code/(\d+)/"[^>]*>([^<]+)</a>'
)


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


def _clean_sector_name(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip()


def _heat_level(heat: int) -> int:
    if heat >= 5:
        return 5
    return max(1, heat)


def _heat_class(heat: int) -> str:
    return f"heat-{_heat_level(heat)}"


def _row_heat_class(heat: int) -> str:
    return f"row-heat-{_heat_level(heat)}"


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
            if clean_name:
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


def _build_eastmoney_link_map(payload: dict) -> dict[str, str]:
    sector_links: dict[str, str] = {}
    for source_windows in payload["source_windows"].values():
        for section in source_windows.values():
            for record in section.records:
                sector_code = (record.sector_code or "").strip().upper()
                if re.fullmatch(r"BK\d{4,}", sector_code):
                    sector_links.setdefault(
                        _clean_sector_name(record.sector_name),
                        EASTMONEY_CONCEPT_DETAIL_URL.format(sector_code=sector_code),
                    )
    return sector_links


def _render_source_badge(label: str, source_url: str | None) -> str:
    if not source_url:
        return f"<span class='source'>{escape(label)}</span>"
    return (
        f"<a class='source source-link' href='{escape(source_url)}' target='_blank' rel='noreferrer noopener'>"
        f"{escape(label)}</a>"
    )


def _render_sector_link(sector_name: str, sector_links: dict[str, str]) -> str:
    source_url = sector_links.get(_clean_sector_name(sector_name))
    if not source_url:
        return escape(sector_name)
    return (
        f"<a class='sector-link' href='{escape(source_url)}' target='_blank' rel='noreferrer noopener'>"
        f"{escape(sector_name)}</a>"
    )


def _render_sector_chip(
    sector_name: str,
    sector_links: dict[str, str],
    sector_heat: dict[str, int],
    sector_occurrences: dict[str, list[str]],
    *,
    compact: bool = False,
    show_source_badges: bool = False,
) -> str:
    heat = sector_heat.get(sector_name, 1)
    chip_class = f"chip {_heat_class(heat)}"
    if compact:
        chip_class += " chip-compact"
    if show_source_badges:
        chip_class += " chip-with-sources"
    title = " / ".join(sector_occurrences.get(sector_name, [])) or sector_name
    badge = f"<span class='chip-count'>{heat}</span>" if heat > 1 else ""
    source_badges = ""
    if show_source_badges:
        occurrences = sector_occurrences.get(sector_name, [])
        source_items: list[str] = []
        if any(item.startswith("东方财富") for item in occurrences):
            source_items.append("<span class='chip-source-badge chip-source-eastmoney'>东</span>")
        if any(item.startswith("同花顺") for item in occurrences):
            source_items.append("<span class='chip-source-badge chip-source-tonghuashun'>顺</span>")
        if source_items:
            source_badges = f"<span class='chip-source-badges'>{''.join(source_items)}</span>"
    return (
        f"<span class='{chip_class}' title='{escape(title)}'>"
        f"{source_badges}{_render_sector_link(sector_name, sector_links)}{badge}</span>"
    )


def _render_chip_list(
    items: list[str],
    sector_links: dict[str, str],
    sector_heat: dict[str, int],
    sector_occurrences: dict[str, list[str]],
    *,
    empty_text: str = "暂无",
    compact: bool = False,
    show_source_badges: bool = False,
) -> str:
    if not items:
        return f"<span class='empty-text'>{escape(empty_text)}</span>"
    return "".join(
        _render_sector_chip(
            item,
            sector_links,
            sector_heat,
            sector_occurrences,
            compact=compact,
            show_source_badges=show_source_badges,
        )
        for item in items
    )


def _render_warning_list(warnings: list[str]) -> str:
    if not warnings:
        return ""
    items = "".join(f"<li>{escape(item)}</li>" for item in warnings)
    return f"""
    <section class="warning-card">
      <h2>降级提示</h2>
      <ul>{items}</ul>
    </section>
    """


def _render_tab_bar(trade_date: str) -> str:
    tabs = [
        ("summary", "摘要"),
        ("window-1", trade_date),
        ("window-3", "近3日"),
        ("window-5", "近5日"),
        ("window-10", "近10日"),
        ("window-20", "近20日"),
        ("components", "成分股"),
        ("funds", "基金"),
    ]
    items = "".join(
        f"<a class='tab-link' href='#{anchor}'>{escape(label)}</a>"
        for anchor, label in tabs
    )
    return f"""
    <nav class="tab-bar">
      <div class="tab-scroll">
        {items}
      </div>
    </nav>
    """


def _render_stat_card(label: str, value: int | str) -> str:
    return f"""
    <div class="stat-card">
      <span>{escape(label)}</span>
      <strong>{escape(str(value))}</strong>
    </div>
    """


def _render_leader_card(
    title: str,
    leaders: list[str],
    sector_links: dict[str, str],
    sector_heat: dict[str, int],
    sector_occurrences: dict[str, list[str]],
) -> str:
    return f"""
    <div class="summary-card">
      <h3>{escape(title)}</h3>
      <div class="chip-row">
        {_render_chip_list(leaders, sector_links, sector_heat, sector_occurrences)}
      </div>
    </div>
    """


def _render_compare_summary(
    title: str,
    items: list[str],
    sector_links: dict[str, str],
    sector_heat: dict[str, int],
    sector_occurrences: dict[str, list[str]],
    *,
    empty_text: str = "暂无",
) -> str:
    compact = False
    return f"""
    <div class="compare-card">
      <h4>{escape(title)}</h4>
      <div class="chip-row">
        {_render_chip_list(
            items,
            sector_links,
            sector_heat,
            sector_occurrences,
            empty_text=empty_text,
            compact=compact,
        )}
      </div>
    </div>
    """


def _build_source_url(source_key: str, window_days: int) -> str | None:
    if source_key == "eastmoney":
        return EASTMONEY_CONCEPT_LIST_URL
    return TONGHUASHUN_WINDOW_URLS.get(window_days)


def _render_source_table(
    section: WindowSection,
    source_key: str,
    window_days: int,
    sector_links: dict[str, str],
    sector_heat: dict[str, int],
    sector_occurrences: dict[str, list[str]],
) -> str:
    rows = []
    for record in section.records:
        heat = sector_heat.get(record.sector_name, 1)
        row_class = _row_heat_class(heat) if heat > 1 else ""
        row_sector_links = dict(sector_links)
        if source_key == "eastmoney":
            sector_code = (record.sector_code or "").strip().upper()
            if re.fullmatch(r"BK\d{4,}", sector_code):
                row_sector_links[_clean_sector_name(record.sector_name)] = (
                    EASTMONEY_CONCEPT_DETAIL_URL.format(sector_code=sector_code)
                )
        rows.append(
            f"""
            <tr class="{row_class}">
              <td>{record.rank_no or "-"}</td>
              <td>{_render_sector_chip(record.sector_name, row_sector_links, sector_heat, sector_occurrences)}</td>
              <td>{_fmt_amount(record.main_net_inflow)}</td>
              <td>{_fmt_pct(record.pct_change)}</td>
            </tr>
            """
        )

    note_html = f"<p class='note'>{escape(section.note)}</p>" if section.note else ""
    body = "\n".join(rows) if rows else "<tr><td colspan='4'>暂无数据</td></tr>"
    return f"""
    <div class="table-card">
      <div class="table-head">
        <h3>{escape(section.source_label)}</h3>
        {_render_source_badge(section.source_label, _build_source_url(source_key, window_days))}
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
    </div>
    """


def _render_window_section(
    trade_date: str,
    window_days: int,
    eastmoney_section: WindowSection,
    tonghuashun_section: WindowSection,
    comparison: dict,
    sector_links: dict[str, str],
    sector_heat: dict[str, int],
    sector_occurrences: dict[str, list[str]],
) -> str:
    title = trade_date if window_days == 1 else WINDOW_LABELS[window_days]
    return f"""
    <section id="window-{window_days}" class="window-card anchor-section">
      <div class="window-head">
        <h2>{escape(title)}</h2>
      </div>
      <div class="table-grid">
        {_render_source_table(eastmoney_section, "eastmoney", window_days, sector_links, sector_heat, sector_occurrences)}
        {_render_source_table(tonghuashun_section, "tonghuashun", window_days, sector_links, sector_heat, sector_occurrences)}
      </div>
    </section>
    """


def _render_stock_link(text: str, stock_code: str) -> str:
    clean_code = stock_code.strip()
    if not clean_code or not re.fullmatch(r"\d{6}", clean_code):
        return escape(text)
    stock_url = TONGHUASHUN_STOCK_URL.format(stock_code=clean_code)
    return (
        f"<a class='stock-link' href='{escape(stock_url)}' target='_blank' rel='noreferrer noopener'>"
        f"{escape(text)}</a>"
    )


def _render_components(
    sector_name: str,
    components: list[SectorComponentRecord],
    sector_links: dict[str, str],
    sector_heat: dict[str, int],
    sector_occurrences: dict[str, list[str]],
) -> str:
    if not components:
        return f"""
        <div class="component-card">
          <div class="component-head">{_render_sector_chip(sector_name, sector_links, sector_heat, sector_occurrences)}</div>
          <p class="empty-text">暂无成分股数据</p>
        </div>
        """

    rows = "\n".join(
        f"""
        <tr>
          <td>{component.rank_no or "-"}</td>
          <td>{_render_stock_link(component.stock_code, component.stock_code)}</td>
          <td>{_render_stock_link(component.stock_name, component.stock_code)}</td>
          <td>{_fmt_amount(component.latest_price)}</td>
          <td>{_fmt_pct(component.pct_change)}</td>
        </tr>
        """
        for component in components
    )
    return f"""
    <div class="component-card">
      <div class="component-head">{_render_sector_chip(sector_name, sector_links, sector_heat, sector_occurrences)}</div>
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
        <tbody>{rows}</tbody>
      </table>
    </div>
    """


def _render_funds(
    sector_name: str,
    funds: list[MatchedFund],
    record: SectorFlowRecord | None,
    sector_links: dict[str, str],
    sector_heat: dict[str, int],
    sector_occurrences: dict[str, list[str]],
) -> str:
    items = (
        "\n".join(
            f"<li><strong>{escape(fund.fund_name)}</strong>（{escape(fund.fund_code)} / {escape(fund.fund_type)}）</li>"
            for fund in funds
        )
        if funds
        else "<p class='empty-text'>暂无匹配 ETF/基金</p>"
    )
    return f"""
    <div class="fund-card">
      <div class="component-head">{_render_sector_chip(sector_name, sector_links, sector_heat, sector_occurrences)}</div>
      <p class="fund-meta">净流入：{_fmt_amount(record.main_net_inflow if record else None)}，涨跌幅：{_fmt_pct(record.pct_change if record else None)}</p>
      {"<ul>" + items + "</ul>" if funds else items}
    </div>
    """


def render_html(payload: dict, report_name: str, raw_dir: Path | None = None) -> str:
    sector_links = _build_sector_link_map(raw_dir)
    eastmoney_links = _build_eastmoney_link_map(payload)
    sector_links = {**eastmoney_links, **sector_links}
    sector_heat = payload["sector_heat"]
    sector_occurrences = payload["sector_occurrences"]
    warnings_html = _render_warning_list(payload["warnings"])
    tab_bar_html = _render_tab_bar(payload["trade_date"])
    summary_html = f"""
    <section id="summary" class="hero anchor-section">
      <div class="hero-head">
        <div>
          <p class="eyebrow">股票基金日报</p>
          <h1>{escape(report_name)} | {escape(payload["trade_date"])}</h1>
        </div>
      </div>
      <div class="hero-panel-grid">
        <div class="summary-card summary-card-dark">
          <h3>高重复热点</h3>
          <div class="chip-row">
            {_render_chip_list(payload["repeated_focus"], sector_links, sector_heat, sector_occurrences, empty_text="暂无", show_source_badges=True)}
          </div>
        </div>
        <div class="summary-card summary-card-dark">
          <h3>跨周期持续热点</h3>
          <div class="chip-row">
            {_render_chip_list(payload["persistent_focus"], sector_links, sector_heat, sector_occurrences, empty_text="暂无", show_source_badges=True)}
          </div>
        </div>
      </div>
      <div class="summary-grid">
        {_render_leader_card("东方财富当日 Top10", payload["leaders_by_source"]["eastmoney"], sector_links, sector_heat, sector_occurrences)}
        {_render_leader_card("同花顺当日 Top10", payload["leaders_by_source"]["tonghuashun"], sector_links, sector_heat, sector_occurrences)}
      </div>
    </section>
    """

    windows_html = "\n".join(
        _render_window_section(
            payload["trade_date"],
            window_days,
            payload["source_windows"]["eastmoney"][window_days],
            payload["source_windows"]["tonghuashun"][window_days],
            payload["comparisons"][window_days],
            sector_links,
            sector_heat,
            sector_occurrences,
        )
        for window_days in (1, 3, 5, 10, 20)
    )

    component_note = ""
    if payload["component_unmatched_sectors"]:
        component_note = (
            "<p class='note'>以下概念暂未匹配到成分股页："
            + escape("、".join(payload["component_unmatched_sectors"]))
            + "</p>"
        )

    components_html = "\n".join(
        _render_components(sector_name, components, sector_links, sector_heat, sector_occurrences)
        for sector_name, components in payload["top_components"].items()
    )
    funds_html = "\n".join(
        _render_funds(
            sector_name,
            funds,
            payload["focus_records"].get(sector_name),
            sector_links,
            sector_heat,
            sector_occurrences,
        )
        for sector_name, funds in payload["related_funds"].items()
    )

    return f"""
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
      <meta charset="utf-8" />
      <title>{escape(report_name)}</title>
      <style>
        html {{ scroll-behavior:smooth; }}
        body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif; background:#f3f6fb; color:#1f2937; }}
        .wrap {{ max-width:1560px; margin:0 auto; padding:14px; }}
        .anchor-section {{ scroll-margin-top:72px; }}
        .hero {{ background:linear-gradient(135deg,#0f3d91,#1d4ed8); color:#fff; border-radius:18px; padding:16px; box-shadow:0 18px 40px rgba(29,78,216,.18); }}
        .hero-head {{ display:block; }}
        .eyebrow {{ margin:0 0 6px; font-size:12px; letter-spacing:.08em; text-transform:uppercase; opacity:.78; }}
        .hero h1 {{ margin:0; font-size:34px; line-height:1.2; }}
        .hero-panel-grid {{ display:grid; grid-template-columns:1.2fr 1fr; gap:10px; margin-top:12px; }}
        .summary-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; margin-top:12px; }}
        .summary-card,.warning-card,.window-card,.section-card {{ background:#fff; color:#1f2937; border-radius:16px; padding:14px; box-shadow:0 10px 24px rgba(15,23,42,.06); }}
        .summary-card h3,.warning-card h2,.window-card h2,.section-card h2 {{ margin:0 0 12px; }}
        .summary-card-dark {{ background:rgba(255,255,255,.12); color:#fff; border:1px solid rgba(255,255,255,.18); box-shadow:none; }}
        .summary-card-dark .empty-text {{ color:rgba(255,255,255,.72); }}
        .warning-card {{ margin-top:12px; border:1px solid #fecaca; background:#fff7ed; }}
        .warning-card ul {{ margin:0; padding-left:18px; color:#9a3412; line-height:1.7; }}
        .tab-bar {{ position:sticky; top:8px; z-index:40; margin-top:12px; padding:8px 10px; border-radius:14px; background:rgba(255,255,255,.92); box-shadow:0 10px 24px rgba(15,23,42,.08); backdrop-filter:blur(10px); }}
        .tab-scroll {{ display:flex; gap:8px; overflow-x:auto; scrollbar-width:none; }}
        .tab-scroll::-webkit-scrollbar {{ display:none; }}
        .tab-link {{ flex:0 0 auto; padding:8px 12px; border-radius:999px; background:#eef2ff; color:#1d4ed8; text-decoration:none; font-size:13px; font-weight:600; white-space:nowrap; }}
        .tab-link:hover {{ background:#dbeafe; }}
        .window-card,.section-card {{ margin-top:12px; }}
        .window-head {{ display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:8px; }}
        .chip-row {{ display:flex; flex-wrap:wrap; gap:8px; }}
        .chip {{ display:inline-flex; align-items:center; padding:5px 10px; border-radius:999px; background:#e0f2fe; color:#0f172a; font-size:12px; line-height:1.5; border:1px solid #bfdbfe; }}
        .chip-compact {{ background:#eef2ff; border-color:#c7d2fe; }}
        .chip-with-sources {{ position:relative; overflow:visible; }}
        .chip-source-badges {{ position:absolute; top:-8px; right:-6px; display:flex; gap:0; }}
        .chip-source-badge {{ display:inline-flex; align-items:center; justify-content:center; width:18px; height:18px; margin-left:-4px; border-radius:999px; font-size:10px; line-height:1; font-weight:800; box-shadow:0 2px 8px rgba(15,23,42,.18), 0 0 0 2px #ffffff; }}
        .chip-source-eastmoney {{ background:#1d4ed8; color:#ffffff; z-index:2; }}
        .chip-source-tonghuashun {{ background:#16a34a; color:#ffffff; z-index:1; }}
        .chip-count {{ display:inline-flex; align-items:center; justify-content:center; min-width:18px; height:18px; margin-left:6px; padding:0 5px; border-radius:999px; background:rgba(255,255,255,.72); font-size:11px; font-weight:700; color:#0f172a; }}
        .heat-1 {{ background:#f8fbff; border-color:#bfdbfe; color:#334155; }}
        .heat-2 {{ background:#d1fae5; border-color:#22c55e; color:#14532d; }}
        .heat-3 {{ background:#fde68a; border-color:#f59e0b; color:#78350f; }}
        .heat-4 {{ background:#fdba74; border-color:#ea580c; color:#7c2d12; }}
        .heat-5 {{ background:#f87171; border-color:#b91c1c; color:#ffffff; }}
        .empty-text {{ color:#94a3b8; font-size:13px; }}
        .table-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }}
        .table-card {{ border:1px solid #e5e7eb; border-radius:14px; padding:10px 12px; background:#fff; }}
        .table-head,.section-head {{ display:flex; justify-content:space-between; align-items:center; gap:10px; margin-bottom:10px; }}
        .table-head h3,.section-head h2 {{ margin:0; }}
        table {{ width:100%; border-collapse:collapse; }}
        th,td {{ padding:7px 9px; border-bottom:1px solid #e5e7eb; text-align:left; vertical-align:top; font-size:13px; line-height:1.45; }}
        th {{ background:#f8fafc; }}
        tr.row-heat-2 td:first-child {{ border-left:5px solid #16a34a; }}
        tr.row-heat-3 td:first-child {{ border-left:5px solid #d97706; }}
        tr.row-heat-4 td:first-child {{ border-left:5px solid #ea580c; }}
        tr.row-heat-5 td:first-child {{ border-left:5px solid #b91c1c; }}
        tr.row-heat-2 td {{ background:#ecfdf5; }}
        tr.row-heat-3 td {{ background:#fffbeb; }}
        tr.row-heat-4 td {{ background:#fff7ed; }}
        tr.row-heat-5 td {{ background:#fee2e2; }}
        .source {{ color:#1d4ed8; font-size:12px; font-weight:700; white-space:nowrap; }}
        .source-link,.sector-link,.stock-link {{ color:#1d4ed8; text-decoration:none; }}
        .source-link:hover,.sector-link:hover,.stock-link:hover {{ text-decoration:underline; }}
        .note {{ margin:0 0 10px; color:#9a3412; font-size:12px; line-height:1.6; }}
        .component-grid,.fund-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:10px; }}
        .component-card,.fund-card {{ border:1px solid #e5e7eb; border-radius:14px; padding:10px 12px; background:#fff; }}
        .component-head {{ margin-bottom:8px; font-weight:700; }}
        .fund-meta {{ margin:0; color:#475569; font-size:12px; line-height:1.6; }}
        .fund-card ul {{ margin:8px 0 0; padding-left:18px; }}
        .fund-card li {{ line-height:1.6; font-size:13px; }}
        .footer {{ margin:12px 0 0; color:#64748b; font-size:12px; }}
        @media (max-width: 1080px) {{
          .hero-head,.hero-panel-grid,.summary-grid,.table-grid {{ grid-template-columns:1fr; }}
        }}
        @media (max-width: 640px) {{
          .wrap {{ padding:12px; }}
          .hero h1 {{ font-size:28px; }}
        }}
      </style>
    </head>
    <body>
      <div class="wrap">
        {summary_html}
        {warnings_html}
        {tab_bar_html}
        {windows_html}
        <section id="components" class="section-card anchor-section">
          <div class="section-head">
            <h2>概念前十成分股</h2>
            {_render_source_badge("同花顺概念详情页", TONGHUASHUN_CONCEPT_INDEX_URL)}
          </div>
          {component_note}
          <div class="component-grid">
            {components_html}
          </div>
        </section>
        <section id="funds" class="section-card anchor-section">
          <div class="section-head">
            <h2>热点板块关联 ETF/基金</h2>
            <span class="source">按双源当日榜单并集匹配</span>
          </div>
          <div class="fund-grid">
            {funds_html}
          </div>
        </section>
        <p class="footer">数据来源：东方财富公开接口、东方财富历史累计、同花顺公开页面。报告仅做信息整理，不构成投资建议。</p>
      </div>
    </body>
    </html>
    """


def save_report(output_dir: Path, trade_date: str, html: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"{trade_date}-daily-report.html"
    report_path.write_text(html, encoding="utf-8")
    return report_path
