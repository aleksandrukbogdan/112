"""Конфигурация из переменных окружения."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _b(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL", "postgresql://t112:t112@postgres:5432/trainer112"
    )
    asr_url: str = os.getenv("ASR_URL", "http://asr:8081")
    tts_url: str = os.getenv("TTS_URL", "http://tts:8082")

    llm_base_url: str = os.getenv("LLM_BASE_URL", "http://host.docker.internal:1212/v1")
    llm_model: str = os.getenv("LLM_MODEL", "Qwen3-VL")
    llm_enabled: bool = _b("LLM_ENABLED", "true")
    llm_timeout_sec: float = float(os.getenv("LLM_TIMEOUT_SEC", "20"))

    tts_enabled: bool = _b("TTS_ENABLED", "true")
    asr_enabled: bool = _b("ASR_ENABLED", "true")

    scenarios_dir: str = os.getenv("SCENARIOS_DIR", "/content/scenarios")
    cors_origins: str = os.getenv("CORS_ORIGINS", "*")


settings = Settings()
