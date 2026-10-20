# Alembic 配置：产教融合项目管理系统
# 数据库连接串从环境变量 PM_DATABASE_URL 读取，未设置时用本地默认值

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# 让 alembic/ 目录可被导入（alembic_helpers.py 在这里）
_ALEMBIC_DIR = Path(__file__).resolve().parent
if str(_ALEMBIC_DIR) not in sys.path:
    sys.path.insert(0, str(_ALEMBIC_DIR))

# 让 backend/ 可被导入（app 包）
_BACKEND_DIR = _ALEMBIC_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 数据库连接：优先环境变量，其次读应用配置，最后兜底 SQLite
DB_URL = os.getenv("PM_DATABASE_URL", "")
if not DB_URL:
    try:
        from app.core.config import settings

        DB_URL = settings.database_url
    except Exception:
        DB_URL = "sqlite:///./data/pm.db"

# SQLite 相对路径要基于 backend 目录解析
if DB_URL.startswith("sqlite:///") and not DB_URL.startswith("sqlite:////"):
    rel = DB_URL.replace("sqlite:///", "")
    if not os.path.isabs(rel):
        DB_URL = "sqlite:///" + str((_BACKEND_DIR / rel).as_posix())

config.set_main_option("sqlalchemy.url", DB_URL)

target_metadata = None   # 本迁移为手写，不用 autogenerate


def run_migrations_offline() -> None:
    context.configure(
        url=DB_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
