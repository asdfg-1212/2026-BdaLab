"""集中读取部署配置；清洗规则独立存放。"""

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    data_dir: Path = ROOT / "ml-1m"
    artifact_dir: Path = ROOT / "artifacts"
    hadoop_home: Path = Path("/opt/hadoop")
    hadoop_timeout: int = 1800
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"


settings = Settings()
