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
    - 已有库但版本落后：alembic upgrade head 只执行新增的迁移
    - 已有环境：检测到表存在但版本为空时自动补上
    - 历史漂移（create_all 不执行迁移导致的模型/库不一致）：
      由 repair_sqlite_schema() 按已知清单修复
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

logger = logging.getLogger(__name__)

# 与 alembic/versions/ 下最新的 revision 保持一致
# 新增迁移时记得同步更新这里
LATEST_REVISION = "0007_report_grace"

# alembic.ini 位置：app/db/alembic_sync.py → backend/alembic.ini
_ALEMBIC_INI = Path(__file__).resolve().parent.parent.parent / "alembic.ini"


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


# ---------------------------------------------------------------- 升级与修复
def run_alembic_upgrade(db_url: str) -> bool:
    """对已有库执行 alembic upgrade head（SQLite 专用）

    只在版本标记落后于 head 时才有事情做；追平后是空转。
    任何失败都只记日志，不阻断启动（SQLite 部署允许降级运行）。
    """
    from app.db.dialect import is_sqlite

    if not is_sqlite(db_url) or not _ALEMBIC_INI.is_file():
        return False

    try:
        current = alembic_version_of(db_url)
        if not current or current == LATEST_REVISION:
            return False

        from alembic import command
        from alembic.config import Config

        cfg = Config(str(_ALEMBIC_INI))
        cfg.set_main_option("sqlalchemy.url", db_url)
        logger.info("检测到 Alembic 版本落后（%s → %s），执行迁移...", current, LATEST_REVISION)
        command.upgrade(cfg, "head")
        logger.info("Alembic 迁移完成")
        return True
    except Exception:
        logger.warning("Alembic 迁移失败（不影响本次启动，请手动执行 alembic upgrade head）",
                       exc_info=True)
        return False


# 已知的历史漂移：SQLite 走 create_all，模型改过的约束不会同步到旧库。
# 每项 = (表名, 修复函数)。修复函数接收 (engine, insp)。
def _repair_alert_action_operator_id(engine, insp) -> bool:
    """alert_action.operator_id 恢复可为空（对齐 alembic 0003 与模型）

    SQLite 不支持直接改列约束，按标准做法重建表：
    建新表 → 拷数据 → 删旧表 → 重命名。
    """
    if "alert_action" not in insp.get_table_names():
        return False
    cols = {c["name"]: c for c in insp.get_columns("alert_action")}
    col = cols.get("operator_id")
    if col is None or col.get("nullable", True):
        return False  # 不存在或已是可空，无需修复

    logger.info("修复历史漂移：alert_action.operator_id 重建为可空")
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alert_action_migrate_tmp"))
        conn.execute(
            text(
                """
                CREATE TABLE alert_action_migrate_tmp (
                    id INTEGER NOT NULL,
                    alert_id INTEGER NOT NULL,
                    action VARCHAR(16) NOT NULL,
                    operator_id INTEGER,
                    reason VARCHAR(500),
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    PRIMARY KEY (id),
                    CONSTRAINT ck_action_type CHECK (action IN ('CLAIM','TRANSFER','CLOSE','IGNORE','ESCALATE')),
                    FOREIGN KEY(alert_id) REFERENCES alert (id),
                    FOREIGN KEY(operator_id) REFERENCES sys_user (id)
                )
                """
            )
        )
        conn.execute(
            text(
                "INSERT INTO alert_action_migrate_tmp "
                "(id, alert_id, action, operator_id, reason, created_at) "
                "SELECT id, alert_id, action, operator_id, reason, created_at FROM alert_action"
            )
        )
        conn.execute(text("DROP TABLE alert_action"))
        conn.execute(text("ALTER TABLE alert_action_migrate_tmp RENAME TO alert_action"))
    return True


def _repair_user_password_columns(engine, insp) -> bool:
    """sys_user 补上改密相关列（对齐 alembic 0006 与模型）

    SQLite 支持 ADD COLUMN，无需重建表。
    """
    if "sys_user" not in insp.get_table_names():
        return False
    cols = {c["name"] for c in insp.get_columns("sys_user")}
    added = False
    with engine.begin() as conn:
        if "must_change_password" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE sys_user ADD COLUMN must_change_password "
                    "BOOLEAN NOT NULL DEFAULT 0"
                )
            )
            added = True
        if "password_updated_at" not in cols:
            conn.execute(
                text("ALTER TABLE sys_user ADD COLUMN password_updated_at DATETIME")
            )
            added = True
    if added:
        logger.info("修复历史漂移：sys_user 补齐改密列")
    return added


_KNOWN_REPAIRS = [
    _repair_alert_action_operator_id,
    _repair_user_password_columns,
]


def repair_sqlite_schema(db_url: str) -> int:
    """修复 SQLite 已知的结构漂移（幂等）

    create_all 只会建新表、不会改旧表，所以历史上"模型改了但库没改"
    的列约束会一直错下去（典型：alert_action.operator_id）。
    这里按清单逐项检查修复，返回修复项数。
    """
    from app.db.dialect import is_sqlite

    if not is_sqlite(db_url):
        return 0

    engine = create_engine(db_url, pool_pre_ping=True)
    try:
        insp = inspect(engine)
        if "alert" not in insp.get_table_names():
            return 0  # 还没建表，全新库无需修复
        fixed = sum(1 for fn in _KNOWN_REPAIRS if fn(engine, insp))
        if fixed:
            logger.info("SQLite 结构漂移修复完成：%d 项", fixed)
        return fixed
    except Exception:
        logger.warning("SQLite 结构漂移修复失败（不影响使用）", exc_info=True)
        return 0
    finally:
        engine.dispose()
