"""应用配置：从环境变量 / .env 读取"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="PM_",
        extra="ignore",
    )

    # 应用
    app_name: str = "产教融合项目管理系统"
    debug: bool = False
    secret_key: str = "dev-secret-change-me-in-production"
    access_token_expire_minutes: int = 30
    algorithm: str = "HS256"

    # 数据库
    database_url: str = "postgresql+psycopg://postgres:123@localhost:5432/pm_dev"

    # 邮件
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_ssl: bool = True
    mail_from: str = ""

    # 附件
    upload_dir: str = "D:/data/pm/upload"
    upload_max_size_mb: int = 50

    # 定时任务
    scheduler_enabled: bool = False
    daily_scan_hour: int = 1
    digest_hour: int = 8
    digest_minute: int = 30

    # CORS（开发期前端 5173）
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

# 附件目录确保存在
Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
