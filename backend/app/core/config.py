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

    # 短信（可选，一期未接入实现）
    sms_enabled: bool = False
    sms_provider: str = ""
    sms_access_key: str = ""
    sms_secret_key: str = ""

    # 附件
    upload_dir: str = "D:/data/pm/upload"
    upload_max_size_mb: int = 50

    # 前端静态托管
    # 生产部署默认由 nginx 托管前端（frontend_serve=False）；
    # 设为 true 时由 FastAPI 直接托管，适合「不想装 nginx」的极简部署
    frontend_serve: bool = False
    frontend_dir: str = "/app/frontend_dist"

    # 定时任务
    scheduler_enabled: bool = False
    daily_scan_hour: int = 1
    digest_hour: int = 8
    digest_minute: int = 30

    # 数据保留（年）：用于运维清理脚本
    audit_retention_years: int = 2
    snapshot_retention_years: int = 3

    # CORS（开发期前端 5173）
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

# 附件目录确保存在
Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
