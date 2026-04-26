from __future__ import annotations

from dataclasses import dataclass, field

from app.sector_strength.data_fetcher import BoardComponent, BoardMarketSnapshot, KlineBar


@dataclass(slots=True)
class LeaderCandidate:
    stock_code: str
    stock_name: str
    pct_change: float | None
    amount: float | None
    turnover_rate: float | None
    is_limit_up: bool


@dataclass(slots=True)
class BoardIndicators:
    board_name: str
    board_code: str | None
    board_type: str | None
    trade_date: str | None
    close: float | None
    board_return_5d: float | None
    board_return_20d: float | None
    hs300_return_5d: float | None
    hs300_return_20d: float | None
    rs_5d: float | None
    rs_20d: float | None
    ma5: float | None
    ma10: float | None
    ma20: float | None
    ma60: float | None
    today_amount: float | None
    amount_ma20: float | None
    amount_ratio_20d: float | None
    inflow_5d: float | None
    inflow_10d: float | None
    inflow_5d_ratio: float | None
    inflow_10d_ratio: float | None
    component_count: int
    quoted_component_count: int
    up_ratio: float | None
    gt3_ratio: float | None
    gt5_ratio: float | None
    limit_up_density: float | None
    leader_candidate: LeaderCandidate | None
    warnings: list[str] = field(default_factory=list)
    missing_metrics: list[str] = field(default_factory=list)


def _collect_close_window(kline: list[KlineBar], window_days: int) -> list[float] | None:
    """Collect the latest N close values and require every value to be present."""
    if len(kline) < window_days:
        return None
    closes = [item.close for item in kline[-window_days:]]
    if any(value is None for value in closes):
        return None
    return [float(value) for value in closes if value is not None]


def calculate_return(kline: list[KlineBar], window_days: int) -> float | None:
    """Calculate N-day return with close_t / close_t-N - 1."""
    if len(kline) <= window_days:
        return None
    current = kline[-1].close
    previous = kline[-(window_days + 1)].close
    if current in (None, 0) or previous in (None, 0):
        return None
    return (current / previous - 1) * 100


def calculate_moving_average(kline: list[KlineBar], window_days: int) -> float | None:
    """Calculate simple moving average of the latest close prices."""
    closes = _collect_close_window(kline, window_days)
    if closes is None:
        return None
    return sum(closes) / window_days


def calculate_amount_average(kline: list[KlineBar], window_days: int) -> float | None:
    """Calculate average turnover amount for the latest N bars."""
    if len(kline) < window_days:
        return None
    amounts = [item.amount for item in kline[-window_days:]]
    if any(value is None for value in amounts):
        return None
    valid_amounts = [float(value) for value in amounts if value is not None]
    return sum(valid_amounts) / window_days


def calculate_amount_sum(kline: list[KlineBar], window_days: int) -> float | None:
    """Calculate total turnover amount for the latest N bars."""
    if len(kline) < window_days:
        return None
    amounts = [item.amount for item in kline[-window_days:]]
    if any(value is None for value in amounts):
        return None
    valid_amounts = [float(value) for value in amounts if value is not None]
    return sum(valid_amounts)


def calculate_ratio(numerator: float | None, denominator: float | None) -> float | None:
    """Calculate percentage ratio and return None when denominator is missing or zero."""
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator * 100


def calculate_up_ratio(components: list[BoardComponent]) -> tuple[float | None, int]:
    """Calculate the share of components with positive change."""
    valid = [item for item in components if item.pct_change is not None]
    if not valid:
        return None, 0
    up_count = sum(1 for item in valid if item.pct_change > 0)
    return up_count / len(valid), len(valid)


def calculate_threshold_ratio(components: list[BoardComponent], threshold: float) -> float | None:
    """Calculate the share of components with pct_change above a threshold."""
    valid = [item for item in components if item.pct_change is not None]
    if not valid:
        return None
    hit_count = sum(1 for item in valid if item.pct_change > threshold)
    return hit_count / len(valid)


def calculate_limit_up_density(components: list[BoardComponent]) -> float | None:
    """Calculate limit-up count divided by quoted component count."""
    valid = [item for item in components if item.pct_change is not None]
    if not valid:
        return None
    return sum(1 for item in valid if item.is_limit_up) / len(valid)


def select_leader_candidate(components: list[BoardComponent]) -> LeaderCandidate | None:
    """Select leader candidate from top-5 by amount, then choose max pct_change."""
    eligible = [item for item in components if item.amount is not None]
    if len(eligible) < 5:
        return None
    top_amount = sorted(eligible, key=lambda item: item.amount or 0, reverse=True)[:5]
    with_pct = [item for item in top_amount if item.pct_change is not None]
    if not with_pct:
        return None
    leader = max(with_pct, key=lambda item: item.pct_change or 0)
    return LeaderCandidate(
        stock_code=leader.stock_code,
        stock_name=leader.stock_name,
        pct_change=leader.pct_change,
        amount=leader.amount,
        turnover_rate=leader.turnover_rate,
        is_limit_up=leader.is_limit_up,
    )


def calculate_indicators(snapshot: BoardMarketSnapshot) -> BoardIndicators:
    """Build all requested metrics from a fetched board snapshot."""
    board_name = snapshot.resolved_name or snapshot.candidate.name
    close = snapshot.board_kline_60[-1].close if snapshot.board_kline_60 else None
    board_return_5d = calculate_return(snapshot.board_kline_60, 5)
    board_return_20d = calculate_return(snapshot.board_kline_60, 20)
    hs300_return_5d = calculate_return(snapshot.benchmark_kline_60, 5)
    hs300_return_20d = calculate_return(snapshot.benchmark_kline_60, 20)
    rs_5d = None
    if board_return_5d is not None and hs300_return_5d is not None:
        rs_5d = board_return_5d - hs300_return_5d
    rs_20d = None
    if board_return_20d is not None and hs300_return_20d is not None:
        rs_20d = board_return_20d - hs300_return_20d

    ma5 = calculate_moving_average(snapshot.board_kline_60, 5)
    ma10 = calculate_moving_average(snapshot.board_kline_60, 10)
    ma20 = calculate_moving_average(snapshot.board_kline_60, 20)
    ma60 = calculate_moving_average(snapshot.board_kline_60, 60)
    today_amount = snapshot.board_kline_60[-1].amount if snapshot.board_kline_60 else None
    amount_ma20 = calculate_amount_average(snapshot.board_kline_60, 20)
    amount_ratio_20d = None
    if today_amount is not None and amount_ma20 not in (None, 0):
        amount_ratio_20d = today_amount / amount_ma20

    amount_sum_5d = calculate_amount_sum(snapshot.board_kline_60, 5)
    amount_sum_10d = calculate_amount_sum(snapshot.board_kline_60, 10)
    inflow_5d = snapshot.fund_flow_5d.main_net_inflow if snapshot.fund_flow_5d else None
    inflow_10d = snapshot.fund_flow_10d.main_net_inflow if snapshot.fund_flow_10d else None
    inflow_5d_ratio = calculate_ratio(inflow_5d, amount_sum_5d)
    inflow_10d_ratio = calculate_ratio(inflow_10d, amount_sum_10d)

    up_ratio, quoted_component_count = calculate_up_ratio(snapshot.components)
    gt3_ratio = calculate_threshold_ratio(snapshot.components, 3)
    gt5_ratio = calculate_threshold_ratio(snapshot.components, 5)
    limit_up_density = calculate_limit_up_density(snapshot.components)
    leader_candidate = select_leader_candidate(snapshot.components)

    indicators = BoardIndicators(
        board_name=board_name,
        board_code=snapshot.resolved_code,
        board_type=snapshot.board_type,
        trade_date=snapshot.trade_date,
        close=close,
        board_return_5d=board_return_5d,
        board_return_20d=board_return_20d,
        hs300_return_5d=hs300_return_5d,
        hs300_return_20d=hs300_return_20d,
        rs_5d=rs_5d,
        rs_20d=rs_20d,
        ma5=ma5,
        ma10=ma10,
        ma20=ma20,
        ma60=ma60,
        today_amount=today_amount,
        amount_ma20=amount_ma20,
        amount_ratio_20d=amount_ratio_20d,
        inflow_5d=inflow_5d,
        inflow_10d=inflow_10d,
        inflow_5d_ratio=inflow_5d_ratio,
        inflow_10d_ratio=inflow_10d_ratio,
        component_count=len(snapshot.components),
        quoted_component_count=quoted_component_count,
        up_ratio=up_ratio,
        gt3_ratio=gt3_ratio,
        gt5_ratio=gt5_ratio,
        limit_up_density=limit_up_density,
        leader_candidate=leader_candidate,
        warnings=list(snapshot.warnings),
    )

    metric_pairs = {
        "board_return_5d": board_return_5d,
        "board_return_20d": board_return_20d,
        "hs300_return_5d": hs300_return_5d,
        "hs300_return_20d": hs300_return_20d,
        "rs_5d": rs_5d,
        "rs_20d": rs_20d,
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "ma60": ma60,
        "amount_ratio_20d": amount_ratio_20d,
        "inflow_5d_ratio": inflow_5d_ratio,
        "inflow_10d_ratio": inflow_10d_ratio,
        "up_ratio": up_ratio,
        "gt3_ratio": gt3_ratio,
        "gt5_ratio": gt5_ratio,
        "limit_up_density": limit_up_density,
    }
    indicators.missing_metrics = [name for name, value in metric_pairs.items() if value is None]
    if leader_candidate is None:
        indicators.missing_metrics.append("leader_candidate")
    return indicators
