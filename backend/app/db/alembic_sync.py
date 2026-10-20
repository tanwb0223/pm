"""自动处理 SQLite 的 Alembic 版本标记

问题背景：
    SQLite 首次启动时，init_db() 用 create_all 建表，
    但没有写 alembic_version。

    之后有人执行 alembic upgrade head，Alembic 以为数据库是空的，
    会从头执行 0001_init，报 "table sys_college already exists"。

解决方案：
    建表后立即写入 alembic 版本标记（stamp head）。
    这样：
    - 全新部署：create_all 建表 + stamp head
    - 后续升级：alembic upgrade head 只执行新增的迁移
    - 已有环境：检测到表存在但版本为空时自动补上
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

logger = logging.getLogger(__name__)

# 与 alembic/versions/ 下最新的 revision 保持一致
# 新增迁移时记得同步更新这里
LATEST_REVISION = "0004_attachment"


def alembic_version_of(db_url: str) -> str | None:
    """读取数据库当前的 alembic 版本"""
    engine = create_engine(db_url, pool_pre_ping=True)
    try:
        insp = inspect(engine)
        if "alembic_version" not in insp.get_table_names():
            return None
        with engine.connect() as conn:
            row = conn.execute(text("SELECT version_num FROM alembic_version")).first()
            return row[0] if row else None
    finally:
        engine.dispose()


def stamp_alembic(db_url: str, revision: str = LATEST_REVISION) -> bool:
    """写入 alembic 版本标记

    Returns:
        True 表示写入了新标记，False 表示已有标记或无表
    """
    engine = create_engine(db_url, pool_pre_ping=True)
    try:
        insp = inspect(engine)
        tables = insp.get_table_names()

        # 业务表还没建，不用标记
        if "sys_user" not in tables:
            return False

        if "alembic_version" not in tables:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "CREATE TABLE alembic_version ("
                        "version_num VARCHAR(32) NOT NULL, "
                        "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
                    )
                )
        else:
            with engine.connect() as conn:
                row = conn.execute(text("SELECT version_num FROM alembic_version")).first()
                if row and row[0]:
                    # 已有版本，不覆盖
                    return False

        with engine.begin() as conn:
            conn.execute(text("DELETE FROM alembic_version"))
            conn.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
                {"v": revision},
            )

        logger.info("Alembic 版本标记已写入：%s", revision)
        return True
    finally:
        engine.dispose()


def ensure_alembic_stamped(db_url: str) -> None:
    """建表后调用：确保 alembic 版本标记存在

    幂等：已有标记时什么都不做。
    """
    from app.db.dialect import is_sqlite

    if not is_sqlite(db_url):
        # PostgreSQL 用 alembic 管理，不需要这个
        return

    try:
        current = alembic_version_of(db_url)
        if current:
            logger.debug("Alembic 版本已存在：%s", current)
            return
        stamp_alembic(db_url)
    except Exception:
        logger.warning("写入 Alembic 版本标记失败（不影响使用）", exc_info=True)
