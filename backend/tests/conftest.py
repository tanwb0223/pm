"""测试公共夹具

注意：必须在 import app.* 之前设置 PM_SECRET_KEY，
否则 config 的密钥校验会让整个测试进程起不来。
"""
from __future__ import annotations

import os

os.environ.setdefault("PM_SECRET_KEY", "test-secret-key-for-pytest-only-0123456789")
os.environ.setdefault("PM_DEBUG", "false")

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402


@pytest.fixture()
def db():
    """内存 SQLite 会话（每用例独立建表）"""
    # 触发所有模型注册到 metadata
    import app.models.alert  # noqa: F401
    import app.models.attachment  # noqa: F401
    import app.models.auth  # noqa: F401
    import app.models.project  # noqa: F401
    import app.models.report  # noqa: F401
    import app.models.system  # noqa: F401
    from app.db.session import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
