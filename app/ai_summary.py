from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from urllib import error, request
from zoneinfo import ZoneInfo

from app.config import AIConfig, PROJECT_ROOT


SOURCE_LABELS = {
    "eastmoney": "东方财富",
    "tonghuashun": "同花顺",
}
WINDOW_LABELS = {
    1: "1日",
    3: "3日",
    5: "5日",
    10: "10日",
    20: "20日",
}
FUND_PERIOD_LABELS = {
    "day": "当日",
    "week": "近一周",
    "month": "近一月",
}
AI_CALL_LOG_ROOT = PROJECT_ROOT / "output" / "ai-calls"


def _serialize_components(payload: dict) -> dict[str, list[str]]:
    serialized: dict[str, list[str]] = {}
    for sector_name, components in payload.get("top_components", {}).items():
        names = [component.stock_name for component in components[:4] if component.stock_name]
        if names:
            serialized[sector_name] = names
    return serialized


def _serialize_board_rankings(payload: dict) -> dict[str, dict[str, list[str]]]:
    serialized: dict[str, dict[str, list[str]]] = {}
    for source_key, windows in payload.get("source_windows", {}).items():
        source_label = SOURCE_LABELS.get(source_key, source_key)
        serialized[source_label] = {}
        for window_days, section in windows.items():
            serialized[source_label][WINDOW_LABELS.get(window_days, str(window_days))] = [
                record.sector_name for record in section.records[:10]
            ]
    return serialized


def _serialize_board_signals(payload: dict) -> dict:
    return {
        "高重复热点": payload.get("repeated_focus", [])[:8],
        "跨周期持续热点": payload.get("persistent_focus", [])[:8],
        "当日双平台接近": payload.get("consensus_hot", [])[:8],
        "当日平台分歧": payload.get("divergence_hot", [])[:8],
        "数据降级提示": payload.get("warnings", [])[:4],
    }


def _fund_rank_return(record, period: str) -> float | None:
    if period == "day":
        return record.daily_growth_pct
    if period == "week":
        return record.weekly_growth_pct
    return record.monthly_growth_pct


def _format_return(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}%"


def _serialize_fund_rankings(payload: dict) -> dict[str, list[str]]:
    fund_holdings = payload.get("fund_holdings", {})
    serialized: dict[str, list[str]] = {}
    for period, section in payload.get("fund_rank_sections", {}).items():
        period_label = FUND_PERIOD_LABELS.get(period, period)
        serialized[period_label] = []
        for record in section.records[:6]:
            holdings = fund_holdings.get(record.fund_code, [])
            holding_names = "、".join(holding.stock_name for holding in holdings[:5]) or "暂无持仓"
            report_date = holdings[0].report_date if holdings else "无"
            serialized[period_label].append(
                f"{record.rank_no}. {record.fund_name}({_format_return(_fund_rank_return(record, period))})"
                f"；持仓日={report_date}；前五持仓={holding_names}"
            )
    return serialized


def _serialize_holding_stock_heat(payload: dict) -> list[str]:
    stock_map: dict[str, dict] = {}
    for holdings in payload.get("fund_holdings", {}).values():
        for holding in holdings[:5]:
            bucket = stock_map.setdefault(
                holding.stock_code,
                {
                    "stock_code": holding.stock_code,
                    "stock_name": holding.stock_name,
                    "fund_count": 0,
                    "ratio_sum": 0.0,
                },
            )
            bucket["fund_count"] += 1
            bucket["ratio_sum"] += holding.net_value_ratio or 0.0
    ranked = sorted(
        stock_map.values(),
        key=lambda item: (-item["fund_count"], -item["ratio_sum"], item["stock_name"]),
    )[:12]
    return [
        f"{item['stock_name']}：{item['fund_count']}只基金重仓，合计占比{item['ratio_sum']:.1f}%"
        for item in ranked
    ]


def _serialize_related_etfs(payload: dict) -> dict[str, list[str]]:
    focus_names = list(
        dict.fromkeys(
            payload.get("repeated_focus", [])[:6]
            + payload.get("persistent_focus", [])[:6]
            + payload.get("consensus_hot", [])[:6]
            + payload.get("divergence_hot", [])[:4]
        )
    )
    related_funds = payload.get("related_funds", {})
    serialized: dict[str, list[str]] = {}
    for sector_name in focus_names:
        funds = related_funds.get(sector_name, [])
        etfs = [
            f"{fund.fund_name}({fund.fund_code})"
            for fund in funds
            if "ETF" in fund.fund_type.upper() or "ETF" in fund.fund_name.upper()
        ][:3]
        if etfs:
            serialized[sector_name] = etfs
    return serialized


def _extract_text(payload: dict) -> str:
    output_text = str(payload.get("output_text", "")).strip()
    if output_text:
        return output_text

    choices = payload.get("choices", [])
    if choices:
        message = choices[0].get("message", {})
        content = message.get("content", "")
        if isinstance(content, str) and content.strip():
            return content.strip()

    parts: list[str] = []
    for item in payload.get("output", []):
        for content in item.get("content", []):
            text = str(content.get("text", "")).strip()
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def build_ai_prompt_payload(payload: dict) -> dict:
    return {
        "交易日": payload["trade_date"],
        "板块多周期榜单": _serialize_board_rankings(payload),
        "板块信号摘要": _serialize_board_signals(payload),
        "基金涨幅榜与前五持仓": _serialize_fund_rankings(payload),
        "基金重仓股聚集": _serialize_holding_stock_heat(payload),
        "可跟踪ETF候选": _serialize_related_etfs(payload),
        "热点板块成分股样本": _serialize_components(payload),
    }


def build_ai_prompt(payload: dict) -> str:
    prompt_payload = build_ai_prompt_payload(payload)
    return (
        "你是一名A股投资策略分析师。基于板块排行榜（同花顺+东方财富，多周期）和基金数据，"
        "输出可执行市场判断与投资建议。目标：识别主线板块、轮动路径、资金风格、风险信号，并给具体操作建议。"
        "要求：1.板块分析为核心，找主线板块2-3个、次主线1-2个；依据短期1-3日、中期5-10日、20日持续性、两平台一致性；"
        "识别持续走强、短期冲高、分歧板块。2.判断阶段：启动/主升/轮动/退潮；说明轮动方向，预测下一步轮动。"
        "3.列风险：仅短期上榜、快速拉升不持续、两平台不一致。4.基金与资金：判断成长/价值/主题/防御风格，"
        "说明重仓股集中板块、是否抱团、是否与主线一致。5.投资建议必须具体：⭐主线板块2-3个+逻辑；🔄轮动机会1-2个；"
        "❌回避方向；基金配置写适合指数/行业/主动及加仓/持有/观望；操作写追涨/低吸/等待和轻/中/重仓位。"
        "输出结构：1核心结论(3条内)；2主线板块；3轮动与阶段；4风险提示；5投资建议。"
        "原则：不复述涨跌，必须做判断；重点看持续性+一致性；方向明确，不要模糊结论。"
        f"数据：{json.dumps(prompt_payload, ensure_ascii=False, separators=(',', ':'))}"
    )


def _truncate_error_text(value: str, limit: int = 180) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _sanitize_request_label(value: str) -> str:
    compact = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value.strip())
    compact = compact.strip("-_")
    return compact or "generic"


def _write_ai_call_log(
    request_label: str,
    prompt: str,
    attempts: list[dict],
    *,
    final_text: str | None,
    final_warning: str | None,
) -> Path:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    day_dir = AI_CALL_LOG_ROOT / now.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    safe_label = _sanitize_request_label(request_label)
    file_path = day_dir / f"{now.strftime('%H%M%S-%f')}-{safe_label}.json"
    file_path.write_text(
        json.dumps(
            {
                "created_at": now.isoformat(),
                "request_label": request_label,
                "prompt": prompt,
                "attempts": attempts,
                "final_text": final_text,
                "final_warning": final_warning,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return file_path


def request_ai_text(
    ai_config: AIConfig,
    prompt: str,
    *,
    request_label: str = "generic",
) -> tuple[str | None, str | None]:
    if not ai_config.enabled:
        return None, None
    if not ai_config.base_url or not ai_config.api_key or not ai_config.model:
        return None, "AI 已启用，但缺少 `base_url` / `api_key` / `model` 配置。"

    body = json.dumps(
        {
            "model": ai_config.model,
            "input": prompt,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {ai_config.api_key}",
    }
    endpoints = [
        (
            "responses",
            ai_config.base_url.rstrip("/") + "/responses",
            body,
        ),
        (
            "chat.completions",
            ai_config.base_url.rstrip("/") + "/chat/completions",
            json.dumps(
                {
                    "model": ai_config.model,
                    "messages": [{"role": "user", "content": prompt}],
                },
                ensure_ascii=False,
            ).encode("utf-8"),
        ),
    ]
    errors: list[str] = []
    attempts: list[dict] = []

    for endpoint_name, endpoint, request_body in endpoints:
        attempt = {
            "endpoint_name": endpoint_name,
            "endpoint": endpoint,
            "response_id": None,
            "success": False,
            "warning": None,
            "response_payload": None,
            "response_text": None,
            "error": None,
        }
        req = request.Request(
            endpoint,
            data=request_body,
            headers=headers,
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=60) as resp:
                response_text = resp.read().decode("utf-8")
                attempt["response_text"] = response_text
                response_payload = json.loads(response_text)
        except error.HTTPError as exc:
            response_body = exc.read().decode("utf-8", errors="ignore")
            attempt["error"] = f"HTTP {exc.code}"
            attempt["response_text"] = response_body
            errors.append(
                f"{endpoint_name}: HTTP {exc.code} {_truncate_error_text(response_body or str(exc))}"
            )
            attempts.append(attempt)
            continue
        except error.URLError as exc:
            attempt["error"] = str(exc.reason)
            errors.append(f"{endpoint_name}: {exc.reason}")
            attempts.append(attempt)
            continue
        except TimeoutError as exc:
            attempt["error"] = str(exc)
            errors.append(f"{endpoint_name}: {exc}")
            attempts.append(attempt)
            continue
        except json.JSONDecodeError as exc:
            attempt["error"] = f"JSONDecodeError {exc}"
            errors.append(f"{endpoint_name}: JSONDecodeError {exc}")
            attempts.append(attempt)
            continue

        attempt["response_payload"] = response_payload
        attempt["response_id"] = response_payload.get("id") if isinstance(response_payload, dict) else None
        text = _extract_text(response_payload)
        if text:
            attempt["success"] = True
            attempts.append(attempt)
            _write_ai_call_log(
                request_label,
                prompt,
                attempts,
                final_text=text,
                final_warning=None,
            )
            return text, None
        attempt["warning"] = "返回成功，但没有提取到文本内容。"
        attempts.append(attempt)
        errors.append(f"{endpoint_name}: 返回成功，但没有提取到文本内容。")
    if errors:
        warning = "AI 请求失败：" + "；".join(errors[:2])
        _write_ai_call_log(
            request_label,
            prompt,
            attempts,
            final_text=None,
            final_warning=warning,
        )
        return None, warning
    warning = "AI 请求失败：未返回可用结果。"
    _write_ai_call_log(
        request_label,
        prompt,
        attempts,
        final_text=None,
        final_warning=warning,
    )
    return None, warning


def build_ai_summary_result(ai_config: AIConfig, payload: dict) -> tuple[str | None, str | None]:
    return request_ai_text(ai_config, build_ai_prompt(payload), request_label="daily-ai-summary")


def _trim_weekly_rankings(rankings: dict[str, dict[str, list[str]]]) -> dict[str, dict[str, list[str]]]:
    return {
        source_label: {
            window_label: names[:5]
            for window_label, names in windows.items()
        }
        for source_label, windows in rankings.items()
    }


def _compact_history_snapshot(snapshot: dict) -> dict:
    daily_ai_input = snapshot.get("daily_ai_input", {})
    return {
        "交易日": snapshot.get("trade_date", ""),
        "板块多周期榜单": _trim_weekly_rankings(daily_ai_input.get("板块多周期榜单", {})),
        "板块信号摘要": daily_ai_input.get("板块信号摘要", {}),
        "基金重仓股聚集": daily_ai_input.get("基金重仓股聚集", [])[:8],
        "可跟踪ETF候选": daily_ai_input.get("可跟踪ETF候选", {}),
        "AI日分析": snapshot.get("daily_ai_summary") or "",
        "AI日分析告警": snapshot.get("daily_ai_warning") or "",
    }


def build_weekly_ai_prompt(history_snapshots: list[dict]) -> str:
    compact_history = [_compact_history_snapshot(snapshot) for snapshot in history_snapshots]
    return (
        "你是一名A股复盘与节奏分析师。下面给你最近一周内若干个交易日的历史快照，"
        "每个快照都包含当日多周期板块榜单、板块信号摘要，以及当日 AI 分析结果。"
        "请基于这些连续样本做跨天综合分析，不要逐日流水账。"
        "目标：识别近一周主线如何演化、哪些方向在强化或钝化、资金风格是否切换、"
        "当前最值得跟踪的主线与轮动机会，以及需要回避的风险方向。"
        "要求：1.先判断样本是否足够，若不足 5 个交易日，要先说明局限；"
        "2.主线板块给 2-3 个，次主线给 1-2 个，必须说明是延续、扩散、分歧修复还是退潮；"
        "3.说明轮动路径：哪些方向在接力，哪些方向只是脉冲；"
        "4.结合基金重仓股聚集和 ETF 候选，判断更偏成长、主题、价值还是防御；"
        "5.风险提示必须点名：高位反复、平台分歧、持续性不足或短期拥挤；"
        "6.操作建议要具体到继续跟踪、低吸等待、只观察、不追涨等。"
        "输出结构：1近一周主线演化；2当前最强主线与次主线；3轮动路径与资金风格；4风险提示；5后续观察与操作建议。"
        "原则：只讲跨天结论，不重复原始涨跌描述；持续性优先于单日强弱；结论必须明确。"
        f"数据：{json.dumps(compact_history, ensure_ascii=False, separators=(',', ':'))}"
    )


def build_weekly_ai_summary_result(
    ai_config: AIConfig,
    history_snapshots: list[dict],
) -> tuple[str | None, str | None]:
    if not history_snapshots:
        return None, "近一周综合分析未生成：缺少历史快照。"
    return request_ai_text(
        ai_config,
        build_weekly_ai_prompt(history_snapshots),
        request_label="weekly-ai-summary",
    )


def build_ai_summary(ai_config: AIConfig, payload: dict) -> str | None:
    summary, _ = build_ai_summary_result(ai_config, payload)
    return summary
