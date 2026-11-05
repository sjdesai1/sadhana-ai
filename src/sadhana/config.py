"""App settings, read from environment variables or a .env file."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SADHANA_", extra="ignore")

    env: str = "dev"
    configs_dir: Path = REPO_ROOT / "configs"
    outputs_dir: Path = REPO_ROOT / "outputs"
    database_url: str = "postgresql+psycopg://sadhana:sadhana@localhost:5432/sadhana"
    smtp_host: str = "localhost"
    smtp_port: int = 1025


settings = Settings()
