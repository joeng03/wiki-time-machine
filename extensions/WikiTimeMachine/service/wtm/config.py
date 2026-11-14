"""Environment-driven configuration (no secrets in code)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    db_host: str = field(default_factory=lambda: _env("WTM_DB_HOST", "127.0.0.1"))
    db_port: int = field(default_factory=lambda: int(_env("WTM_DB_PORT", "3306")))
    db_name: str = field(default_factory=lambda: _env("WTM_DB_NAME", "wtm"))
    reader_user: str = field(default_factory=lambda: _env("WTM_READER_USER", "wtm_reader"))
    reader_password: str = field(default_factory=lambda: _env("WTM_READER_PASSWORD"))
    importer_user: str = field(default_factory=lambda: _env("WTM_IMPORTER_USER", "wtm_importer"))
    importer_password: str = field(default_factory=lambda: _env("WTM_IMPORTER_PASSWORD"))
    user_agent: str = field(default_factory=lambda: _env("WTM_USER_AGENT"))
    embedder: str = field(default_factory=lambda: _env("WTM_EMBEDDER", "minilm"))
    snapshot_mode: str = field(default_factory=lambda: _env("WTM_SNAPSHOT_MODE", "svt"))
    llm_base_url: str = field(default_factory=lambda: _env("WTM_LLM_BASE_URL"))
    llm_model: str = field(default_factory=lambda: _env("WTM_LLM_MODEL"))
    llm_api_key: str = field(default_factory=lambda: _env("WTM_LLM_API_KEY"))
    llm_timeout: float = field(default_factory=lambda: float(_env("WTM_LLM_TIMEOUT", "45")))
    sql_dir: str = field(default_factory=lambda: _env("WTM_SQL_DIR", "/app/sql"))
    retrieval_k: int = field(default_factory=lambda: int(_env("WTM_RETRIEVAL_K", "5")))

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base_url and self.llm_model)


def settings() -> Settings:
    return Settings()
