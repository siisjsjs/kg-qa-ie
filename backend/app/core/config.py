"""应用配置（pydantic-settings）。

优先级：环境变量 > .env 文件 > 默认值。
注意：LLM / Embedding 的运行时配置以 DB system_config 表为准（可前端动态切换），
本文件仅提供启动期默认/兜底值。
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ===== 应用 =====
    app_env: str = "dev"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_secret_key: str = "CHANGE_ME"
    app_config_encryption_key: str = ""  # base64 fernet key；为空则启用占位实现

    # ===== MySQL =====
    database_url: str = "mysql+pymysql://kgqa:CHANGE_ME@localhost:3306/kg_qa_ie?charset=utf8mb4"
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # ===== Neo4j =====
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "CHANGE_ME"

    # ===== Chroma =====
    chroma_host: str = "localhost"
    chroma_port: int = 8001
    chroma_persist_dir: str = "./data/chroma"

    # ===== Wiki =====
    wiki_root: str = "./data/wiki"
    cache_dir: str = "./data/cache"

    # ===== LLM 默认（兜底）=====
    llm_provider: str = "deepseek"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    llm_temperature: float = 0.3
    llm_max_tokens: int = 8192

    # ===== Embedding 默认 =====
    embedding_provider: str = "openai_compat"
    embedding_api_key: str = ""
    embedding_base_url: str = ""
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024

    # ===== JWT =====
    jwt_secret: str = "CHANGE_ME"
    jwt_algorithm: str = "HS256"
    jwt_access_ttl_min: int = 60
    jwt_refresh_ttl_days: int = 30

    # ===== CORS =====
    cors_origins: str = "http://localhost:5173,http://localhost"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def wiki_root_path(self) -> Path:
        return Path(self.wiki_root).resolve()

    @property
    def cache_dir_path(self) -> Path:
        return Path(self.cache_dir).resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
