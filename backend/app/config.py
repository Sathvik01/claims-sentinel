from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    database_url: str = f"sqlite:///{(BACKEND_DIR / 'claims.db').as_posix()}"
    sample_data_dir: Path = PROJECT_DIR / "sample_data"
    upload_dir: Path = BACKEND_DIR / "uploads"

    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-120b"  # structured output (json_schema)
    groq_vision_model: str = "qwen/qwen3.8-27b"  # image / scanned-document input (JSON mode)
    llm_timeout_s: int = 60

    # Detection thresholds
    amount_tolerance: float = 1.0
    near_dup_threshold: float = 0.70
    near_dup_max_days: int = 3
    near_dup_max_amount_pct: float = 10.0
    outlier_z: float = 3.5
    outlier_min_ratio: float = 1.5
    severity_high: float = 25000.0
    severity_medium: float = 5000.0


settings = Settings()
