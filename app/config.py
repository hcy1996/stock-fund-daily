from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.json"
EXAMPLE_CONFIG_PATH = PROJECT_ROOT / "config.example.json"


@dataclass(slots=True)
class SMTPConfig:
    host: str
    port: int
    username: str
    password: str
    sender: str
    use_ssl: bool
    starttls: bool


@dataclass(slots=True)
class ScheduleConfig:
    hour: int
    minute: int
    timezone: str


@dataclass(slots=True)
class StorageConfig:
    db_path: Path
    raw_dir: Path
    output_dir: Path


@dataclass(slots=True)
class ReportConfig:
    top_n: int
    funds_per_sector: int


@dataclass(slots=True)
class SourcesConfig:
    enable_tonghuashun_20d: bool
    user_agent: str


@dataclass(slots=True)
class MetaConfig:
    report_name: str


@dataclass(slots=True)
class AppConfig:
    smtp: SMTPConfig
    recipients: list[str]
    schedule: ScheduleConfig
    storage: StorageConfig
    report: ReportConfig
    sources: SourcesConfig
    meta: MetaConfig
    config_path: Path
    using_example_config: bool


def _resolve_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (PROJECT_ROOT / path).resolve()


def load_config(config_path: str | None = None) -> AppConfig:
    path = Path(config_path).resolve() if config_path else DEFAULT_CONFIG_PATH
    using_example = False
    if not path.exists():
        path = EXAMPLE_CONFIG_PATH
        using_example = True

    payload = json.loads(path.read_text(encoding="utf-8"))

    smtp = payload["smtp"]
    schedule = payload["schedule"]
    storage = payload["storage"]
    report = payload["report"]
    sources = payload["sources"]
    meta = payload.get("meta", {"report_name": "股票基金日报"})

    app_config = AppConfig(
        smtp=SMTPConfig(
            host=smtp["host"],
            port=int(smtp["port"]),
            username=smtp["username"],
            password=smtp["password"],
            sender=smtp["sender"],
            use_ssl=bool(smtp.get("use_ssl", True)),
            starttls=bool(smtp.get("starttls", False)),
        ),
        recipients=list(payload["recipients"]),
        schedule=ScheduleConfig(
            hour=int(schedule["hour"]),
            minute=int(schedule["minute"]),
            timezone=schedule["timezone"],
        ),
        storage=StorageConfig(
            db_path=_resolve_path(storage["db_path"]),
            raw_dir=_resolve_path(storage["raw_dir"]),
            output_dir=_resolve_path(storage["output_dir"]),
        ),
        report=ReportConfig(
            top_n=int(report["top_n"]),
            funds_per_sector=int(report["funds_per_sector"]),
        ),
        sources=SourcesConfig(
            enable_tonghuashun_20d=bool(sources.get("enable_tonghuashun_20d", True)),
            user_agent=sources["user_agent"],
        ),
        meta=MetaConfig(report_name=meta["report_name"]),
        config_path=path,
        using_example_config=using_example,
    )
    app_config.storage.db_path.parent.mkdir(parents=True, exist_ok=True)
    app_config.storage.raw_dir.mkdir(parents=True, exist_ok=True)
    app_config.storage.output_dir.mkdir(parents=True, exist_ok=True)
    return app_config
