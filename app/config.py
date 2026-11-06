from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = PROJECT_ROOT / "data"


class Settings(BaseSettings):
    """Настройки приложения. Все переменные можно задать через OPS_*."""

    model_config = SettingsConfigDict(
        env_prefix="OPS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "local"
    incident_id: str = "INC-2026-11-06-01"
    incident_dir: Path = DATA_ROOT / "incidents" / "INC-2026-11-06-01"
    enterprise_dir: Path = DATA_ROOT
    runs_dir: Path = PROJECT_ROOT / "runs"
    checkpoint_db: Path = PROJECT_ROOT / "runs" / "checkpoints.sqlite"

    llm_provider: Literal["anthropic", "openai"] = "anthropic"
    llm_model: str | None = None
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_temperature: float = 0.0
    llm_max_tokens: int = 8192

    max_iterations: int = 10
    max_tool_rounds: int = 12
    run_timeout_seconds: int = 240
    app_port: int = 8095

    langfuse_public_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("LANGFUSE_PUBLIC_KEY", "OPS_LANGFUSE_PUBLIC_KEY"),
    )
    langfuse_secret_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("LANGFUSE_SECRET_KEY", "OPS_LANGFUSE_SECRET_KEY"),
    )
    langfuse_host: str | None = Field(
        default=None,
        validation_alias=AliasChoices("LANGFUSE_HOST", "OPS_LANGFUSE_HOST"),
    )
    routerai_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("KEY", "ROUTERAI_API_KEY", "OPS_ROUTERAI_KEY"),
    )

    mcp_command: str | None = None

    @property
    def resolved_model(self) -> str:
        if self.llm_model:
            return self.llm_model
        if self.llm_provider == "anthropic":
            return os.getenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", "claude-haiku-4-5-20251001")
        if self.routerai_key or os.getenv("KEY") or os.getenv("ROUTERAI_API_KEY"):
            return os.getenv("OPENAI_MODEL", "anthropic/claude-haiku-4.5")
        return os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    @property
    def resolved_api_key(self) -> str | None:
        if self.llm_api_key:
            return self.llm_api_key
        if self.llm_provider == "anthropic":
            return os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")
        return (
            os.getenv("OPENAI_API_KEY")
            or self.routerai_key
            or os.getenv("ROUTERAI_API_KEY")
            or os.getenv("KEY")
        )

    @property
    def resolved_base_url(self) -> str | None:
        if self.llm_base_url:
            return self.llm_base_url
        if self.llm_provider == "anthropic":
            return os.getenv("ANTHROPIC_BASE_URL")
        if os.getenv("OPENAI_BASE_URL"):
            return os.getenv("OPENAI_BASE_URL")
        if os.getenv("ROUTERAI_BASE_URL"):
            return os.getenv("ROUTERAI_BASE_URL")
        if self.routerai_key or os.getenv("KEY") or os.getenv("ROUTERAI_API_KEY"):
            return "https://routerai.ru/api/v1"
        return None

    @property
    def langfuse_enabled(self) -> bool:
        return bool(
            (self.langfuse_public_key or os.getenv("LANGFUSE_PUBLIC_KEY"))
            and (self.langfuse_secret_key or os.getenv("LANGFUSE_SECRET_KEY"))
        )

    @property
    def langfuse_public_key_resolved(self) -> str | None:
        return self.langfuse_public_key or os.getenv("LANGFUSE_PUBLIC_KEY")

    @property
    def langfuse_secret_key_resolved(self) -> str | None:
        return self.langfuse_secret_key or os.getenv("LANGFUSE_SECRET_KEY")

    @property
    def langfuse_host_resolved(self) -> str:
        return self.langfuse_host or os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

    @property
    def mcp_server_command(self) -> str:
        if self.mcp_command:
            return self.mcp_command
        return str(Path(sys.executable))

    def ensure_dirs(self) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_db.parent.mkdir(parents=True, exist_ok=True)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
