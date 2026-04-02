from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


CONFIG_PATH = Path.home() / ".eternal_return_log_analyzer.json"


def build_default_log_path() -> str:
    return str(
        Path.home()
        / "AppData"
        / "LocalLow"
        / "NimbleNeuron"
        / "Eternal Return"
        / "Player.log"
    )


def default_analysis_mode() -> str:
    return "local"


def _normalize_analysis_mode(raw: dict[str, object]) -> str:
    mode = raw.get("analysis_mode")
    if isinstance(mode, str) and mode in {"local", "direct"}:
        return mode

    if raw.get("use_server_ai") is True:
        return "direct"

    distribution_mode = raw.get("distribution_mode")
    if isinstance(distribution_mode, str) and distribution_mode == "local-only":
        return "local"

    use_ai = raw.get("use_ai")
    if use_ai is False:
        return "local"

    legacy_server_url = raw.get("server_url")
    if isinstance(legacy_server_url, str) and legacy_server_url:
        return "direct"

    return default_analysis_mode()


@dataclass
class AppConfig:
    log_path: str = build_default_log_path()
    analysis_mode: str = default_analysis_mode()
    mask_sensitive_data: bool = True
    recent_line_count: int = 3000
    ai_model: str = "gpt-5-mini"


def load_config() -> AppConfig:
    if not CONFIG_PATH.exists():
        return AppConfig()

    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return AppConfig()

    if not isinstance(raw, dict):
        return AppConfig()

    config = AppConfig()
    for field_name in asdict(config):
        if field_name in raw:
            setattr(config, field_name, raw[field_name])

    if not config.log_path:
        config.log_path = build_default_log_path()
    config.analysis_mode = _normalize_analysis_mode(raw)
    if config.analysis_mode not in {"local", "direct"}:
        config.analysis_mode = default_analysis_mode()

    return config


def save_config(config: AppConfig) -> None:
    CONFIG_PATH.write_text(
        json.dumps(asdict(config), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
