from __future__ import annotations

import json

from app.ai_summary import request_ai_text
from app.config import AIConfig


def _serialize_focus_sector_cards(bridge_payload: dict) -> list[dict]:
    serialized: list[dict] = []
    for card in bridge_payload.get("focus_sector_cards", [])[:8]:
        serialized.append(
            {
                "板块": card.get("sector_name"),
                "评分": card.get("score"),
                "等级": card.get("grade_code"),
                "建议": card.get("suggestion"),
                "RS20": card.get("rs_20d"),
                "10日净流入占比": card.get("inflow_10d_ratio"),
                "量比20日": card.get("amount_ratio_20d"),
                "龙头": (card.get("leader_candidate") or {}).get("stock_name"),
                "关联基金": [
                    {
                        "基金": item.get("fund_name"),
                        "类型": item.get("fund_type"),
                        "原因": item.get("reasons"),
                        "重叠持仓": item.get("overlap_stocks"),
                    }
                    for item in card.get("related_funds", [])[:4]
                ],
                "风险": card.get("risk_flags", [])[:4],
            }
        )
    return serialized


def _serialize_fund_links(bridge_payload: dict) -> list[dict]:
    serialized: list[dict] = []
    for item in bridge_payload.get("fund_to_sector_links", [])[:10]:
        serialized.append(
            {
                "基金": item.get("fund_name"),
                "基金代码": item.get("fund_code"),
                "基金类型": item.get("fund_type"),
                "相关板块": item.get("sectors", [])[:4],
            }
        )
    return serialized


def build_sector_bridge_ai_prompt_payload(report_payload: dict, bridge_payload: dict) -> dict:
    return {
        "交易日": report_payload.get("trade_date"),
        "基金日报主线": {
            "高重复热点": report_payload.get("repeated_focus", [])[:8],
            "跨周期持续热点": report_payload.get("persistent_focus", [])[:8],
            "重点板块": report_payload.get("focus_names", [])[:12],
        },
        "板块强度解释层": _serialize_focus_sector_cards(bridge_payload),
        "基金到板块映射": _serialize_fund_links(bridge_payload),
        "桥接告警": bridge_payload.get("warnings", [])[:6],
    }


def build_sector_bridge_ai_prompt(report_payload: dict, bridge_payload: dict) -> str:
    prompt_payload = build_sector_bridge_ai_prompt_payload(report_payload, bridge_payload)
    return (
        "你是一名A股基金与板块联动分析师。你的任务是基于基金日报和板块强度结果，"
        "解释今天重点基金背后的强势主线、受益方向和风险点。"
        "要求：1. 先归纳2-3条市场主线；2. 明确哪些基金受哪些板块驱动；"
        "3. 识别板块强但基金映射弱、或基金强但板块证据不足的情况；"
        "4. 给出观察建议和风险提示；5. 只能总结输入事实，不允许编造指标。"
        "输出必须层次清晰，严格使用以下四个二级标题："
        "## 主线判断、## 板块解读、## 基金解读、## 风险与观察。"
        "每个标题下使用3-5条短 bullet；每条 bullet 只表达一个判断；"
        "先写结论，再写依据；优先点名具体板块和基金，不要泛泛而谈；"
        "避免大段长文，不要重复输入里的原始字段名。"
        f"数据：{json.dumps(prompt_payload, ensure_ascii=False, separators=(',', ':'))}"
    )


def request_sector_bridge_ai_summary(
    ai_config: AIConfig,
    report_payload: dict,
    bridge_payload: dict,
) -> tuple[str | None, str | None, str]:
    prompt = build_sector_bridge_ai_prompt(report_payload, bridge_payload)
    summary, warning = request_ai_text(
        ai_config,
        prompt,
        request_label="sector-bridge-ai-summary",
    )
    return summary, warning, prompt
