"""Environment-backed service settings without business decisions."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"missing required environment variable: {name}")
    return value


def _positive_int(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 1:
        raise ValueError(f"{name} must be at least 1")
    return value


class ServiceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    app_env: str
    host: str
    port: int = Field(ge=1, le=65535)
    log_level: str
    openai_api_key: str

    @classmethod
    def common_values(cls) -> dict[str, object]:
        return {
            "app_env": os.getenv("APP_ENV", "development"),
            "host": os.getenv("HOST", "0.0.0.0"),
            "port": _positive_int("PORT", 8000),
            "log_level": os.getenv("LOG_LEVEL", "INFO"),
            "openai_api_key": _required("OPENAI_API_KEY"),
        }


class KnowledgeSettings(ServiceSettings):
    public_base_url: HttpUrl
    data_dir: Path
    embedding_model: str
    agent_model: str
    mcp_bearer_token: str
    browser_executor_base_url: HttpUrl
    browser_executor_token: str
    browser_request_timeout_seconds: int = Field(ge=1)

    @classmethod
    def from_env(cls) -> "KnowledgeSettings":
        return cls(
            **cls.common_values(),
            public_base_url=os.getenv(
                "KNOWLEDGE_PUBLIC_BASE_URL", "http://localhost:8001"
            ),
            data_dir=Path(os.getenv("KNOWLEDGE_DATA_DIR", "/app/data")),
            embedding_model=os.getenv(
                "EMBEDDING_MODEL", "text-embedding-3-small"
            ),
            agent_model=os.getenv("KNOWLEDGE_AGENT_MODEL", "gpt-6-luna"),
            mcp_bearer_token=_required("MCP_BEARER_TOKEN"),
            browser_executor_base_url=_required("BROWSER_EXECUTOR_BASE_URL"),
            browser_executor_token=_required("BROWSER_EXECUTOR_TOKEN"),
            browser_request_timeout_seconds=_positive_int(
                "BROWSER_REQUEST_TIMEOUT_SECONDS", 180
            ),
        )


class BrowserSettings(ServiceSettings):
    public_base_url: HttpUrl
    data_dir: Path
    agent_model: str
    agent_reasoning_effort: str
    browser_executor_token: str
    demo_api_token: str
    company_name: str
    start_url: HttpUrl
    allowed_domain: str
    max_pages: int = Field(ge=1, le=5)
    max_daily_browser_runs: int = Field(ge=1)
    max_concurrent_browser_runs: int = Field(ge=1)

    @classmethod
    def from_env(cls) -> "BrowserSettings":
        return cls(
            **cls.common_values(),
            public_base_url=os.getenv(
                "BROWSER_PUBLIC_BASE_URL", "http://localhost:8002"
            ),
            data_dir=Path(os.getenv("BROWSER_DATA_DIR", "/app/data")),
            agent_model=os.getenv("AGENT_MODEL", "gpt-6-luna"),
            agent_reasoning_effort=os.getenv("AGENT_REASONING_EFFORT", "low"),
            browser_executor_token=_required("BROWSER_EXECUTOR_TOKEN"),
            demo_api_token=_required("DEMO_API_TOKEN"),
            company_name=_required("COMPANY_NAME"),
            start_url=_required("START_URL"),
            allowed_domain=_required("ALLOWED_DOMAIN"),
            max_pages=_positive_int("MAX_PAGES", 5),
            max_daily_browser_runs=_positive_int("MAX_DAILY_BROWSER_RUNS", 20),
            max_concurrent_browser_runs=_positive_int(
                "MAX_CONCURRENT_BROWSER_RUNS", 1
            ),
        )
