"""Application settings. Fails fast at import if the environment is invalid."""

from enum import StrEnum

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Role(StrEnum):
    OPERATOR = "operator"
    APPROVER = "approver"


class ApiKey(BaseModel):
    """One credential. Roles do not nest: an operator key cannot approve, and an
    approver key cannot create campaigns `[AZ-04]`. Separation of duties is the
    only reason to have two roles at all, and a hierarchy would dissolve it.
    """

    key_id: str = Field(min_length=1)      # logged; identifies who acted [AU-06]
    secret: str = Field(min_length=8)      # never logged, traced or returned [AU-05]
    role: Role
    accounts: list[str] = Field(min_length=1)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"                # "dev" gates the event-simulation endpoint (M8)
    log_level: str = "INFO"
    host: str = "127.0.0.1"
    port: int = 8000

    config_dir: str = "config"      # scoring.yaml, playbooks.yaml

    # Two files, not two tables: the agent's connection cannot reach app state
    # even if every other control fails.
    agent_db_path: str = "data/customer_agent.db"
    app_db_path: str = "data/app.db"

    # JSON in the environment, never in the repo `[AU-03]`, `[SEC-08]`.
    api_keys: list[ApiKey] = []
    rate_limit_per_minute: int = 30     # per key, on the expensive routes [SEC-13]

    # Required from M5 onward. Empty is fine until then.
    openai_api_key: str = ""

    # gpt-5-nano is the cheapest model on the current price list. Six per-stage
    # overrides rather than one, so a quality problem on a single stage is one
    # environment variable and not a blanket upgrade of all six.
    openai_model_default: str = "gpt-5-nano"
    openai_model_analyze: str = ""
    openai_model_segment: str = ""
    openai_model_plan: str = ""
    openai_model_generate: str = ""
    openai_model_optimize: str = ""
    openai_model_query: str = ""

    llm_timeout_seconds: float = 60.0
    llm_max_attempts: int = 3           # transient failures only
    token_budget_per_campaign: int = 60_000     # hard cap [NFR-04]
    agent_max_tool_iterations: int = 6          # [EC-18]

    @property
    def stage_models(self) -> dict[str, str]:
        return {
            stage: getattr(self, f"openai_model_{stage}") or self.openai_model_default
            for stage in ("analyze", "segment", "plan", "generate", "optimize", "query")
        }


settings = Settings()
