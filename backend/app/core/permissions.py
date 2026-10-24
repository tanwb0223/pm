"""角色默认权限映射

设计原则（对应需求文档 6.2 角色矩阵）：
    看得到 → 做得了 → 管得住
每级角色在下一级基础上叠加职责相关权限，避免"只有超管能用"。

本文件是**唯一权威定义**：
- 全新部署：app/db/session.py 的 init_seed_data() 读这里灌数据
- 已有部署：alembic/versions/0005_role_perms.py 读这里补授权
- 新增权限点：0008_permissions_fix.py 读这里补

## 与需求附录二的对齐（2026-10-24 修订）

早期矩阵有两处方向性错误，导致核心角色干不了本职：
1. COLLEGE_OWNER 缺 `project:sub:edit` / `progress:urge` / `project:sub:close`
   → 学院负责人建不了本学院子项目、结项确认不了、催不了办（需求 2.2、5.3、6.3-25）
2. PROJECT_OWNER 有 `report:void`
   → 需求 3.4 与附录二都写「作废只有系统管理员能做」
3. SUB_OWNER 缺 `alert:close`
   → 需求 3.3(4) 已认领状态可「关闭」，子项目负责人只能接单不能结单
4. VIEWER 缺 `report`
   → 需求 2.2「查看者：查看看板与报表」，4.5「校领导可看全校」，
     但前端路由守卫把只读用户挡在报表页外

同时把「导出」权限真正接入接口（export:ledger / export:progress 原本是死权限点）。
"""
from __future__ import annotations

# ================================================================ 权限点全集
# 与 0001_init.py / session.py 里的权限点保持一致（40 项）
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
    "project:sub:view", "project:sub:edit", "project:sub:close", "project:check",
    "progress:update", "progress:onbehalf", "progress:urge",
    "alert:claim", "alert:close", "alert:transfer",
    "report:fill", "report:void", "report:grant",
    "export:ledger", "export:progress",
    "risk:edit",
)

# ================================================================ 基础只读
# 注意：VIEWER 保留 export 系列（需求 4.5「校领导和质量督导……可以导出」）；
# REPORTER 等角色不再从 VIEWER_PERMS 继承 export（附录二：填报人导出=—）。
VIEWER_PERMS: tuple[str, ...] = (
    "dashboard", "dashboard:view",
    "project", "project:major", "project:major:view",
    "project:sub", "project:sub:view",
    "progress", "progress:table",
    "alert", "alert:list",
    "report",                       # 查看报表列表/及时率（只读，不含 report:fill）
    "export", "export:ledger", "export:progress",
)

# 非查看者角色的公共底包：不含导出（附录二：填报人/子项目负责人导出=—）
_BASE: tuple[str, ...] = (
    "dashboard", "dashboard:view",
    "project", "project:major", "project:major:view",
    "project:sub", "project:sub:view",
    "progress", "progress:table",
    "alert", "alert:list",
)

# ================================================================ 角色矩阵
ROLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    # 只读用户（校领导、质量督导）：能看全校、能导出，什么都不能改
    "VIEWER": VIEWER_PERMS,

    # 填报人：填进度、填报表；导出=—（需求附录二）
    "REPORTER": _BASE + (
        "report", "report:fill",
        "progress:update",
        "risk", "risk:list",
    ),

    # 子项目负责人：在填报人基础上，可认领并关闭本人相关预警
    "SUB_OWNER": _BASE + (
        "report", "report:fill",
        "progress:update",
        "risk", "risk:list",
        "alert:claim", "alert:close",
    ),

    # 学院负责人：建/编辑本学院子项目、结项确认、代填、催办、开补填/更正
    "COLLEGE_OWNER": _BASE + (
        "report", "report:fill", "report:grant",
        "progress:update", "progress:onbehalf", "progress:urge",
        "risk", "risk:list",
        "alert:claim", "alert:close",
        "project:college:edit",
        # 需求 2.2「建立本学院子项目、指定子项目负责人」+ 6.1 第 6 项「结项确认」
        "project:sub:edit", "project:sub:close",
        "project:check",
        "export", "export:ledger", "export:progress",
    ),

    # 大项目负责人：可催办、可编辑风险、可转交预警、可开启补填/更正
    "MAJOR_OWNER": _BASE + (
        "report", "report:fill", "report:grant",
        "progress:update", "progress:onbehalf", "progress:urge",
        "risk", "risk:list", "risk:edit",
        "alert:claim", "alert:close", "alert:transfer",
        "project:check",
        "project:major:edit", "project:sub:edit", "project:sub:close",
        "export", "export:ledger", "export:progress",
    ),

    # 项目总负责人：除系统管理外的全部业务权限
    # 注意：不含 report:void —— 需求 3.4 / 附录二：作废只有系统管理员能做
    "PROJECT_OWNER": _BASE + (
        "report", "report:fill", "report:grant",
        "progress:update", "progress:onbehalf", "progress:urge",
        "risk", "risk:list", "risk:edit",
        "alert:claim", "alert:close", "alert:transfer",
        "project:check",
        "project:major:edit", "project:sub:edit", "project:sub:close",
        "project:college:edit",
        "export", "export:ledger", "export:progress",
    ),

    # 系统管理员：全部权限（登录时 useCan 也会短路放行）
    "SYS_ADMIN": ALL_PERMISSIONS,
}


def perms_for(role_code: str) -> tuple[str, ...]:
    """取某角色的默认权限点"""
    return ROLE_PERMISSIONS.get(role_code, ())


# ================================================================ 权限点元数据
# 种子数据与迁移都从这里生成，杜绝「ALL_PERMISSIONS 声明了 39/40 项、
# session.py 只写了 38 项」这类漂移（历史上就因此漏了 report:grant，
# 导致全新部署没人能开逾期补填）。
PERMISSION_META: dict[str, tuple[str, str]] = {
    "dashboard": ("我的督办", "MODULE"),
    "dashboard:view": ("查看督办", "PAGE"),
    "project": ("项目管理", "MODULE"),
    "project:major": ("大项目", "PAGE"),
    "project:major:view": ("查看大项目", "ACTION"),
    "project:major:edit": ("编辑大项目", "ACTION"),
    "project:college": ("学院分工", "PAGE"),
    "project:college:edit": ("编辑学院分工", "ACTION"),
    "project:sub": ("子项目", "PAGE"),
    "project:sub:view": ("查看子项目", "ACTION"),
    "project:sub:edit": ("编辑子项目", "ACTION"),
    "project:sub:close": ("结项确认", "ACTION"),
    "project:check": ("完整性检查", "ACTION"),
    "progress": ("进度管控", "MODULE"),
    "progress:table": ("进度总表", "PAGE"),
    "progress:update": ("更新进度", "ACTION"),
    "progress:onbehalf": ("代填进度", "ACTION"),
    "progress:urge": ("催办", "ACTION"),
    "alert": ("预警管理", "MODULE"),
    "alert:list": ("预警列表", "PAGE"),
    "alert:claim": ("认领预警", "ACTION"),
    "alert:close": ("关闭预警", "ACTION"),
    "alert:transfer": ("转交预警", "ACTION"),
    "report": ("报表管理", "MODULE"),
    "report:fill": ("填报报表", "ACTION"),
    "report:void": ("作废报表", "ACTION"),
    "report:grant": ("开启补填/更正", "ACTION"),
    "export": ("数据导出", "MODULE"),
    "export:ledger": ("导出台账", "ACTION"),
    "export:progress": ("导出进度", "ACTION"),
    "risk": ("风险登记", "MODULE"),
    "risk:list": ("风险列表", "PAGE"),
    "risk:edit": ("编辑风险", "ACTION"),
    "system": ("系统管理", "MODULE"),
    "system:user": ("用户管理", "PAGE"),
    "system:role": ("角色权限", "PAGE"),
    "system:org": ("组织管理", "PAGE"),
    "system:config": ("参数配置", "PAGE"),
    "system:holiday": ("免填日历", "PAGE"),
    "system:audit": ("操作日志", "PAGE"),
}

# 启动自检：权限点全集与元数据必须一一对应
_missing_meta = set(ALL_PERMISSIONS) - set(PERMISSION_META)
_extra_meta = set(PERMISSION_META) - set(ALL_PERMISSIONS)
if _missing_meta or _extra_meta:
    raise RuntimeError(
        f"权限点定义不一致：缺元数据 {sorted(_missing_meta)}，多出 {sorted(_extra_meta)}"
    )


def permission_rows() -> list[tuple[str, str, str, int]]:
    """按 ALL_PERMISSIONS 的顺序生成 (code, name, type, sort)"""
    return [
        (code, PERMISSION_META[code][0], PERMISSION_META[code][1], i)
        for i, code in enumerate(ALL_PERMISSIONS)
    ]

