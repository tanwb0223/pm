"""应用配置：从环境变量 / .env 读取"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（backend/ 的上一级）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

logger = logging.getLogger(__name__)

# 内置占位密钥：既不能用于生产，也不该在日志里被当成"已配置"
_PLACEHOLDER_SECRETS = {
    "dev-secret-change-me-in-production",
    "请替换为随机字符串（至少 32 位）",
    "change-me",
    "changeme",
    "secret",
}
_MIN_SECRET_LEN = 32


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
    # 8 小时：内网系统一天一班，30 分钟会让用户反复被踢下线。
    # 需要更严的时效时改 .env 的 PM_ACCESS_TOKEN_EXPIRE_MINUTES。
    access_token_expire_minutes: int = 480
    algorithm: str = "HS256"

    # 登录防爆破（同一账号 + 同一来源连续失败）
    login_max_attempts: int = 5
    login_lock_minutes: int = 15

    @model_validator(mode="after")
    def _check_secret_key(self) -> "Settings":
        """拒绝用占位密钥启动生产实例

        JWT 用 HS256 对称签名，密钥泄露 = 任何人都能伪造任意用户的 token。
        仓库里带了 .env.example，用户照抄就会拿到一个公开常量，
        所以这里必须硬拦一次，而不是只在文档里写"记得改"。
        """
        key = (self.secret_key or "").strip()
        weak = key in _PLACEHOLDER_SECRETS or len(key) < _MIN_SECRET_LEN
        if not weak:
            return self
        if self.debug:
            logger.warning(
                "PM_SECRET_KEY 仍是占位值或长度不足 %d 位，调试模式放行；"
                "上生产前务必替换。",
                _MIN_SECRET_LEN,
            )
            return self
        raise ValueError(
            "PM_SECRET_KEY 未设置或仍为占位值（长度需 ≥ %d）。"
            "请生成一个随机密钥后写入 backend/.env：\n"
            '    python -c "import secrets; print(secrets.token_urlsafe(48))"'
            % _MIN_SECRET_LEN
        )

    # ---------------------------------------------------------------- 数据库
    # 留空则自动使用 SQLite（零依赖，解压即用）
    # 需要 PostgreSQL 时显式指定：
    #   PM_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/dbname
    database_url: str = ""

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

    # ---------------------------------------------------------------- 数据目录
    # 默认按平台自适应，可用 PM_DATA_DIR 统一覆盖
    #   Linux:   /data/pm
    #   Windows: D:/data/pm  （无 D 盘时回退到项目目录下的 data/）
    data_dir: str = ""

    # 附件（留空则用 {data_dir}/upload）
    upload_dir: str = ""
    upload_max_size_mb: int = 50

    # 前端静态托管
    # 设为 true 时由 FastAPI 直接托管前端产物，无需 nginx
    frontend_serve: bool = False
    frontend_dir: str = ""

    # 数据库自动备份
    backup_enabled: bool = True          # 是否启用自动备份
    backup_hour: int = 2                 # 每日几点备份（避开 01:00 快照）
    backup_minute: int = 0
    backup_dir: str = ""                 # 留空则用 {data_dir}/backup
    backup_keep_days: int = 30           # 保留天数
    backup_script: str = ""              # 备份脚本路径（留空自动查找 scripts/backup.sh）

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


    def model_post_init(self, __context: object) -> None:
        """填充平台自适应路径

        优先级：环境变量显式指定 > 平台默认
        """
        import os
        import sys

        is_windows = sys.platform.startswith("win")

        # 1. 数据根目录
        if not self.data_dir:
            if is_windows:
                # Windows 优先用 D:/data/pm，D 盘不存在时回退到项目目录
                candidate = Path("D:/data/pm")
                try:
                    if candidate.drive and Path(candidate.drive + "/").exists():
                        self.data_dir = str(candidate)
                    else:
                        raise OSError
                except (OSError, ValueError):
                    self.data_dir = str(_PROJECT_ROOT / "data")
            else:
                self.data_dir = "/data/pm"

        data_root = Path(self.data_dir)

        # 2. 附件目录
        if not self.upload_dir:
            self.upload_dir = str(data_root / "upload")

        # 3. 备份目录
        if not self.backup_dir:
            self.backup_dir = str(data_root / "backup")

        # 4. 前端产物目录
        if not self.frontend_dir:
            self.frontend_dir = str(_PROJECT_ROOT.parent / "frontend" / "dist")

        # 5. 数据库连接串
        # 未配置时默认用 SQLite（零依赖），配置了就用配置的
        if not self.database_url:
            db_file = data_root / "pm.db"
            self.database_url = f"sqlite:///{db_file.as_posix()}"

    @property
    def log_dir(self) -> Path:
        return Path(self.data_dir) / "log"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

# 确保关键目录存在（失败不阻断启动，仅告警，便于首次部署排错）
import logging as _logging

_early_log = _logging.getLogger(__name__)

for _name, _d in (
    ("附件目录", settings.upload_dir),
    ("备份目录", settings.backup_dir),
    ("日志目录", str(settings.log_dir)),
):
    try:
        Path(_d).mkdir(parents=True, exist_ok=True)
    except OSError as _e:
        _early_log.warning("无法创建%s %s: %s", _name, _d, _e)
