"""模型包：集中导入，保证 Base.metadata 里注册了所有表

为什么要有这个文件：
- 测试与 `init_db()` 里如果漏 import 某个模型模块，`create_all` 就少建表
- 新模型（如 proj_change_log）只被服务层按需延迟导入，在「只做 create_all」
  的场景下不会被发现，容易出现「表不存在」的运行时错误
统一在这里导入一遍，任何 `import app.models` 都能拿到完整元数据。
"""
from __future__ import annotations

from app.models import (  # noqa: F401
    alert,
    attachment,
    auth,
    change_log,
    project,
    report,
    system,
)

__all__ = [
    "alert",
    "attachment",
    "auth",
    "change_log",
    "project",
    "report",
    "system",
]
