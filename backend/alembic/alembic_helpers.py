"""Alembic 迁移辅助：屏蔽 PostgreSQL / SQLite 差异

问题：
    迁移文件里有大量 PG 专属语法（COMMENT ON、JSONB、GIN 索引、
    Identity），在 SQLite 上执行会报错。

方案：
    提供辅助函数，在 SQLite 上自动跳过不兼容语句。

用法：
    from alembic_helpers import cmt, gin_index, json_column, pk_column

    pk_column()                      # 自动适配
    json_column(nullable=False)      # 自动适配
    cmt("sys_user", "用户表")        # SQLite 上跳过
    gin_index("report", "content")   # SQLite 上跳过
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# ================================================================ 方言判断
def current_dialect() -> str:
    """当前迁移运行的数据库类型"""
    return op.get_context().dialect.name


def is_postgres() -> bool:
    return current_dialect() == "postgresql"


def is_sqlite() -> bool:
    return current_dialect() == "sqlite"


# ================================================================ 类型
def pk_column() -> sa.Column:
    """自增主键列

    PG:     BIGINT GENERATED ALWAYS AS IDENTITY
    SQLite: INTEGER PRIMARY KEY（自增）
    """
    if is_postgres():
        return sa.Column(
            "id", sa.BigInteger(), sa.Identity(always=True), primary_key=True
        )
    return sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True)


def bigint_column(name: str, **kwargs) -> sa.Column:
    """BIGINT 列

    SQLite 上 BIGINT 会导致自增失效（只有 INTEGER PRIMARY KEY 才自增），
    所以非主键的 BIGINT 在 SQLite 上降级为 INTEGER。
    """
    if is_postgres():
        return sa.Column(name, sa.BigInteger(), **kwargs)
    return sa.Column(name, sa.Integer(), **kwargs)


def json_column(name: str, **kwargs) -> sa.Column:
    """JSON 列

    PG:     JSONB（可索引、可查询）
    SQLite: JSON（存 TEXT）
    """
    if is_postgres():
        return sa.Column(name, postgresql.JSONB(), **kwargs)
    return sa.Column(name, sa.JSON(), **kwargs)


# ================================================================ 注释
def cmt(table: str, text: str) -> None:
    """表注释（SQLite 不支持 COMMENT ON，自动跳过）"""
    if not is_postgres():
        return
    safe = text.replace("'", "''")
    op.execute(f"COMMENT ON TABLE {table} IS '{safe}'")


def col_cmt(table: str, column: str, text: str) -> None:
    """列注释（SQLite 自动跳过）"""
    if not is_postgres():
        return
    safe = text.replace("'", "''")
    op.execute(f"COMMENT ON COLUMN {table}.{column} IS '{safe}'")


# ================================================================ 索引
def gin_index(table: str, column: str, name: str | None = None) -> None:
    """GIN 索引（仅 PG，SQLite 跳过）"""
    if not is_postgres():
        return
    idx = name or f"idx_{table}_{column}_gin"
    op.execute(f"CREATE INDEX {idx} ON {table} USING GIN ({column})")


def trgm_index(table: str, column: str, name: str | None = None) -> None:
    """pg_trgm 模糊搜索索引（仅 PG）"""
    if not is_postgres():
        return
    idx = name or f"idx_{table}_{column}_trgm"
    op.execute(f"CREATE INDEX {idx} ON {table} USING GIN ({column} gin_trgm_ops)")


def partial_index(
    name: str, table: str, columns: list[str], where: str
) -> None:
    """部分索引（PG 与 SQLite 都支持，语法一致）"""
    cols = ", ".join(columns)
    op.execute(f"CREATE INDEX {name} ON {table} ({cols}) WHERE {where}")


# ================================================================ 扩展
def create_extensions() -> None:
    """创建 PG 扩展（SQLite 跳过）"""
    if not is_postgres():
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")


# ================================================================ 原生 SQL
def execute_pg(sql: str) -> None:
    """执行 PG 专属 SQL（SQLite 上跳过）"""
    if not is_postgres():
        return
    op.execute(sql)


def execute_sqlite(sql: str) -> None:
    """执行 SQLite 专属 SQL（PG 上跳过）"""
    if not is_sqlite():
        return
    op.execute(sql)


# ================================================================ 时间默认值
def now_default():
    """当前时间默认值

    PG:     now()
    SQLite: CURRENT_TIMESTAMP
    """
    if is_postgres():
        return sa.text("now()")
    return sa.text("CURRENT_TIMESTAMP")


# ================================================================ UPSERT
def upsert(table: str, columns: list[str], values: list[str], conflict_cols: list[str]) -> None:
    """INSERT ... ON CONFLICT DO NOTHING

    PG 与 SQLite 3.24+ 语法一致，但为兼容更早版本 SQLite 用 INSERT OR IGNORE。
    """
    cols = ", ".join(columns)
    vals = ", ".join(values)

    if is_postgres():
        conflict = ", ".join(conflict_cols)
        op.execute(f"INSERT INTO {table} ({cols}) VALUES ({vals}) ON CONFLICT ({conflict}) DO NOTHING")
    else:
        op.execute(f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({vals})")


def upsert_sql(table: str, columns: str, values: str, conflict_cols: str) -> str:
    """返回 UPSERT 的 SQL 字符串（用于 op.execute 前判断）"""
    if is_postgres():
        return f"INSERT INTO {table} ({columns}) VALUES ({values}) ON CONFLICT ({conflict_cols}) DO NOTHING"
    return f"INSERT OR IGNORE INTO {table} ({columns}) VALUES ({values})"


# ================================================================ UPSERT（带参数）
def upsert_text(sql_pg: str, sql_sqlite: str | None = None):
    """返回 UPSERT 的 text() 对象

    PG 用 ON CONFLICT (col) DO NOTHING
    SQLite 用 INSERT OR IGNORE（兼容性最好）

    参数：
        sql_pg:     PG 版 SQL
        sql_sqlite: SQLite 版 SQL（不传则自动把 INSERT INTO 换成 INSERT OR IGNORE INTO）
    """
    from sqlalchemy import text

    if is_postgres():
        return text(sql_pg)

    if sql_sqlite is None:
        # 自动转换：INSERT INTO → INSERT OR IGNORE INTO，去掉 ON CONFLICT 部分
        import re

        sql = sql_pg.replace("INSERT INTO", "INSERT OR IGNORE INTO")
        sql = re.sub(r"\s*ON CONFLICT\s*\([^)]*\)\s*DO NOTHING", "", sql)
        sql = re.sub(r"\s*ON CONFLICT\s+DO NOTHING", "", sql)
        return text(sql)
    return text(sql_sqlite)


def insert_ignore(table: str, columns: list[str]) -> str:
    """生成 INSERT / INSERT OR IGNORE 的前缀（配合 bindparams 用）"""
    cols = ", ".join(columns)
    params = ", ".join(":" + c for c in columns)
    if is_postgres():
        return f"INSERT INTO {table} ({cols}) VALUES ({params})"
    return f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({params})"
