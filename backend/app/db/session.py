"""数据库会话：同步 SQLAlchemy 2.0"""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

engine = create_engine(
    settings.database_url,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,      # 断线自动重连
    echo=settings.debug,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """所有模型的基类"""


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：每个请求一个会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
