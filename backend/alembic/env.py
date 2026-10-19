# Alembic 配置：产教融合项目管理系统
# 数据库连接串从环境变量 PM_DATABASE_URL 读取，未设置时用本地默认值

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 优先读环境变量，方便容器化部署
DB_URL = os.getenv(
    "PM_DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/pm",
)
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
