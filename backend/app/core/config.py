from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "CORTEX"
    app_version: str = "0.1.0"
    environment: str = Field(default="development")
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = Field(default_factory=lambda: [
        "http://localhost:3000",
        "http://localhost:5173",
        "https://cortex-frontend.netlify.app"
    ])
    enable_testing_routes: bool = False

    # API key guarding mutation endpoints (POST /fail, /recover, /recovery,
    # /sandbox/inject-failure). Empty (default) means the guard is OFF — local
    # dev and the demo sandbox work without a key. Set CORTEX_API_KEY to any
    # non-empty value to require `X-API-Key: <key>` on those routes.
    cortex_api_key: str = ""

    hydradb_api_key: str | None = None
    hydradb_project_id: str = "cortex-local"
    grok_api_key: str | None = None
    grok_base_url: str = "https://api.x.ai/v1"
    grok_model: str = "latest"
    grok_timeout_seconds: float = 8.0
    grok_max_retries: int = 2
    reasoning_confidence_threshold: int = 72
    reasoning_prompt_cache_ttl_seconds: int = 300
    reasoning_ws_batch_window_ms: int = 250

    detection_latency_target_ms: int = 100
    recovery_time_target_ms: int = 1000
    recovery_success_target: float = 99.97


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
