from __future__ import annotations

from html import escape
import json
from pathlib import Path
import re

from app.models import (
    FundHoldingRecord,
    FundRankRecord,
    FundRankSection,
    MatchedFund,
    SectorComponentRecord,
    SectorFlowRecord,
    WindowSection,
)
from app.raw_enricher import TONGHUASHUN_CONCEPT_DETAIL_URL, TONGHUASHUN_CONCEPT_INDEX_URL
from app.sector_bridge_renderer import render_sector_bridge_section
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
EASTMONEY_FUND_DETAIL_URL = "https://fund.eastmoney.com/{fund_code}.html"
EASTMONEY_FUND_RANK_URL = "https://fund.eastmoney.com/data/fundranking.html"
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


def _render_plain_chip(
    label: str,
    heat: int,
    occurrences: list[str],
) -> str:
    title = " / ".join(occurrences) or label
    badge = f"<span class='chip-count'>{heat}</span>" if heat > 1 else ""
    return f"<span class='chip {_heat_class(heat)}' title='{escape(title)}'>{escape(label)}{badge}</span>"


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


def _render_markdown_inline(text: str) -> str:
    escaped = escape(text)
    code_tokens: dict[str, str] = {}

    def _replace_code(match: re.Match[str]) -> str:
        token = f"__AI_CODE_{len(code_tokens)}__"
        code_tokens[token] = f"<code>{match.group(1)}</code>"
        return token

    escaped = re.sub(r"`([^`\n]+)`", _replace_code, escaped)
    escaped = re.sub(
        r"\[([^\]]+)\]\((https?://[^\s)]+)\)",
        (
            r"<a class='ai-inline-link' href='\2' target='_blank' rel='noreferrer noopener'>"
            r"\1</a>"
        ),
        escaped,
    )
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"__(.+?)__", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<em>\1</em>", escaped)
    escaped = re.sub(r"(?<!_)_(?!\s)(.+?)(?<!\s)_(?!_)", r"<em>\1</em>", escaped)
    for token, html in code_tokens.items():
        escaped = escaped.replace(token, html)
    return escaped


def _is_markdown_hr(line: str) -> bool:
    return bool(re.fullmatch(r"\s*(?:[-*_]\s*){3,}\s*", line))


def _is_markdown_table_separator(line: str) -> bool:
    return bool(
        re.fullmatch(r"\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*", line)
    )


def _parse_markdown_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _looks_like_markdown_table(lines: list[str], idx: int) -> bool:
    if idx + 1 >= len(lines):
        return False
    return "|" in lines[idx] and _is_markdown_table_separator(lines[idx + 1])


def _is_markdown_block_start(lines: list[str], idx: int) -> bool:
    if idx >= len(lines):
        return False
    stripped = lines[idx].strip()
    if not stripped:
        return False
    return (
        stripped.startswith("```")
        or stripped.startswith(">")
        or _is_markdown_hr(stripped)
        or _looks_like_markdown_table(lines, idx)
        or bool(re.match(r"^(#{1,4})\s+.+$", stripped))
        or bool(re.match(r"^\d+[.)、]\s+.+$", stripped))
        or bool(re.match(r"^[-*+]\s+.+$", stripped))
    )


def _render_markdown_paragraph(lines: list[str]) -> str:
    content = "<br />".join(_render_markdown_inline(line.strip()) for line in lines if line.strip())
    return f"<p>{content}</p>"


def _render_markdown_table(lines: list[str], start_idx: int) -> tuple[str, int]:
    headers = _parse_markdown_table_row(lines[start_idx])
    rows: list[list[str]] = []
    idx = start_idx + 2
    while idx < len(lines):
        stripped = lines[idx].strip()
        if not stripped or "|" not in lines[idx]:
            break
        rows.append(_parse_markdown_table_row(lines[idx]))
        idx += 1

    header_html = "".join(f"<th>{_render_markdown_inline(cell)}</th>" for cell in headers)
    row_html = "".join(
        "<tr>"
        + "".join(f"<td>{_render_markdown_inline(cell)}</td>" for cell in row)
        + "</tr>"
        for row in rows
    )
    return (
        "<div class='ai-table-wrap'>"
        "<table class='ai-table'>"
        f"<thead><tr>{header_html}</tr></thead>"
        f"<tbody>{row_html}</tbody>"
        "</table>"
        "</div>",
        idx,
    )


def _render_markdown_blocks(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    parts: list[str] = []
    idx = 0

    while idx < len(lines):
        stripped = lines[idx].strip()
        if not stripped:
            idx += 1
            continue

        if stripped.startswith("```"):
            language = stripped[3:].strip()
            code_lines: list[str] = []
            idx += 1
            while idx < len(lines) and not lines[idx].strip().startswith("```"):
                code_lines.append(lines[idx].rstrip())
                idx += 1
            if idx < len(lines):
                idx += 1
            code_html = escape("\n".join(code_lines))
            language_html = (
                f"<div class='ai-code-lang'>{escape(language)}</div>" if language else ""
            )
            parts.append(
                "<div class='ai-code-block'>"
                f"{language_html}<pre><code>{code_html}</code></pre>"
                "</div>"
            )
            continue

        if _looks_like_markdown_table(lines, idx):
            table_html, idx = _render_markdown_table(lines, idx)
            parts.append(table_html)
            continue

        if _is_markdown_hr(stripped):
            parts.append("<hr class='ai-divider' />")
            idx += 1
            continue

        heading_match = re.match(r"^(#{1,4})\s+(.+)$", stripped)
        if heading_match:
            level = min(6, 2 + len(heading_match.group(1)))
            parts.append(
                f"<h{level}>{_render_markdown_inline(heading_match.group(2).strip())}</h{level}>"
            )
            idx += 1
            continue

        if stripped.startswith(">"):
            quote_lines: list[str] = []
            while idx < len(lines) and lines[idx].strip().startswith(">"):
                quote_lines.append(re.sub(r"^\s*>\s?", "", lines[idx]))
                idx += 1
            quote_html = _render_markdown_blocks("\n".join(quote_lines))
            parts.append(f"<blockquote>{quote_html}</blockquote>")
            continue

        ordered_match = re.match(r"^\d+[.)、]\s+(.+)$", stripped)
        unordered_match = re.match(r"^[-*+]\s+(.+)$", stripped)
        if ordered_match or unordered_match:
            pattern = (
                re.compile(r"^\d+[.)、]\s+(.+)$")
                if ordered_match
                else re.compile(r"^[-*+]\s+(.+)$")
            )
            tag = "ol" if ordered_match else "ul"
            items: list[str] = []
            while idx < len(lines):
                current = lines[idx].strip()
                if not current:
                    break
                item_match = pattern.match(current)
                if not item_match:
                    break
                item_lines = [item_match.group(1).strip()]
                idx += 1
                while idx < len(lines):
                    continuation = lines[idx].strip()
                    if not continuation or _is_markdown_block_start(lines, idx):
                        break
                    item_lines.append(continuation)
                    idx += 1
                item_html = "<br />".join(_render_markdown_inline(line) for line in item_lines)
                items.append(f"<li>{item_html}</li>")
            parts.append(f"<{tag}>{''.join(items)}</{tag}>")
            continue

        paragraph_lines: list[str] = []
        while idx < len(lines):
            current = lines[idx].strip()
            if not current:
                break
            if paragraph_lines and _is_markdown_block_start(lines, idx):
                break
            paragraph_lines.append(lines[idx])
            idx += 1
        parts.append(_render_markdown_paragraph(paragraph_lines))

    return "".join(parts)


def _render_ai_summary(
    summary_text: str | None,
    *,
    title: str,
    anchor_id: str | None = None,
) -> str:
    if not summary_text:
        return ""
    summary_text = summary_text.strip()
    if not summary_text:
        return ""
    content = _render_markdown_blocks(summary_text)
    section_id = f' id="{escape(anchor_id)}"' if anchor_id else ""
    return f"""
    <section{section_id} class="ai-card anchor-section">
      <h2>{escape(title)}</h2>
      <div class="ai-content">{content}</div>
      <p class="ai-note">仅供参考，不构成投资建议。</p>
    </section>
    """


def _render_ai_warning(
    message: str | None,
    *,
    title: str,
    anchor_id: str | None = None,
) -> str:
    if not message:
        return ""
    section_id = f' id="{escape(anchor_id)}"' if anchor_id else ""
    return f"""
    <section{section_id} class="ai-warning-card anchor-section">
      <h2>{escape(title)}</h2>
      <p>{escape(message)}</p>
    </section>
    """


def _render_tab_bar(
    trade_date: str,
    has_weekly_ai: bool,
    has_daily_ai: bool,
    has_sector_bridge: bool,
    has_sector_bridge_ai: bool,
) -> str:
    tabs = [("summary", "摘要")]
    if has_weekly_ai:
        tabs.append(("weekly-ai", "近一周AI"))
    if has_daily_ai:
        tabs.append(("daily-ai", "当日AI"))
    if has_sector_bridge:
        tabs.append(("sector-bridge", "板块强度"))
    if has_sector_bridge_ai:
        tabs.append(("sector-bridge-ai", "桥接AI"))
    tabs.extend(
        [
            ("window-1", trade_date),
            ("window-3", "近3日"),
            ("window-5", "近5日"),
            ("window-10", "近10日"),
            ("window-20", "近20日"),
            ("fund-rank", "基金排行"),
            ("components", "成分股"),
            ("funds", "基金"),
        ]
    )
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


def _render_fund_link(record: FundRankRecord) -> str:
    fund_code = record.fund_code.strip()
    if not re.fullmatch(r"\d{6}", fund_code):
        return escape(record.fund_name)
    fund_url = EASTMONEY_FUND_DETAIL_URL.format(fund_code=fund_code)
    return (
        f"<a class='fund-link' href='{escape(fund_url)}' target='_blank' rel='noreferrer noopener'>"
        f"{escape(record.fund_name)}</a>"
    )


def _fund_rank_active_value(record: FundRankRecord, ranking_period: str) -> float | None:
    if ranking_period == "day":
        return record.daily_growth_pct
    if ranking_period == "week":
        return record.weekly_growth_pct
    return record.monthly_growth_pct


def _fmt_net_value(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _render_holding_chips(holdings: list[FundHoldingRecord]) -> str:
    if not holdings:
        return "<span class='empty-text'>暂无</span>"
    return "".join(
        f"<span class='holding-chip' title='占净值比例 {_fmt_pct(holding.net_value_ratio)}'>"
        f"{escape(holding.stock_name)}</span>"
        for holding in holdings[:5]
    )


def _render_fund_rank_table(
    section: FundRankSection,
    fund_holdings: dict[str, list[FundHoldingRecord]],
) -> str:
    rows = []
    for record in section.records:
        active_value = _fund_rank_active_value(record, section.ranking_period)
        holdings = fund_holdings.get(record.fund_code, [])
        rows.append(
            f"""
            <tr>
              <td>{record.rank_no or "-"}</td>
              <td>
                <div>{_render_fund_link(record)}</div>
                <span class="fund-code">{escape(record.fund_code)}</span>
              </td>
              <td>{escape(record.net_value_date or "-")}</td>
              <td>{_fmt_net_value(record.unit_net_value)}</td>
              <td class="fund-rank-active">{_fmt_pct(active_value)}</td>
              <td><div class="holding-row">{_render_holding_chips(holdings)}</div></td>
            </tr>
            """
        )

    note_html = f"<p class='note'>{escape(section.note)}</p>" if section.note else ""
    body = "\n".join(rows) if rows else "<tr><td colspan='6'>暂无数据</td></tr>"
    return f"""
    <div class="table-card">
      <div class="table-head">
        <h3>{escape(section.title)}</h3>
        <span class="source">{escape(section.value_label)}排序</span>
      </div>
      {note_html}
      <table>
        <thead>
          <tr>
            <th>排名</th>
            <th>基金</th>
            <th>净值日期</th>
            <th>单位净值</th>
            <th>{escape(section.value_label)}</th>
            <th>前五持仓</th>
          </tr>
        </thead>
        <tbody>
          {body}
        </tbody>
      </table>
    </div>
    """


def _render_fund_rank_sections(payload: dict) -> str:
    sections = payload["fund_rank_sections"]
    fund_holdings = payload.get("fund_holdings", {})
    snapshot_date = payload.get("fund_rank_snapshot_date") or payload["trade_date"]
    return f"""
    <section id="fund-rank" class="section-card anchor-section">
      <div class="section-head">
        <h2>基金排行榜</h2>
        {_render_source_badge(f"天天基金 {snapshot_date}", EASTMONEY_FUND_RANK_URL)}
      </div>
      <div class="rank-grid">
        {_render_fund_rank_table(sections["day"], fund_holdings)}
        {_render_fund_rank_table(sections["week"], fund_holdings)}
        {_render_fund_rank_table(sections["month"], fund_holdings)}
      </div>
    </section>
    """


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
    weekly_ai_summary_html = _render_ai_summary(
        payload.get("weekly_ai_summary"),
        title="AI 近一周综合分析",
        anchor_id="weekly-ai",
    )
    weekly_ai_warning_html = _render_ai_warning(
        payload.get("weekly_ai_warning"),
        title="AI 近一周综合分析未生成",
        anchor_id="weekly-ai",
    )
    ai_summary_html = _render_ai_summary(
        payload.get("ai_summary"),
        title="AI 当日归类参考",
        anchor_id="daily-ai",
    )
    ai_warning_html = _render_ai_warning(
        payload.get("ai_summary_warning"),
        title="AI 当日归类参考未生成",
        anchor_id="daily-ai",
    )
    sector_bridge_ai_summary_html = _render_ai_summary(
        payload.get("sector_bridge_ai_summary"),
        title="AI 基金-板块综合解读",
        anchor_id="sector-bridge-ai",
    )
    sector_bridge_ai_warning_html = _render_ai_warning(
        payload.get("sector_bridge_ai_warning"),
        title="AI 基金-板块综合解读未生成",
        anchor_id="sector-bridge-ai",
    )
    repeated_focus_html = (
        "".join(
            _render_plain_chip(
                label,
                payload["repeated_focus_heat"].get(label, 1),
                payload["repeated_focus_occurrences"].get(label, []),
            )
            for label in payload["repeated_focus"]
        )
        if payload["repeated_focus"]
        else "<span class='empty-text'>暂无</span>"
    )
    persistent_focus_html = (
        "".join(
            _render_plain_chip(
                label,
                payload["persistent_focus_heat"].get(label, 1),
                payload["persistent_focus_occurrences"].get(label, []),
            )
            for label in payload["persistent_focus"]
        )
        if payload["persistent_focus"]
        else "<span class='empty-text'>暂无</span>"
    )
    has_weekly_ai = bool(weekly_ai_summary_html or weekly_ai_warning_html)
    has_daily_ai = bool(ai_summary_html or ai_warning_html)
    has_sector_bridge = bool(payload.get("sector_bridge"))
    has_sector_bridge_ai = bool(sector_bridge_ai_summary_html or sector_bridge_ai_warning_html)
    tab_bar_html = _render_tab_bar(
        payload["trade_date"],
        has_weekly_ai,
        has_daily_ai,
        has_sector_bridge,
        has_sector_bridge_ai,
    )
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
            {repeated_focus_html}
          </div>
        </div>
        <div class="summary-card summary-card-dark">
          <h3>跨周期持续热点</h3>
          <div class="chip-row">
            {persistent_focus_html}
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
    fund_rank_html = _render_fund_rank_sections(payload)
    sector_bridge_html = render_sector_bridge_section(payload.get("sector_bridge", {}))

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
        .ai-top-stack {{ display:grid; gap:12px; margin-top:12px; }}
        .summary-card,.warning-card,.window-card,.section-card {{ background:#fff; color:#1f2937; border-radius:16px; padding:14px; box-shadow:0 10px 24px rgba(15,23,42,.06); }}
        .summary-card h3,.warning-card h2,.window-card h2,.section-card h2 {{ margin:0 0 12px; }}
        .summary-card-dark {{ background:rgba(255,255,255,.12); color:#fff; border:1px solid rgba(255,255,255,.18); box-shadow:none; }}
        .summary-card-dark .empty-text {{ color:rgba(255,255,255,.72); }}
        .warning-card {{ margin-top:12px; border:1px solid #fecaca; background:#fff7ed; }}
        .warning-card ul {{ margin:0; padding-left:18px; color:#9a3412; line-height:1.7; }}
        .ai-card,.ai-warning-card {{ margin-top:12px; border-radius:16px; padding:14px; box-shadow:0 10px 24px rgba(15,23,42,.06); }}
        .ai-card {{ border:1px solid #bfdbfe; background:#eff6ff; color:#1e3a8a; }}
        .ai-card h2 {{ margin:0 0 10px; color:#1d4ed8; }}
        .ai-warning-card {{ border:1px solid #fcd34d; background:#fffbeb; color:#92400e; }}
        .ai-warning-card h2 {{ margin:0 0 8px; color:#b45309; }}
        .ai-warning-card p {{ margin:0; line-height:1.7; }}
        .ai-content {{ display:grid; gap:10px; color:#1e3a8a; }}
        .ai-content h3,.ai-content h4,.ai-content h5,.ai-content h6 {{ margin:8px 0 0; color:#1d4ed8; line-height:1.35; }}
        .ai-content h3 {{ font-size:17px; }}
        .ai-content h4 {{ font-size:15px; }}
        .ai-content h5 {{ font-size:14px; }}
        .ai-content h6 {{ font-size:13px; }}
        .ai-content p {{ margin:0; line-height:1.8; color:#1e293b; }}
        .ai-content ol,.ai-content ul {{ margin:0; padding-left:22px; line-height:1.8; display:grid; gap:6px; }}
        .ai-content li {{ margin:0; color:#1e293b; }}
        .ai-content strong {{ color:#172554; font-weight:800; }}
        .ai-content em {{ color:#1d4ed8; font-style:italic; }}
        .ai-content code {{ display:inline-block; padding:1px 6px; border-radius:6px; background:rgba(29,78,216,.12); color:#172554; font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:12px; }}
        .ai-inline-link {{ color:#1d4ed8; text-decoration:none; font-weight:700; }}
        .ai-inline-link:hover {{ text-decoration:underline; }}
        .ai-divider {{ margin:2px 0; border:none; border-top:1px dashed #93c5fd; }}
        .ai-content blockquote {{ margin:0; padding:10px 12px; border-left:4px solid #60a5fa; border-radius:12px; background:rgba(255,255,255,.55); color:#1e3a8a; }}
        .ai-content blockquote > :first-child {{ margin-top:0; }}
        .ai-content blockquote > :last-child {{ margin-bottom:0; }}
        .ai-code-block {{ overflow:hidden; border:1px solid rgba(29,78,216,.18); border-radius:12px; background:#0f172a; box-shadow:inset 0 1px 0 rgba(255,255,255,.04); }}
        .ai-code-lang {{ padding:8px 12px; background:rgba(255,255,255,.06); color:#bfdbfe; font-size:11px; font-weight:700; letter-spacing:.08em; text-transform:uppercase; }}
        .ai-code-block pre {{ margin:0; padding:12px; overflow:auto; }}
        .ai-code-block pre code {{ display:block; padding:0; border-radius:0; background:none; color:#e2e8f0; font-size:12px; line-height:1.65; }}
        .ai-table-wrap {{ overflow:auto; border:1px solid #bfdbfe; border-radius:12px; background:rgba(255,255,255,.78); }}
        .ai-table {{ width:100%; min-width:420px; border-collapse:collapse; }}
        .ai-table th,.ai-table td {{ padding:8px 10px; border-bottom:1px solid #dbeafe; text-align:left; vertical-align:top; font-size:13px; line-height:1.6; color:#1e293b; }}
        .ai-table th {{ background:rgba(191,219,254,.35); color:#1d4ed8; font-weight:700; }}
        .ai-table tr:last-child td {{ border-bottom:none; }}
        .ai-note {{ margin:10px 0 0; font-size:12px; color:#475569; }}
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
        .source-link,.sector-link,.stock-link,.fund-link {{ color:#1d4ed8; text-decoration:none; }}
        .source-link:hover,.sector-link:hover,.stock-link:hover,.fund-link:hover {{ text-decoration:underline; }}
        .rank-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; }}
        .fund-code {{ display:block; margin-top:2px; color:#64748b; font-size:12px; }}
        .fund-rank-active {{ color:#b91c1c; font-weight:800; }}
        .holding-row {{ display:flex; flex-wrap:wrap; gap:4px; max-width:260px; }}
        .holding-chip {{ display:inline-flex; max-width:96px; padding:2px 6px; border-radius:999px; background:#f1f5f9; color:#334155; font-size:12px; line-height:1.5; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
        .note {{ margin:0 0 10px; color:#9a3412; font-size:12px; line-height:1.6; }}
        .component-grid,.fund-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:10px; }}
        .bridge-two-col {{ display:grid; grid-template-columns:minmax(0,1.2fr) minmax(0,0.8fr); gap:12px; align-items:start; margin-top:12px; }}
        .bridge-column {{ display:grid; gap:10px; min-width:0; }}
        .bridge-sector-list {{ display:grid; gap:10px; }}
        .bridge-fund-table-wrap {{ overflow:auto; border:1px solid #e5e7eb; border-radius:14px; background:#fff; }}
        .bridge-stat {{ font-size:28px; font-weight:800; color:#0f172a; line-height:1.1; }}
        .bridge-fund-detail {{ display:block; margin-top:4px; color:#64748b; font-size:12px; line-height:1.6; }}
        .component-card,.fund-card {{ border:1px solid #e5e7eb; border-radius:14px; padding:10px 12px; background:#fff; }}
        .component-head {{ margin-bottom:8px; font-weight:700; }}
        .fund-meta {{ margin:0; color:#475569; font-size:12px; line-height:1.6; }}
        .fund-card ul {{ margin:8px 0 0; padding-left:18px; }}
        .fund-card li {{ line-height:1.6; font-size:13px; }}
        .footer {{ margin:12px 0 0; color:#64748b; font-size:12px; }}
        @media (max-width: 1080px) {{
          .hero-head,.hero-panel-grid,.summary-grid,.table-grid,.rank-grid,.bridge-two-col {{ grid-template-columns:1fr; }}
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
        <section class="ai-top-stack">
          {sector_bridge_ai_summary_html}
          {sector_bridge_ai_warning_html}
          {ai_summary_html}
          {ai_warning_html}
          {weekly_ai_summary_html}
          {weekly_ai_warning_html}
        </section>
        {warnings_html}
        {tab_bar_html}
        {windows_html}
        {fund_rank_html}
        {sector_bridge_html}
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
