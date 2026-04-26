from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from app.ai_summary import request_ai_text
from app.config import AIConfig
from app.fund_rank_report.holdings import ReportFundHoldings, serialize_holding_bundle
from app.models import FundSectorClassificationRecord
from app.storage import get_fund_sector_classifications, upsert_fund_sector_classifications


CLASSIFICATION_BATCH_SIZE = 20
CLASSIFICATION_OUTPUT_DIR = Path("eastmoney") / "fund_rank_ai_classification"
_JSON_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def classification_snapshot_path(raw_dir: Path, fund_code: str, report_date: str) -> Path:
    safe_date = report_date.replace("/", "-")
    return raw_dir / CLASSIFICATION_OUTPUT_DIR / f"{fund_code}__{safe_date}.json"


def _chunked(items: list[ReportFundHoldings], batch_size: int) -> list[list[ReportFundHoldings]]:
    return [items[index : index + batch_size] for index in range(0, len(items), batch_size)]


def _classification_key(bundle: ReportFundHoldings) -> tuple[str, str]:
    return bundle.fund_code, bundle.report_date


def _extract_json_text(text: str) -> str:
    fenced = _JSON_BLOCK_PATTERN.search(text)
    if fenced:
        return fenced.group(1).strip()

    stripped = text.strip()
    dict_start = stripped.find("{")
    dict_end = stripped.rfind("}")
    list_start = stripped.find("[")
    list_end = stripped.rfind("]")
    if dict_start != -1 and dict_end != -1 and dict_start < dict_end:
        return stripped[dict_start : dict_end + 1]
    if list_start != -1 and list_end != -1 and list_start < list_end:
        return stripped[list_start : list_end + 1]
    return stripped


def _normalize_confidence(value) -> float | None:
    if value in {None, "", "-", "--"}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_prompt(batch: list[ReportFundHoldings]) -> str:
    funds = [
        {
            "fund_code": bundle.fund_code,
            "fund_name": bundle.fund_name,
            "report_date": bundle.report_date,
            "holdings": [
                {
                    "stock_code": record.stock_code,
                    "stock_name": record.stock_name,
                    "net_value_ratio": record.net_value_ratio,
                    "rank_no": record.rank_no,
                }
                for record in bundle.holdings[:10]
            ],
        }
        for bundle in batch
    ]
    return (
        "你是A股基金持仓板块归类助手。"
        "根据基金名称、基金代码、持仓截止时间、前十大持仓股票，判断每只基金最相关的一个大板块和一个细分板块。"
        "不要输出 markdown，不要解释规则外内容，只返回 JSON。"
        "JSON 格式必须是："
        "{\"results\":[{\"fund_code\":\"\",\"report_date\":\"\",\"primary_sector\":\"\",\"sub_sector\":\"\",\"reason\":\"\",\"confidence\":0.0}]}"
        "其中 confidence 取 0 到 1。"
        f"输入数据：{json.dumps({'funds': funds}, ensure_ascii=False, separators=(',', ':'))}"
    )


def _parse_response_records(
    text: str,
    bundles_by_key: dict[tuple[str, str], ReportFundHoldings],
) -> tuple[list[FundSectorClassificationRecord], list[str]]:
    payload = json.loads(_extract_json_text(text))
    items = payload.get("results", []) if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise ValueError("AI 分类结果不是列表。")

    records: list[FundSectorClassificationRecord] = []
    warnings: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        fund_code = str(item.get("fund_code", "")).strip()
        report_date = str(item.get("report_date", "")).strip()
        key = (fund_code, report_date)
        bundle = bundles_by_key.get(key)
        if not bundle:
            warnings.append(f"AI 返回了未知基金 {fund_code}@{report_date}")
            continue

        primary_sector = str(item.get("primary_sector", "")).strip()
        sub_sector = str(item.get("sub_sector", "")).strip()
        if not primary_sector or not sub_sector:
            warnings.append(f"{bundle.fund_name}({bundle.fund_code}) AI 分类缺少板块字段")
            continue

        records.append(
            FundSectorClassificationRecord(
                fund_code=bundle.fund_code,
                fund_name=bundle.fund_name,
                report_date=bundle.report_date,
                primary_sector=primary_sector,
                sub_sector=sub_sector,
                reason=str(item.get("reason", "")).strip() or None,
                confidence=_normalize_confidence(item.get("confidence")),
                raw_payload=json.dumps(item, ensure_ascii=False),
            )
        )
    return records, warnings


def _write_debug_snapshot(
    raw_dir: Path,
    bundle: ReportFundHoldings,
    record: FundSectorClassificationRecord,
    *,
    cache_hit: bool,
) -> None:
    path = classification_snapshot_path(raw_dir, bundle.fund_code, bundle.report_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "cache_hit": cache_hit,
                "fund": serialize_holding_bundle(bundle),
                "classification": {
                    "fund_code": record.fund_code,
                    "fund_name": record.fund_name,
                    "report_date": record.report_date,
                    "primary_sector": record.primary_sector,
                    "sub_sector": record.sub_sector,
                    "reason": record.reason,
                    "confidence": record.confidence,
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _row_to_record(row: sqlite3.Row) -> FundSectorClassificationRecord:
    return FundSectorClassificationRecord(
        fund_code=row["fund_code"],
        fund_name=row["fund_name"],
        report_date=row["report_date"],
        primary_sector=row["primary_sector"],
        sub_sector=row["sub_sector"],
        reason=row["reason"],
        confidence=row["confidence"],
        raw_payload=row["raw_payload"],
        created_at=row["created_at"],
    )


def resolve_fund_sector_classifications(
    conn: sqlite3.Connection,
    ai_config: AIConfig,
    raw_dir: Path,
    bundles: list[ReportFundHoldings],
    *,
    batch_size: int = CLASSIFICATION_BATCH_SIZE,
) -> tuple[dict[tuple[str, str], FundSectorClassificationRecord], list[str]]:
    if not bundles:
        return {}, []

    bundle_map = {_classification_key(bundle): bundle for bundle in bundles}
    cached_rows = get_fund_sector_classifications(conn, list(bundle_map))
    resolved = {
        key: _row_to_record(row)
        for key, row in cached_rows.items()
    }
    missing = [bundle for key, bundle in bundle_map.items() if key not in resolved]
    warnings: list[str] = []

    if missing:
        for batch in _chunked(missing, batch_size):
            prompt = _build_prompt(batch)
            text, warning = request_ai_text(
                ai_config,
                prompt,
                request_label="fund-sector-classification",
            )
            if warning:
                warnings.append(
                    f"AI 板块识别失败 {batch[0].fund_code}~{batch[-1].fund_code}: {warning}"
                )
                continue
            if not text:
                warnings.append(
                    f"AI 板块识别未返回文本 {batch[0].fund_code}~{batch[-1].fund_code}"
                )
                continue
            try:
                records, parse_warnings = _parse_response_records(
                    text,
                    {_classification_key(bundle): bundle for bundle in batch},
                )
            except (json.JSONDecodeError, ValueError) as exc:
                warnings.append(
                    f"AI 板块识别解析失败 {batch[0].fund_code}~{batch[-1].fund_code}: {exc}"
                )
                continue
            warnings.extend(parse_warnings)
            if records:
                upsert_fund_sector_classifications(conn, records)
                for record in records:
                    resolved[(record.fund_code, record.report_date)] = record

            missing_keys = {
                _classification_key(bundle)
                for bundle in batch
            } - {
                (record.fund_code, record.report_date)
                for record in records
            }
            if missing_keys:
                labels = [
                    f"{bundle_map[key].fund_name}({bundle_map[key].fund_code})"
                    for key in sorted(missing_keys)
                ]
                warnings.append(
                    "AI 板块识别缺结果共 "
                    f"{len(labels)} 只：{'、'.join(labels[:10])}"
                    + (" 等" if len(labels) > 10 else "")
                )

    for key, bundle in bundle_map.items():
        record = resolved.get(key)
        if not record:
            continue
        _write_debug_snapshot(raw_dir, bundle, record, cache_hit=key in cached_rows)

    return resolved, warnings


def serialize_classification_record(record: FundSectorClassificationRecord) -> dict:
    return {
        "fund_code": record.fund_code,
        "fund_name": record.fund_name,
        "report_date": record.report_date,
        "primary_sector": record.primary_sector,
        "sub_sector": record.sub_sector,
        "reason": record.reason,
        "confidence": record.confidence,
    }
