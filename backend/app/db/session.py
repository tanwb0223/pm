"""数据库会话：同时支持 PostgreSQL 与 SQLite

两种数据库的引擎参数不同：
    PostgreSQL  连接池（pool_size/max_overflow）+ pre_ping
    SQLite      无连接池概念，需 check_same_thread=False + timeout

用法不变：
    from app.db.session import SessionLocal, get_db, Base
"""
from __future__ import annotations

import logging
from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings
from app.db.dialect import ensure_sqlite_dir, is_sqlite

logger = logging.getLogger(__name__)

_url = settings.database_url


def _build_engine():
    """按数据库类型构建引擎"""
    if is_sqlite(_url):
        ensure_sqlite_dir(_url)
        eng = create_engine(
            _url,
            echo=settings.debug,
            future=True,
            # SQLite 必须：允许跨线程（FastAPI 多线程）
            connect_args={
                "check_same_thread": False,
                "timeout": 30,          # 写锁等待 30 秒
            },
        )

        # 开启 WAL 模式：读写并发能力大幅提升
        @event.listens_for(eng, "connect")
        def _set_sqlite_pragma(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")     # SQLite 默认不启用外键
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

        logger.info("数据库：SQLite（WAL 模式）")
        return eng

    # PostgreSQL
    eng = create_engine(
        _url,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,      # 断线自动重连
        echo=settings.debug,
        future=True,
    )
    logger.info("数据库：PostgreSQL")
    return eng


engine = _build_engine()

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


def init_db() -> None:
    """首次运行时建表（SQLite 专用，PG 用 Alembic 迁移）

    SQLite 部署时没有 Alembic，直接按模型建表。
    """
    if not is_sqlite(_url):
        return

    # 确保所有模型都已导入，否则 metadata 里没有表
    import app.models.alert          # noqa: F401
    import app.models.attachment     # noqa: F401
    import app.models.auth           # noqa: F401
    import app.models.project        # noqa: F401
    import app.models.report         # noqa: F401
    import app.models.system         # noqa: F401

    Base.metadata.create_all(engine)
    logger.info("SQLite 表结构已就绪（%d 张表）", len(Base.metadata.tables))

    # 写入 Alembic 版本标记
    # 否则后续 alembic upgrade 会以为数据库是空的，重复建表
    from app.db.alembic_sync import ensure_alembic_stamped

    ensure_alembic_stamped(_url)


def init_seed_data() -> None:
    """SQLite 首次建库时灌入基础数据

    PG 用 Alembic 迁移灌种子，SQLite 需要单独做。
    """
    if not is_sqlite(_url):
        return

    from sqlalchemy import select

    from app.models.system import SysConfig, SysPermission, SysRole, SysRolePermission

    with SessionLocal() as db:
        # 已有数据就跳过
        if db.scalar(select(SysRole).limit(1)):
            return

        logger.info("初始化基础数据...")

        # 角色
        roles = [
            ("SYS_ADMIN", "系统管理员", True),
            ("PROJECT_OWNER", "项目总负责人", True),
            ("MAJOR_OWNER", "大项目负责人", True),
            ("COLLEGE_OWNER", "学院负责人", True),
            ("SUB_OWNER", "子项目负责人", True),
            ("REPORTER", "填报人", True),
            ("VIEWER", "只读用户", True),
        ]
        role_map = {}
        for code, name, is_system in roles:
            r = SysRole(code=code, name=name, is_system=is_system)
            db.add(r)
            db.flush()
            role_map[code] = r.id

        # 权限（与 0001_init.py 保持一致）
        perms = [
            ("dashboard", "我的督办", "MODULE"),
            ("dashboard:view", "查看督办", "PAGE"),
            ("project", "项目管理", "MODULE"),
            ("project:major", "大项目", "PAGE"),
            ("project:major:view", "查看大项目", "ACTION"),
            ("project:major:edit", "编辑大项目", "ACTION"),
            ("project:college", "学院分工", "PAGE"),
            ("project:college:edit", "编辑学院分工", "ACTION"),
            ("project:sub", "子项目", "PAGE"),
            ("project:sub:view", "查看子项目", "ACTION"),
            ("project:sub:edit", "编辑子项目", "ACTION"),
            ("project:check", "完整性检查", "ACTION"),
            ("progress", "进度管控", "MODULE"),
            ("progress:table", "进度总表", "PAGE"),
            ("progress:update", "更新进度", "ACTION"),
            ("progress:onbehalf", "代填进度", "ACTION"),
            ("progress:urge", "催办", "ACTION"),
            ("alert", "预警管理", "MODULE"),
            ("alert:list", "预警列表", "PAGE"),
            ("alert:claim", "认领预警", "ACTION"),
            ("alert:close", "关闭预警", "ACTION"),
            ("alert:transfer", "转交预警", "ACTION"),
            ("report", "报表管理", "MODULE"),
            ("report:fill", "填报报表", "ACTION"),
            ("report:void", "作废报表", "ACTION"),
            ("export", "数据导出", "MODULE"),
            ("export:ledger", "导出台账", "ACTION"),
            ("export:progress", "导出进度", "ACTION"),
            ("risk", "风险登记", "MODULE"),
            ("risk:list", "风险列表", "PAGE"),
            ("risk:edit", "编辑风险", "ACTION"),
            ("system", "系统管理", "MODULE"),
            ("system:user", "用户管理", "PAGE"),
            ("system:role", "角色权限", "PAGE"),
            ("system:org", "组织管理", "PAGE"),
            ("system:config", "参数配置", "PAGE"),
            ("system:holiday", "免填日历", "PAGE"),
            ("system:audit", "操作日志", "PAGE"),
        ]
        perm_map = {}
        for i, (code, name, ptype) in enumerate(perms):
            p = SysPermission(code=code, name=name, type=ptype, sort=i)
            db.add(p)
            db.flush()
            perm_map[code] = p.id

        # 管理员拥有全部权限
        admin_id = role_map["SYS_ADMIN"]
        for pid in perm_map.values():
            db.add(SysRolePermission(role_id=admin_id, permission_id=pid))

        # 默认参数
        configs = [
            ("alert.AL-01.deviation", "-10", "INT", "进度滞后阈值（%）"),
            ("alert.AL-01.days", "3", "INT", "滞后持续有效日"),
            ("alert.AL-02.deviation", "-25", "INT", "严重滞后阈值（%）"),
            ("alert.AL-03.days", "21", "INT", "停滞判定有效日"),
            ("alert.AL-05.days", "30", "INT", "临期天数"),
            ("alert.AL-05.progress", "80", "INT", "临期进度阈值（%）"),
            ("alert.AL-07.periods", "3", "INT", "连续未填周期数"),
            ("alert.AL-11.days", "60", "INT", "零进度有效日"),
            ("report.correct_days", "3", "INT", "报表自更正天数"),
            ("report.daily.enabled", "false", "BOOL", "是否启用日报"),
            ("aggregate.include_closed", "true", "BOOL", "汇总是否含已结项"),
        ]
        for k, v, vt, remark in configs:
            db.add(SysConfig(key=k, value=v, value_type=vt, remark=remark))

        # 认证方式（与 0002_auth 迁移保持一致）
        from app.models.auth import AuthProvider

        auth_kinds = [
            ("password", "账号密码", True, 0),
            ("oidc", "OIDC 单点登录", False, 1),
            ("oauth2", "OAuth2 登录", False, 2),
            ("cas", "CAS 单点登录", False, 3),
            ("saml2", "SAML 2.0 单点登录", False, 4),
            ("ldap", "LDAP / AD 域", False, 5),
            ("feishu", "飞书", False, 6),
            ("dingtalk", "钉钉", False, 7),
            ("wecom", "企业微信", False, 8),
        ]
        for kind, name, enabled, sort in auth_kinds:
            db.add(
                AuthProvider(
                    kind=kind, display_name=name, enabled=enabled, sort=sort
                )
            )

        db.commit()
        logger.info(
            "基础数据初始化完成：%d 角色 / %d 权限 / %d 参数 / %d 认证方式",
            len(roles), len(perms), len(configs), len(auth_kinds),
        )
