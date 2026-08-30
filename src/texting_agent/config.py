"""Application settings. Fails fast at import if the environment is invalid."""

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Required from M5 onward. Empty is fine until then.
    openai_api_key: str = ""


settings = Settings()
