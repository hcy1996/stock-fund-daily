from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.sector_strength.data_fetcher import BoardMarketSnapshot
from app.sector_strength.indicator_calculator import BoardIndicators


WEIGHTS = {
    "fund_score": 0.25,
    "RS_score": 0.20,
    "trend_score": 0.15,
    "breadth_score": 0.15,
    "volume_score": 0.10,
    "leader_score": 0.10,
}
GRADE_RULES = (
    ("S", "极强", "高景气强趋势", 85.0),
    ("A", "强势", "趋势占优可跟踪", 75.0),
    ("B", "偏强", "有结构亮点", 60.0),
    ("C", "震荡", "观察等待确认", 45.0),
    ("D", "弱势", "风险收益比偏弱", 0.0),
)
SUB_SCORE_KEYS = (
    "fund_score",
    "RS_score",
    "trend_score",
    "breadth_score",
    "volume_score",
    "leader_score",
)


@dataclass(slots=True)
class BoardScoreResult:
    total_score: float | None
    weighted_score: float | None
    available_weight: float
    risk_penalty: float | None
    suggestion: str
    sub_scores: dict[str, float | None]
    leader_stock_index: int | None
    grade_code: str
    grade_label: str
    grade_description: str
    risk_flags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _ScoreComputation:
    result: dict[str, float | int | None]
    weighted_score: float | None
    risk_flags: list[str]


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _tail_numbers(values: Any, window_days: int) -> list[float] | None:
    """Return the latest N numeric values, or None when length/value requirements fail."""
    if not isinstance(values, list) or len(values) < window_days:
        return None
    tail = values[-window_days:]
    if any(value is None for value in tail):
        return None
    return [float(value) for value in tail]


def _safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def _mean(values: list[float] | None) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _score_volume(vol_ratio: float | None) -> float | None:
    """Score turnover activity with a continuous 0-1 mapping around the target range."""
    if vol_ratio is None:
        return None
    if 1.2 <= vol_ratio <= 2:
        return 1.0
    if vol_ratio < 1.2:
        return _clamp(vol_ratio / 1.2)
    return _clamp(1 - (vol_ratio - 2))


def _select_leader(stock_pct_chg: list[Any], stock_amount: list[Any], stock_turnover: list[Any]) -> tuple[int | None, float | None]:
    """Select the leader by top-5 amount then max pct_change, and return its index plus score."""
    ranked = [
        (index, float(pct), float(amount), float(turnover))
        for index, (pct, amount, turnover) in enumerate(zip(stock_pct_chg, stock_amount, stock_turnover))
        if pct is not None and amount is not None and turnover is not None
    ]
    if len(ranked) < 5:
        return None, None
    top_amount = sorted(ranked, key=lambda item: item[2], reverse=True)[:5]
    leader_index, leader_pct, _, leader_turnover = max(top_amount, key=lambda item: item[1])
    ret = leader_pct / 100
    turn = leader_turnover / 100
    ret_score = _clamp(ret / 0.10)
    turn_score = _clamp(1 - abs(turn - 0.25) / 0.25)
    return leader_index, _clamp(0.6 * ret_score + 0.4 * turn_score)


def _compute_sector_score(data: dict[str, Any]) -> _ScoreComputation:
    sector_close = data.get("sector_close") or []
    sector_amount = data.get("sector_amount") or []
    sector_netflow = data.get("sector_netflow") or []
    index_close = data.get("index_close") or []
    stock_pct_chg = data.get("stock_pct_chg") or []
    stock_amount = data.get("stock_amount") or []
    stock_turnover = data.get("stock_turnover") or []
    limit_up_count = data.get("limit_up_count")

    result: dict[str, float | int | None] = {
        "score": None,
        "fund_score": None,
        "RS_score": None,
        "trend_score": None,
        "breadth_score": None,
        "volume_score": None,
        "leader_score": None,
        "penalty": None,
        "leader_stock_index": None,
    }
    risk_flags: list[str] = []

    sector_close_6 = _tail_numbers(sector_close, 6)
    sector_close_21 = _tail_numbers(sector_close, 21)
    index_close_6 = _tail_numbers(index_close, 6)
    index_close_21 = _tail_numbers(index_close, 21)

    ret5 = None
    ret20 = None
    idx_ret5 = None
    idx_ret20 = None
    rs = None
    if sector_close_6 is not None and sector_close_6[0] != 0:
        ret5 = sector_close_6[-1] / sector_close_6[0] - 1
    if sector_close_21 is not None and sector_close_21[0] != 0:
        ret20 = sector_close_21[-1] / sector_close_21[0] - 1
    if index_close_6 is not None and index_close_6[0] != 0:
        idx_ret5 = index_close_6[-1] / index_close_6[0] - 1
    if index_close_21 is not None and index_close_21[0] != 0:
        idx_ret20 = index_close_21[-1] / index_close_21[0] - 1
    if None not in (ret5, ret20, idx_ret5, idx_ret20):
        rs = 0.4 * (ret5 - idx_ret5) + 0.6 * (ret20 - idx_ret20)
        result["RS_score"] = _clamp(rs / 0.10)

    netflow_5 = _tail_numbers(sector_netflow, 5)
    netflow_10 = _tail_numbers(sector_netflow, 10)
    amount_5 = _tail_numbers(sector_amount, 5)
    amount_10 = _tail_numbers(sector_amount, 10)
    if netflow_5 is not None and netflow_10 is not None and amount_5 is not None and amount_10 is not None:
        flow5 = sum(netflow_5)
        flow10 = sum(netflow_10)
        amt5 = sum(amount_5)
        amt10 = sum(amount_10)
        flow_days = sum(1 for value in netflow_5 if value > 0)
        flow5_ratio = _safe_ratio(flow5, amt5)
        flow10_ratio = _safe_ratio(flow10, amt10)
        if flow5_ratio is not None and flow10_ratio is not None:
            raw_fund_score = 0.5 * flow5_ratio + 0.3 * flow10_ratio + 0.2 * (flow_days / 5)
            result["fund_score"] = _clamp(raw_fund_score / 0.05)

    close_5 = _tail_numbers(sector_close, 5)
    close_10 = _tail_numbers(sector_close, 10)
    close_20 = _tail_numbers(sector_close, 20)
    close_60 = _tail_numbers(sector_close, 60)
    bias20 = None
    if close_5 is not None and close_10 is not None and close_20 is not None and close_60 is not None:
        ma5 = _mean(close_5)
        ma10 = _mean(close_10)
        ma20 = _mean(close_20)
        ma60 = _mean(close_60)
        if ma20 not in (None, 0):
            bias20 = (close_60[-1] - ma20) / ma20
        if None not in (ma5, ma10, ma20, ma60) and bias20 is not None:
            trend_score = 0.0
            if close_60[-1] > ma20:
                trend_score += 0.25
            if ma5 > ma10 > ma20:
                trend_score += 0.35
            if ma20 > ma60:
                trend_score += 0.25
            if bias20 < 0.15:
                trend_score += 0.15
            result["trend_score"] = _clamp(trend_score)

    valid_pct = [float(value) for value in stock_pct_chg if value is not None]
    up_ratio = None
    if valid_pct:
        total = len(valid_pct)
        up = sum(1 for value in valid_pct if value > 0)
        gt3 = sum(1 for value in valid_pct if value > 3)
        gt5 = sum(1 for value in valid_pct if value > 5)
        limit_ratio = None
        if isinstance(limit_up_count, int) and total > 0:
            limit_ratio = limit_up_count / total
        if limit_ratio is not None:
            up_ratio = up / total
            result["breadth_score"] = (
                0.4 * up_ratio
                + 0.25 * (gt3 / total)
                + 0.2 * (gt5 / total)
                + 0.15 * limit_ratio
            )

    amount_20 = _tail_numbers(sector_amount, 20)
    vol_ratio = None
    if amount_20 is not None:
        amount_mean_20 = _mean(amount_20)
        if amount_mean_20 not in (None, 0):
            vol_ratio = amount_20[-1] / amount_mean_20
            result["volume_score"] = _score_volume(vol_ratio)

    leader_stock_index, leader_score = _select_leader(stock_pct_chg, stock_amount, stock_turnover)
    result["leader_stock_index"] = leader_stock_index
    result["leader_score"] = leader_score

    penalty = 0.0
    if ret5 is not None and ret5 > 0.15:
        penalty += 0.1
        risk_flags.append("5日涨幅超过15%")
    if bias20 is not None and bias20 > 0.15:
        penalty += 0.1
        risk_flags.append("当前价格偏离MA20超过15%")
    if up_ratio is not None and up_ratio < 0.5:
        penalty += 0.15
        risk_flags.append("上涨家数占比低于50%")
    netflow_2 = _tail_numbers(sector_netflow, 2)
    if netflow_2 is not None and all(value < 0 for value in netflow_2):
        penalty += 0.15
        risk_flags.append("最近2日主力净流入连续为负")
    if vol_ratio is not None and vol_ratio > 3:
        penalty += 0.1
        risk_flags.append("量比超过3，存在放量过热风险")
    result["penalty"] = penalty

    sub_scores = [result[key] for key in SUB_SCORE_KEYS]
    if all(value is not None for value in sub_scores):
        weighted_score = sum(float(result[key]) * WEIGHTS[key] for key in SUB_SCORE_KEYS)
        result["score"] = _clamp(weighted_score - penalty, 0.0, 1.0) * 100
        return _ScoreComputation(result=result, weighted_score=weighted_score, risk_flags=risk_flags)

    return _ScoreComputation(result=result, weighted_score=None, risk_flags=risk_flags)


def _recompute_total_score(result: dict[str, float | int | None]) -> float | None:
    sub_scores = [result[key] for key in SUB_SCORE_KEYS]
    if not all(value is not None for value in sub_scores):
        return None
    weighted_score = sum(float(result[key]) * WEIGHTS[key] for key in SUB_SCORE_KEYS)
    result["score"] = _clamp(weighted_score - float(result["penalty"] or 0.0), 0.0, 1.0) * 100
    return weighted_score


def calc_sector_score(data: dict[str, Any]) -> dict[str, float | int | None]:
    """Calculate sector strength score strictly with the requested formula."""
    return _compute_sector_score(data).result


def build_suggestion(total_score: float | None) -> str:
    """Map final score to buy / observe / avoid suggestion."""
    if total_score is None:
        return "观察"
    if total_score >= 75:
        return "买入"
    if total_score >= 55:
        return "观察"
    return "回避"


def build_grade(total_score: float | None) -> tuple[str, str, str]:
    """Map score to a grade bucket and human-readable label."""
    if total_score is None:
        return "NA", "待定", "关键指标不足，暂不评级"
    for code, label, description, threshold in GRADE_RULES:
        if total_score >= threshold:
            return code, label, description
    return "NA", "待定", "关键指标不足，暂不评级"


def _build_score_input(snapshot: BoardMarketSnapshot) -> dict[str, Any]:
    return {
        "sector_close": [item.close for item in snapshot.board_kline_60],
        "sector_amount": [item.amount for item in snapshot.board_kline_60],
        "sector_netflow": [item.main_net_inflow for item in snapshot.fund_flow_hist_60],
        "index_close": [item.close for item in snapshot.benchmark_kline_60],
        "stock_pct_chg": [item.pct_change for item in snapshot.components],
        "stock_amount": [item.amount for item in snapshot.components],
        "stock_turnover": [item.turnover_rate for item in snapshot.components],
        "limit_up_count": sum(1 for item in snapshot.components if item.is_limit_up),
    }


def _parse_trade_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _pick_previous_snapshot_flow(snapshot: BoardMarketSnapshot, window_days: int) -> float | None:
    history = snapshot.candidate.snapshot_flow_history.get(window_days, [])
    current_trade_date = _parse_trade_date(snapshot.trade_date)
    if current_trade_date is None:
        return None
    for trade_date_text, value, _source in history:
        trade_date = _parse_trade_date(trade_date_text)
        if trade_date is None or trade_date >= current_trade_date:
            continue
        return value
    return None


def _is_reliable_snapshot_history(
    current_1d: float | None,
    current_3d: float | None,
    previous_1d: float | None,
    previous_3d: float | None,
) -> bool:
    if None in (current_1d, current_3d, previous_1d, previous_3d):
        return False
    return not (
        abs(float(current_1d) - float(previous_1d)) < 1e-6
        and abs(float(current_3d) - float(previous_3d)) < 1e-6
    )


def _derive_fund_score_from_snapshot(snapshot: BoardMarketSnapshot) -> tuple[float | None, bool]:
    amounts = [item.amount for item in snapshot.board_kline_60]
    amount_5 = _tail_numbers(amounts, 5)
    amount_10 = _tail_numbers(amounts, 10)
    current_1d = snapshot.fund_flow_1d.main_net_inflow if snapshot.fund_flow_1d else None
    current_3d = snapshot.fund_flow_3d.main_net_inflow if snapshot.fund_flow_3d else None
    current_5d = snapshot.fund_flow_5d.main_net_inflow if snapshot.fund_flow_5d else None
    current_10d = snapshot.fund_flow_10d.main_net_inflow if snapshot.fund_flow_10d else None
    previous_1d = _pick_previous_snapshot_flow(snapshot, 1)
    previous_3d = _pick_previous_snapshot_flow(snapshot, 3)
    if None in (current_1d, current_3d, current_5d, current_10d):
        return None, False
    if amount_5 is None or amount_10 is None:
        return None, False

    flow5_ratio = _safe_ratio(float(current_5d), sum(amount_5))
    flow10_ratio = _safe_ratio(float(current_10d), sum(amount_10))
    if flow5_ratio is None or flow10_ratio is None:
        return None, False

    if not _is_reliable_snapshot_history(current_1d, current_3d, previous_1d, previous_3d):
        flow_days = 1 if float(current_1d) > 0 else 0
        raw_fund_score = 0.5 * flow5_ratio + 0.3 * flow10_ratio + 0.2 * (flow_days / 5)
        return _clamp(raw_fund_score / 0.10), False

    day_0 = float(current_1d)
    day_1 = float(previous_1d)
    day_2 = float(current_3d) - day_0 - day_1
    day_3 = float(previous_3d) - day_1 - day_2
    day_4 = float(current_5d) - day_0 - day_1 - day_2 - day_3
    flow_days = sum(1 for value in [day_0, day_1, day_2, day_3, day_4] if value > 0)
    raw_fund_score = 0.5 * flow5_ratio + 0.3 * flow10_ratio + 0.2 * (flow_days / 5)
    return _clamp(raw_fund_score / 0.05), day_0 < 0 and day_1 < 0


def score_board(snapshot: BoardMarketSnapshot, indicators: BoardIndicators) -> BoardScoreResult:
    """Build board score result for report rendering and JSON export."""
    computation = _compute_sector_score(_build_score_input(snapshot))
    result = computation.result
    if result["fund_score"] is None:
        derived_fund_score, last_two_negative = _derive_fund_score_from_snapshot(snapshot)
        if derived_fund_score is not None:
            result["fund_score"] = derived_fund_score
            if result["penalty"] is None:
                result["penalty"] = 0.0
            if last_two_negative:
                result["penalty"] = float(result["penalty"]) + 0.15
                if "最近2日主力净流入连续为负" not in computation.risk_flags:
                    computation.risk_flags.append("最近2日主力净流入连续为负")
            computation.weighted_score = _recompute_total_score(result)

    available_weight = sum(WEIGHTS[key] for key in SUB_SCORE_KEYS if result.get(key) is not None)
    grade_code, grade_label, grade_description = build_grade(result["score"])

    return BoardScoreResult(
        total_score=result["score"],
        weighted_score=computation.weighted_score,
        available_weight=available_weight,
        risk_penalty=result["penalty"],
        suggestion=build_suggestion(result["score"]),
        sub_scores={key: result.get(key) for key in SUB_SCORE_KEYS},
        leader_stock_index=result["leader_stock_index"] if isinstance(result["leader_stock_index"], int) else None,
        grade_code=grade_code,
        grade_label=grade_label,
        grade_description=grade_description,
        risk_flags=computation.risk_flags,
    )
