"""数据库方言抽象层

同时支持 PostgreSQL 与 SQLite，屏蔽两者差异。

设计原则：
1. **能用 SQLAlchemy 抽象就不用原生 SQL** —— ORM 层自动适配
2. **必须用原生 SQL 时**，通过本模块的辅助函数生成方言相关片段
3. **JSON 类型统一用 SA 的 JSON** —— PG 上映射 JSONB，SQLite 上映射 TEXT
4. **不依赖数据库特有功能**（如 advisory lock），改用应用层实现

支持的数据库：
    postgresql+psycopg://user:pass@host:5432/db
    sqlite:///path/to/pm.db          （相对路径）
    sqlite:////opt/pm/data/pm.db     （绝对路径）
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, Text, func
from sqlalchemy.dialects.postgresql import JSONB

logger = logging.getLogger(__name__)


# ================================================================ 方言识别
def is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def is_postgres(url: str) -> bool:
    return url.startswith("postgresql")


def dialect_of(url: str) -> str:
    """返回 'sqlite' 或 'postgres'"""
    if is_sqlite(url):
        return "sqlite"
    if is_postgres(url):
        return "postgres"
    raise ValueError(f"不支持的数据库类型：{url.split('://')[0]}")


# ================================================================ 类型映射
def json_type(url: str):
    """JSON 列类型

    PostgreSQL 用 JSONB（可索引、可查询）
    SQLite 用 JSON（实际存 TEXT，SQLAlchemy 自动序列化）
    """
    return JSONB if is_postgres(url) else JSON


# 模块级导出：模型里统一用这个
# 但模型加载时还不知道 URL，所以用 PG 的 JSONB 作为默认
# SQLAlchemy 会在建表时按实际方言转换
def json_column_type():
    """给模型用的 JSON 类型

    PG 上生成 JSONB，SQLite 上自动降级为 JSON。
    SQLAlchemy 的 TypeEngine 在 SQLite 方言下会把 JSONB 当作 JSON 处理。
    """
    from app.core.config import settings

    return json_type(settings.database_url)


# ================================================================ 日期运算
def days_ago(day: date, days: int) -> date:
    """N 天前的日期

    替代 SQL 的 `:day - INTERVAL 'N day'`
    """
    return day - timedelta(days=days)


def date_subtract_sql(url: str, column: str, param: str, days: int) -> str:
    """生成方言相关的「日期减 N 天」SQL 片段

    PostgreSQL: {column} > :{param} - INTERVAL '{days} day'
    SQLite:     {column} > date(:{param}, '-{days} day')
    """
    if is_postgres(url):
        return f"{column} > :{param} - INTERVAL '{days} day'"
    return f"{column} > date(:{param}, '-{days} day')"


def date_diff_sql(url: str, a: str, b: str) -> str:
    """生成方言相关的「日期差（天数）」SQL 片段

    PostgreSQL: ({a} - {b})
    SQLite:     julianday({a}) - julianday({b})
    """
    if is_postgres(url):
        return f"({a} - {b})"
    return f"(julianday({a}) - julianday({b}))"


def date_add_sql(url: str, column: str, days: int) -> str:
    """生成方言相关的「日期加 N 天」SQL 片段"""
    if is_postgres(url):
        return f"{column} + INTERVAL '{days} day'"
    return f"date({column}, '+{days} day')"


# ================================================================ 模糊搜索
def ilike(column, keyword: str):
    """大小写不敏感模糊匹配

    PostgreSQL: column ILIKE '%kw%'
    SQLite:     LOWER(column) LIKE LOWER('%kw%')
    """
    from app.core.config import settings

    if is_postgres(settings.database_url):
        return column.ilike(f"%{keyword}%")
    return func.lower(column).like(f"%{keyword.lower()}%")


# ================================================================ 分布式锁
# PostgreSQL 用 advisory lock；SQLite 用进程内文件锁
class DatabaseLock:
    """跨进程互斥锁

    PostgreSQL: pg_try_advisory_lock（数据库级，多实例安全）
    SQLite:     文件锁（单机多进程安全，SQLite 本身就是单机数据库）

    用法：
        lock = DatabaseLock(db, lock_id=2026100101)
        if lock.acquire():
            try:
                do_work()
            finally:
                lock.release()
    """

    def __init__(self, db, lock_id: int, lock_dir: str | None = None):
        self.db = db
        self.lock_id = lock_id
        self._held = False
        self._file_handle = None

        from app.core.config import settings

        self._url = settings.database_url
        self._lock_dir = lock_dir or str(Path(settings.data_dir) / "locks")
        Path(self._lock_dir).mkdir(parents=True, exist_ok=True)

    def acquire(self) -> bool:
        if is_postgres(self._url):
            return self._acquire_pg()
        return self._acquire_file()

    def release(self) -> None:
        if not self._held:
            return
        if is_postgres(self._url):
            self._release_pg()
        else:
            self._release_file()

    # ---------------------------------------------------------- PostgreSQL
    def _acquire_pg(self) -> bool:
        from sqlalchemy import text

        try:
            got = self.db.execute(
                text("SELECT pg_try_advisory_lock(:id)"), {"id": self.lock_id}
            ).scalar()
            self._held = bool(got)
            return self._held
        except Exception:
            logger.warning("advisory lock 获取失败", exc_info=True)
            return False

    def _release_pg(self) -> None:
        from sqlalchemy import text

        try:
            self.db.execute(
                text("SELECT pg_advisory_unlock(:id)"), {"id": self.lock_id}
            )
            self.db.commit()
        except Exception:
            logger.warning("advisory lock 释放失败", exc_info=True)
        self._held = False

    # ---------------------------------------------------------- SQLite（文件锁）
    def _acquire_file(self) -> bool:
        lock_file = Path(self._lock_dir) / f"lock_{self.lock_id}.pid"
        try:
            # O_EXCL 保证原子性：文件已存在说明别人持有
            fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            self._file_handle = (fd, lock_file)
            self._held = True
            return True
        except FileExistsError:
            # 检查持有者是否还活着（防止进程崩溃留下僵尸锁）
            try:
                pid = int(lock_file.read_text().strip() or 0)
                if pid and not _pid_alive(pid):
                    logger.warning("清理僵尸锁：%s（PID %s 已退出）", lock_file, pid)
                    lock_file.unlink(missing_ok=True)
                    return self._acquire_file()   # 重试一次
            except (ValueError, OSError):
                lock_file.unlink(missing_ok=True)
                return self._acquire_file()
            return False

    def _release_file(self) -> None:
        if self._file_handle:
            fd, lock_file = self._file_handle
            try:
                os.close(fd)
            except OSError:
                pass
            lock_file.unlink(missing_ok=True)
            self._file_handle = None
        self._held = False


def _pid_alive(pid: int) -> bool:
    """检查进程是否存活（跨平台）"""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


# ================================================================ 建库辅助
def sqlite_connect_args(url: str) -> dict[str, Any]:
    """SQLite 连接参数

    - check_same_thread=False：FastAPI 多线程访问
    - timeout=30：写锁等待 30 秒（避免 "database is locked"）
    """
    if is_sqlite(url):
        return {"check_same_thread": False, "timeout": 30}
    return {}


def sqlite_engine_kwargs(url: str) -> dict[str, Any]:
    """SQLite 引擎参数"""
    if not is_sqlite(url):
        return {}
    return {
        # SQLite 不支持连接池参数，用 NullPool 避免线程问题
        "pool_pre_ping": False,
    }


def ensure_sqlite_dir(url: str) -> None:
    """确保 SQLite 数据库文件所在目录存在"""
    if not is_sqlite(url):
        return
    # sqlite:///path 或 sqlite:////abs/path
    db_path = url.replace("sqlite:///", "").replace("sqlite://", "")
    if db_path and db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)


# ================================================================ 序列化兼容
def serialize_date(value: Any) -> Any:
    """SQLite 存日期为字符串，读取时可能返回 str

    统一转换为 date/datetime 对象，避免上层类型不一致。
    """
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return value
