from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
import re
import sqlite3

from app.sector_strength.data_fetcher import (
    BoardCandidate,
    BoardMarketSnapshot,
    fetch_benchmark_kline_60,
    fetch_board_market_snapshot,
    fetch_live_candidate_pool,
    load_board_registry_lookup,
    resolve_input_board,
)
from app.sector_strength.indicator_calculator import BoardIndicators, calculate_indicators
from app.sector_strength.report_renderer import render_html_report
from app.sector_strength.scoring_model import GRADE_RULES, WEIGHTS, BoardScoreResult, score_board
from app.storage import connect, get_window_records, init_db, latest_trade_date


SOURCE_PRIORITY = {
    "eastmoney": 0,
    "tonghuashun": 1,
    "eastmoney_history": 2,
}
NORMALIZE_NAME_PATTERN = re.compile(r"[\s()（）\-_/.]+")


@dataclass(slots=True)
class CandidatePoolInfo:
    trade_date: str | None
    source: str
    window_days: int | None
    limit: int
    concept_size: int = 0
    industry_size: int = 0


@dataclass(slots=True)
class BoardAnalysisResult:
    snapshot: BoardMarketSnapshot
    indicators: BoardIndicators
    score: BoardScoreResult


def _normalize_name(name: str) -> str:
    return NORMALIZE_NAME_PATTERN.sub("", name)


def _alias_keys(name: str, code: str | None) -> list[str]:
    keys: list[str] = []
    if code:
        keys.append(f"code:{code.upper()}")
    keys.append(f"name:{name}")
    keys.append(f"norm:{_normalize_name(name)}")
    return keys


def _choose_pool_rows(conn: sqlite3.Connection, limit: int) -> tuple[list[sqlite3.Row], CandidatePoolInfo, list[str]]:
    warnings: list[str] = []
    trade_date = latest_trade_date(conn)
    if not trade_date:
        warnings.append("SQLite 中没有现成候选池，已改走实时兜底候选池")
        return [], CandidatePoolInfo(trade_date=None, source="live-fallback", window_days=None, limit=limit), warnings

    for window_days, source in [(1, "tonghuashun"), (1, "eastmoney"), (5, "tonghuashun"), (5, "eastmoney")]:
        rows = get_window_records(conn, trade_date, window_days, source=source, limit=limit)
        if rows:
            return rows, CandidatePoolInfo(trade_date=trade_date, source=source, window_days=window_days, limit=limit), warnings

    warnings.append(f"{trade_date}: 未找到可用 1 日/5 日候选池，已改走实时兜底候选池")
    return [], CandidatePoolInfo(trade_date=trade_date, source="live-fallback", window_days=None, limit=limit), warnings


def _build_snapshot_flow_lookup(conn: sqlite3.Connection, trade_date: str | None) -> dict[str, dict[int, tuple[float | None, str]]]:
    lookup: dict[str, dict[int, tuple[float | None, str]]] = {}
    if not trade_date:
        return lookup

    rows = list(
        conn.execute(
            """
            SELECT trade_date, window_days, source, sector_code, sector_name, main_net_inflow
            FROM sector_flow_snapshots
            WHERE trade_date = ? AND window_days IN (1, 3, 5, 10)
            """,
            (trade_date,),
        )
    )
    for row in rows:
        window_days = int(row["window_days"])
        source = str(row["source"])
        for key in _alias_keys(str(row["sector_name"]), str(row["sector_code"]) or None):
            current = lookup.setdefault(key, {}).get(window_days)
            current_priority = SOURCE_PRIORITY.get(current[1], 99) if current else 99
            incoming_priority = SOURCE_PRIORITY.get(source, 99)
            if current is None or incoming_priority < current_priority:
                lookup[key][window_days] = (row["main_net_inflow"], source)
    return lookup


def _build_snapshot_flow_history_lookup(conn: sqlite3.Connection) -> dict[str, dict[int, list[tuple[str, float | None, str]]]]:
    lookup: dict[str, dict[int, list[tuple[str, float | None, str]]]] = {}
    trade_dates = [
        row["trade_date"]
        for row in conn.execute(
            """
            SELECT DISTINCT trade_date
            FROM sector_flow_snapshots
            WHERE trade_date <> '' AND window_days IN (1, 3)
            ORDER BY trade_date DESC
            LIMIT 2
            """
        )
    ]
    if not trade_dates:
        return lookup

    placeholders = ",".join("?" for _ in trade_dates)
    rows = list(
        conn.execute(
            f"""
            SELECT trade_date, window_days, source, sector_code, sector_name, main_net_inflow
            FROM sector_flow_snapshots
            WHERE trade_date IN ({placeholders}) AND window_days IN (1, 3)
            ORDER BY trade_date DESC
            """,
            trade_dates,
        )
    )
    for row in rows:
        window_days = int(row["window_days"])
        trade_date = str(row["trade_date"])
        source = str(row["source"])
        payload = (trade_date, row["main_net_inflow"], source)
        for key in _alias_keys(str(row["sector_name"]), str(row["sector_code"]) or None):
            history = lookup.setdefault(key, {}).setdefault(window_days, [])
            if any(item[0] == trade_date for item in history):
                continue
            history.append(payload)
    return lookup


def _snapshot_flows_for(name: str, code: str | None, flow_lookup: dict[str, dict[int, tuple[float | None, str]]]) -> tuple[dict[int, float | None], dict[int, str | None]]:
    flows: dict[int, float | None] = {}
    sources: dict[int, str | None] = {}
    for key in _alias_keys(name, code):
        for window_days, (value, source) in flow_lookup.get(key, {}).items():
            if window_days not in flows:
                flows[window_days] = value
                sources[window_days] = source
    return flows, sources


def _snapshot_flow_history_for(
    name: str,
    code: str | None,
    flow_history_lookup: dict[str, dict[int, list[tuple[str, float | None, str]]]],
) -> dict[int, list[tuple[str, float | None, str | None]]]:
    history: dict[int, list[tuple[str, float | None, str | None]]] = {}
    for key in _alias_keys(name, code):
        for window_days, items in flow_history_lookup.get(key, {}).items():
            bucket = history.setdefault(window_days, [])
            existing_dates = {item[0] for item in bucket}
            for item in items:
                if item[0] in existing_dates:
                    continue
                bucket.append(item)
                existing_dates.add(item[0])
    for items in history.values():
        items.sort(key=lambda item: item[0], reverse=True)
    return history


def _candidate_from_row(
    row: sqlite3.Row,
    flow_lookup: dict[str, dict[int, tuple[float | None, str]]],
    flow_history_lookup: dict[str, dict[int, list[tuple[str, float | None, str]]]],
    pool_info: CandidatePoolInfo,
) -> BoardCandidate:
    code_text = str(row["sector_code"]).strip() if row["sector_code"] is not None else None
    code = code_text or None
    if code and not code.upper().startswith("BK"):
        code = None
    name = str(row["sector_name"]).strip()
    snapshot_flows, snapshot_flow_sources = _snapshot_flows_for(name, code, flow_lookup)
    snapshot_flow_history = _snapshot_flow_history_for(name, code, flow_history_lookup)
    return BoardCandidate(
        name=name,
        code=code,
        board_type="concept",
        rank_no=row["rank_no"],
        trade_date=row["trade_date"],
        pool_source=pool_info.source,
        pool_window_days=pool_info.window_days,
        in_candidate_pool=True,
        snapshot_flows=snapshot_flows,
        snapshot_flow_sources=snapshot_flow_sources,
        snapshot_flow_history=snapshot_flow_history,
    )


def load_candidate_pool(config, limit: int = 50) -> tuple[list[BoardCandidate], CandidatePoolInfo, list[str]]:
    conn = connect(config.storage.db_path)
    init_db(conn)
    rows, pool_info, warnings = _choose_pool_rows(conn, limit)
    flow_lookup = _build_snapshot_flow_lookup(conn, pool_info.trade_date)
    flow_history_lookup = _build_snapshot_flow_history_lookup(conn)

    concept_candidates: list[BoardCandidate] = []
    if rows:
        concept_candidates = [
            _candidate_from_row(row, flow_lookup, flow_history_lookup, pool_info)
            for row in rows[:limit]
        ]
    else:
        live_candidates = fetch_live_candidate_pool(limit, board_type="concept")
        concept_candidates = []
        for item in live_candidates:
            snapshot_flows, snapshot_flow_sources = _snapshot_flows_for(item.name, item.code, flow_lookup)
            snapshot_flow_history = _snapshot_flow_history_for(item.name, item.code, flow_history_lookup)
            concept_candidates.append(
                BoardCandidate(
                    name=item.name,
                    code=item.code,
                    board_type=item.board_type,
                    rank_no=item.rank_no,
                    trade_date=item.trade_date or pool_info.trade_date,
                    pool_source=item.pool_source,
                    pool_window_days=item.pool_window_days,
                    in_candidate_pool=item.in_candidate_pool,
                    snapshot_flows=snapshot_flows,
                    snapshot_flow_sources=snapshot_flow_sources,
                    snapshot_flow_history=snapshot_flow_history,
                )
            )
    industry_candidates = fetch_live_candidate_pool(limit, board_type="industry")
    if not industry_candidates:
        warnings.append("行业候选池抓取失败，当前仅使用概念板块候选池")
    candidates = _deduplicate_candidates(concept_candidates + industry_candidates)
    pool_info.concept_size = len(concept_candidates)
    pool_info.industry_size = len(industry_candidates)
    if industry_candidates:
        pool_info.source = f"{pool_info.source}+industry-live"
    conn.close()
    return candidates, pool_info, warnings


def _deduplicate_candidates(candidates: list[BoardCandidate]) -> list[BoardCandidate]:
    ordered: dict[str, BoardCandidate] = {}
    for candidate in candidates:
        key = f"{candidate.code or ''}:{candidate.name}"
        if key not in ordered:
            ordered[key] = candidate
    return list(ordered.values())


def _find_pool_candidate(query: str, candidates: list[BoardCandidate]) -> BoardCandidate | None:
    text = query.strip()
    if not text:
        return None
    for candidate in candidates:
        if candidate.code and candidate.code.upper() == text.upper():
            return candidate
        if candidate.name == text:
            return candidate

    fuzzy: list[BoardCandidate] = []
    for candidate in candidates:
        if text in candidate.name:
            fuzzy.append(candidate)
    if len(fuzzy) == 1:
        return fuzzy[0]
    return None


def build_analysis_candidates(
    base_candidates: list[BoardCandidate],
    board_queries: list[str] | None,
    pool_info: CandidatePoolInfo,
) -> tuple[list[BoardCandidate], list[str], list[str]]:
    warnings: list[str] = []
    focus_names: list[str] = []
    if not board_queries:
        return _deduplicate_candidates(base_candidates), warnings, focus_names

    existing_candidates = _deduplicate_candidates(base_candidates)
    flow_lookup: dict[str, dict[int, tuple[float | None, str]]] = {}
    for candidate in existing_candidates:
        snapshot_flows, snapshot_sources = candidate.snapshot_flows, candidate.snapshot_flow_sources
        for key in _alias_keys(candidate.name, candidate.code):
            flow_lookup[key] = {
                window_days: (snapshot_flows.get(window_days), snapshot_sources.get(window_days) or "snapshot")
                for window_days in snapshot_flows
            }

    extra_candidates: list[BoardCandidate] = []
    lookup = None
    for query in board_queries:
        existing = _find_pool_candidate(query, existing_candidates)
        if existing is not None:
            focus_names.append(existing.name)
            continue

        if lookup is None:
            lookup = load_board_registry_lookup()
        entry, query_warnings = resolve_input_board(query, lookup)
        warnings.extend(query_warnings)
        if entry is None:
            continue
        focus_names.append(entry.name)
        snapshot_flows, snapshot_flow_sources = _snapshot_flows_for(entry.name, entry.code, flow_lookup)
        extra_candidates.append(
            BoardCandidate(
                name=entry.name,
                code=entry.code,
                board_type=entry.board_type,
                rank_no=None,
                trade_date=pool_info.trade_date,
                pool_source="input",
                pool_window_days=None,
                in_candidate_pool=False,
                snapshot_flows=snapshot_flows,
                snapshot_flow_sources=snapshot_flow_sources,
                snapshot_flow_history={},
            )
        )

    combined = _deduplicate_candidates(existing_candidates + extra_candidates)
    return combined, warnings, focus_names


def _sort_results(results: list[BoardAnalysisResult]) -> list[BoardAnalysisResult]:
    return sorted(
        results,
        key=lambda item: (
            item.score.total_score is None,
            -(item.score.total_score or -1),
            item.snapshot.candidate.rank_no or 9999,
            item.indicators.board_name,
        ),
    )


def _format_number(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "null"
    return f"{value:.{digits}f}"


def _format_raw_score(value: float | None) -> str:
    if value is None:
        return "null"
    return f"{value:.4f}"


def _score_points(value: float | None) -> float | None:
    if value is None:
        return None
    return value * 100


def _render_ranking_lines(results: list[BoardAnalysisResult], limit: int | None = None) -> list[str]:
    lines = ["排名 | 板块 | 等级 | 评分 | 建议 | RS20 | 10日净流入占比% | 评分详情 | 领涨候选"]
    subset = results if limit is None else results[:limit]
    for index, item in enumerate(subset, start=1):
        leader_name = item.indicators.leader_candidate.stock_name if item.indicators.leader_candidate else "-"
        detail = (
            f"资{_format_raw_score(item.score.sub_scores.get('fund_score'))}/"
            f"RS{_format_raw_score(item.score.sub_scores.get('RS_score'))}/"
            f"趋{_format_raw_score(item.score.sub_scores.get('trend_score'))}/"
            f"广{_format_raw_score(item.score.sub_scores.get('breadth_score'))}/"
            f"量{_format_raw_score(item.score.sub_scores.get('volume_score'))}/"
            f"龙{_format_raw_score(item.score.sub_scores.get('leader_score'))}/"
            f"罚{_format_raw_score(item.score.risk_penalty)}"
        )
        lines.append(
            f"{index:>2} | {item.indicators.board_name} | {item.score.grade_code} | {_format_number(item.score.total_score)} | "
            f"{item.score.suggestion} | {_format_number(item.indicators.rs_20d)} | "
            f"{_format_number(item.indicators.inflow_10d_ratio)} | {detail} | {leader_name}"
        )
    return lines


def _render_board_detail(item: BoardAnalysisResult) -> list[str]:
    indicators = item.indicators
    score = item.score
    lines = [
        f"{indicators.board_name} ({indicators.board_code or '-'})",
        (
            f"等级: {score.grade_code} / {score.grade_label} | "
            f"评分: {_format_number(score.total_score)} | "
            f"建议: {score.suggestion} | "
            f"风险惩罚: {_format_number(_score_points(score.risk_penalty))}"
        ),
        (
            "指标: "
            f"5日涨幅={_format_number(indicators.board_return_5d)}%, "
            f"20日涨幅={_format_number(indicators.board_return_20d)}%, "
            f"HS300_5日={_format_number(indicators.hs300_return_5d)}%, "
            f"HS300_20日={_format_number(indicators.hs300_return_20d)}%, "
            f"RS_5日={_format_number(indicators.rs_5d)}, "
            f"RS_20日={_format_number(indicators.rs_20d)}"
        ),
        (
            "均线/量能: "
            f"close={_format_number(indicators.close)}, "
            f"MA5={_format_number(indicators.ma5)}, "
            f"MA10={_format_number(indicators.ma10)}, "
            f"MA20={_format_number(indicators.ma20)}, "
            f"MA60={_format_number(indicators.ma60)}, "
            f"量比20日={_format_number(indicators.amount_ratio_20d)}"
        ),
        (
            "广度/资金: "
            f"上涨占比={_format_number(None if indicators.up_ratio is None else indicators.up_ratio * 100)}%, "
            f">3%占比={_format_number(None if indicators.gt3_ratio is None else indicators.gt3_ratio * 100)}%, "
            f">5%占比={_format_number(None if indicators.gt5_ratio is None else indicators.gt5_ratio * 100)}%, "
            f"涨停密度={_format_number(None if indicators.limit_up_density is None else indicators.limit_up_density * 100)}%, "
            f"5日净流入占比={_format_number(indicators.inflow_5d_ratio)}%, "
            f"10日净流入占比={_format_number(indicators.inflow_10d_ratio)}%"
        ),
    ]
    if indicators.leader_candidate is not None:
        leader = indicators.leader_candidate
        lines.append(
            "龙头候选: "
            f"{leader.stock_name}({leader.stock_code}) "
            f"涨幅={_format_number(leader.pct_change)}%, "
            f"成交额={_format_number(leader.amount)}, "
            f"换手率={_format_number(leader.turnover_rate)}%, "
            f"涨停={'是' if leader.is_limit_up else '否'}"
        )
    if score.risk_flags:
        lines.append("风险提示: " + "；".join(score.risk_flags))
    if indicators.warnings:
        lines.append("数据告警: " + "；".join(indicators.warnings))
    lines.append(
        "打分拆解: "
        f"资金持续性={_format_raw_score(score.sub_scores.get('fund_score'))}*25%, "
        f"相对强度={_format_raw_score(score.sub_scores.get('RS_score'))}*20%, "
        f"趋势结构={_format_raw_score(score.sub_scores.get('trend_score'))}*15%, "
        f"板块广度={_format_raw_score(score.sub_scores.get('breadth_score'))}*15%, "
        f"成交活跃度={_format_raw_score(score.sub_scores.get('volume_score'))}*10%, "
        f"龙头质量={_format_raw_score(score.sub_scores.get('leader_score'))}*10%, "
        f"基准分={_format_number(_score_points(score.weighted_score))}, "
        f"风险惩罚={_format_number(_score_points(score.risk_penalty))}, "
        f"最终分={_format_number(score.total_score)}"
    )
    return lines


def render_summary_text(payload: dict) -> str:
    lines = [
        f"生成时间: {payload['generated_at']}",
        (
            "候选池: "
            f"trade_date={payload['candidate_pool']['trade_date']}, "
            f"source={payload['candidate_pool']['source']}, "
            f"window_days={payload['candidate_pool']['window_days']}, "
            f"size={payload['candidate_pool']['size']}, "
            f"concept_size={payload['candidate_pool']['concept_size']}, "
            f"industry_size={payload['candidate_pool']['industry_size']}"
        ),
    ]
    if payload["warnings"]:
        lines.append("全局告警: " + "；".join(payload["warnings"]))
    if payload["focus_inputs"]:
        lines.append("输入查询: " + ", ".join(payload["focus_inputs"]))
    lines.append("")
    lines.extend(_render_ranking_lines(payload["results"], limit=None))
    lines.append("")

    focus_names = set(payload["focus_board_names"])
    if focus_names:
        lines.append("重点板块明细")
        for item in payload["results"]:
            if item.indicators.board_name not in focus_names:
                continue
            lines.extend(_render_board_detail(item))
            lines.append("")
    else:
        lines.append("前10板块明细")
        for item in payload["results"][:10]:
            lines.extend(_render_board_detail(item))
            lines.append("")
    return "\n".join(lines).strip() + "\n"


def serialize_sector_strength_payload(payload: dict) -> dict:
    return {
        "generated_at": payload["generated_at"],
        "candidate_pool": payload["candidate_pool"],
        "focus_inputs": payload["focus_inputs"],
        "focus_board_names": payload["focus_board_names"],
        "warnings": payload["warnings"],
        "score_weights": WEIGHTS,
        "grade_rules": [
            {
                "grade_code": code,
                "grade_label": label,
                "grade_description": description,
                "min_score": threshold,
            }
            for code, label, description, threshold in GRADE_RULES
        ],
        "results": [
            {
                "snapshot": asdict(item.snapshot),
                "indicators": asdict(item.indicators),
                "score": asdict(item.score),
            }
            for item in payload["results"]
        ],
    }


def _save_outputs(config, trade_date: str, payload: dict, summary_text: str) -> tuple[Path, Path, Path]:
    output_dir = config.storage.output_dir / "sector-strength" / trade_date
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "result.json"
    summary_path = output_dir / "summary.txt"
    html_path = output_dir / "report.html"
    json_path.write_text(
        json.dumps(serialize_sector_strength_payload(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary_path.write_text(summary_text, encoding="utf-8")
    html_path.write_text(render_html_report(payload), encoding="utf-8")
    return json_path, summary_path, html_path


def build_sector_strength_payload(
    config,
    board_queries: list[str] | None = None,
    candidate_limit: int = 50,
) -> tuple[str, dict, str]:
    """Run sector-strength analysis and return trade_date, internal payload and summary text."""
    candidates, pool_info, warnings = load_candidate_pool(config, limit=candidate_limit)
    analysis_candidates, query_warnings, focus_board_names = build_analysis_candidates(candidates, board_queries, pool_info)
    warnings.extend(query_warnings)
    if not analysis_candidates:
        raise RuntimeError("没有可分析的板块。请先抓取候选池，或传入有效板块名称/BK代码。")

    benchmark_kline_60, benchmark_source = fetch_benchmark_kline_60()
    if not benchmark_kline_60:
        raise RuntimeError("沪深300近 60 日 K 线获取失败，无法继续评分。")

    lookup = None
    if any(not candidate.code or not candidate.board_type for candidate in analysis_candidates):
        lookup = load_board_registry_lookup()
    results: list[BoardAnalysisResult] = []
    with ThreadPoolExecutor(max_workers=min(4, max(2, len(analysis_candidates)))) as executor:
        future_map = {
            executor.submit(fetch_board_market_snapshot, candidate, lookup, benchmark_kline_60, benchmark_source): candidate
            for candidate in analysis_candidates
        }
        for future in as_completed(future_map):
            candidate = future_map[future]
            try:
                snapshot = future.result()
            except Exception as exc:
                snapshot = BoardMarketSnapshot(
                    candidate=candidate,
                    resolved_name=candidate.name,
                    resolved_code=candidate.code,
                    board_type=candidate.board_type,
                    trade_date=candidate.trade_date,
                    board_kline_60=[],
                    benchmark_kline_60=benchmark_kline_60,
                    fund_flow_hist_60=[],
                    fund_flow_1d=None,
                    fund_flow_3d=None,
                    fund_flow_5d=None,
                    fund_flow_10d=None,
                    components=[],
                    data_sources={"benchmark": benchmark_source},
                    warnings=[f"{candidate.name}: 抓数异常 {exc}"],
                )
            indicators = calculate_indicators(snapshot)
            score = score_board(snapshot, indicators)
            results.append(BoardAnalysisResult(snapshot=snapshot, indicators=indicators, score=score))

    results = _sort_results(results)
    trade_date = next((item.indicators.trade_date for item in results if item.indicators.trade_date), None)
    if not trade_date:
        trade_date = pool_info.trade_date or datetime.now().date().isoformat()

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "candidate_pool": {
            "trade_date": pool_info.trade_date,
            "source": pool_info.source,
            "window_days": pool_info.window_days,
            "size": len(candidates),
            "concept_size": pool_info.concept_size,
            "industry_size": pool_info.industry_size,
        },
        "focus_inputs": board_queries or [],
        "focus_board_names": focus_board_names,
        "warnings": warnings,
        "results": results,
    }
    summary_text = render_summary_text(payload)
    return trade_date, payload, summary_text


def get_sector_strength_analysis_json(
    config,
    board_queries: list[str] | None = None,
    candidate_limit: int = 50,
) -> tuple[str, dict, str]:
    """Return a JSON-serializable sector-strength analysis payload without writing files."""
    trade_date, payload, summary_text = build_sector_strength_payload(
        config=config,
        board_queries=board_queries,
        candidate_limit=candidate_limit,
    )
    return trade_date, serialize_sector_strength_payload(payload), summary_text


def run_sector_strength_analysis(config, board_queries: list[str] | None = None, candidate_limit: int = 50) -> tuple[Path, Path, Path, str]:
    trade_date, payload, summary_text = build_sector_strength_payload(
        config=config,
        board_queries=board_queries,
        candidate_limit=candidate_limit,
    )
    json_path, summary_path, html_path = _save_outputs(config, trade_date, payload, summary_text)
    return json_path, summary_path, html_path, summary_text
