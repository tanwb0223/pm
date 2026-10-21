"""角色默认权限映射

设计原则（对应需求文档 6.2 角色矩阵）：
    看得到 → 做得了 → 管得住
每级角色在下一级基础上叠加职责相关权限，避免"只有超管能用"。

本文件是**唯一权威定义**：
- 全新部署：app/db/session.py 的 init_seed_data() 读这里灌数据
- 已有部署：alembic/versions/0005_role_perms.py 读这里补授权
"""
from __future__ import annotations

# ================================================================ 权限点全集
# 与 0001_init.py / session.py 里的权限点保持一致（38 项）
ALL_PERMISSIONS: tuple[str, ...] = (
    # 模块
    "dashboard", "project", "progress", "alert", "report", "export", "risk", "system",
    # 页面
    "dashboard:view",
    "project:major", "project:college", "project:sub",
    "progress:table", "alert:list", "risk:list",
    "system:user", "system:role", "system:org", "system:config",
    "system:holiday", "system:audit",
    # 操作
    "project:major:view", "project:major:edit",
    "project:college:edit",
    "project:sub:view", "project:sub:edit", "project:check",
    "progress:update", "progress:onbehalf", "progress:urge",
    "alert:claim", "alert:close", "alert:transfer",
    "report:fill", "report:void", "report:grant",
    "export:ledger", "export:progress",
    "risk:edit",
)

# ================================================================ 基础只读
VIEWER_PERMS: tuple[str, ...] = (
    "dashboard", "dashboard:view",
    "project", "project:major", "project:major:view",
    "project:sub", "project:sub:view",
    "progress", "progress:table",
    "alert", "alert:list",
    "export", "export:ledger", "export:progress",
)

# ================================================================ 角色矩阵
ROLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    # 只读用户：仅查看
    "VIEWER": VIEWER_PERMS,

    # 填报人：填进度、填报表
    # 关键：必须有 report + report:fill，否则看不到"报表填报"入口
    "REPORTER": VIEWER_PERMS + (
        "report", "report:fill",
        "progress:update",
        "risk", "risk:list",
    ),

    # 子项目负责人：在填报人基础上，可认领预警
    "SUB_OWNER": VIEWER_PERMS + (
        "report", "report:fill",
        "progress:update",
        "risk", "risk:list",
        "alert:claim",
    ),

    # 学院负责人：可代填、可关闭预警、可开启逾期补填
    "COLLEGE_OWNER": VIEWER_PERMS + (
        "report", "report:fill", "report:grant",
        "progress:update", "progress:onbehalf",
        "risk", "risk:list",
        "alert:claim", "alert:close",
        "project:college:edit",
    ),

    # 大项目负责人：可催办、可编辑风险、可转交预警、可开启补填
    "MAJOR_OWNER": VIEWER_PERMS + (
        "report", "report:fill", "report:grant",
        "progress:update", "progress:onbehalf", "progress:urge",
        "risk", "risk:list", "risk:edit",
        "alert:claim", "alert:close", "alert:transfer",
        "project:check",
        "project:major:edit", "project:sub:edit",
    ),

    # 项目总负责人：除系统管理外的全部业务权限
    "PROJECT_OWNER": VIEWER_PERMS + (
        "report", "report:fill", "report:void", "report:grant",
        "progress:update", "progress:onbehalf", "progress:urge",
        "risk", "risk:list", "risk:edit",
        "alert:claim", "alert:close", "alert:transfer",
        "project:check",
        "project:major:edit", "project:sub:edit", "project:college:edit",
    ),

    # 系统管理员：全部权限（登录时 useCan 也会短路放行）
    "SYS_ADMIN": ALL_PERMISSIONS,
}


def perms_for(role_code: str) -> tuple[str, ...]:
    """取某角色的默认权限点"""
    return ROLE_PERMISSIONS.get(role_code, ())