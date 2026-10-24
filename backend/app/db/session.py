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

    # 修复历史漂移：create_all 不会改旧表，早期"模型改了库没改"的列约束
    # （如 alert_action.operator_id）在这里按已知清单重建修复
    from app.db.alembic_sync import repair_sqlite_schema

    repair_sqlite_schema(_url)

    # 补齐缺失索引：存量库的表是早期 create_all 建的，当时模型没声明 Index，
    # 迁移里的 40 多条 create_index 从未执行过。这里做一次幂等比对补齐。
    from app.db.alembic_sync import ensure_indexes

    ensure_indexes(_url)

    # 写入 Alembic 版本标记
    # 否则后续 alembic upgrade 会以为数据库是空的，重复建表
    from app.db.alembic_sync import ensure_alembic_stamped

    ensure_alembic_stamped(_url)

    # 版本落后于 head 时执行增量迁移（SQLite 的升级通道）
    from app.db.alembic_sync import run_alembic_upgrade

    run_alembic_upgrade(_url)


# ================================================================ 角色权限基线
# 权威定义在 app/core/permissions.py 的 ROLE_PERMISSIONS。
# 那里按「看得到 → 做得了 → 管得住」逐角色叠加，避免出现
# "角色叫填报人却没有填报权限" 这类问题。
def _ensure_permissions(db) -> None:
    """补齐缺失的权限点（幂等）

    背景：ALL_PERMISSIONS 声明 40 项，但历史上 session.py 的种子清单只写了
    38 项（漏 report:grant），全新部署后除超管外没人能开逾期补填。
    现在权限清单从 app/core/permissions.py 统一生成，这里对存量库做补插。
    """
    from sqlalchemy import select

    from app.core.permissions import permission_rows
    from app.models.system import SysPermission

    existing = set(db.scalars(select(SysPermission.code)))
    added = 0
    for code, name, ptype, sort in permission_rows():
        if code in existing:
            continue
        db.add(SysPermission(code=code, name=name, type=ptype, sort=sort))
        added += 1
    if added:
        db.commit()
        logger.info("已补齐缺失权限点：%d 条", added)


def _ensure_role_permissions(db) -> None:
    """按角色矩阵补齐权限（幂等，只增不减）

    背景：早期版本给 6 个非管理员角色灌的是同一套 14 项「只读」权限，
    结果 REPORTER（填报人）连 report:fill 都没有 —— 角色名叫"填报人"
    却看不到「报表填报」入口，只有 SYS_ADMIN 能用。
    现在改为按 app/core/permissions.py 的角色矩阵逐角色补齐。

    只增不减：已授予的权限不动（避免把管理员在界面上手工加的授权删掉）。
    """
    from sqlalchemy import select

    from app.core.permissions import ROLE_PERMISSIONS
    from app.models.system import SysPermission, SysRole, SysRolePermission

    role_codes = tuple(ROLE_PERMISSIONS.keys())
    roles = {
        r.code: r for r in db.scalars(select(SysRole).where(SysRole.code.in_(role_codes)))
    }
    if not roles:
        return

    # 一次性取出所有需要授予的权限点
    needed_codes = {c for codes in ROLE_PERMISSIONS.values() for c in codes}
    perm_by_code = {
        p.code: p
        for p in db.scalars(
            select(SysPermission).where(SysPermission.code.in_(tuple(needed_codes)))
        )
    }

    added = 0
    for code, role in roles.items():
        wanted = ROLE_PERMISSIONS.get(code, ())
        existing = set(
            db.scalars(
                select(SysRolePermission.permission_id).where(
                    SysRolePermission.role_id == role.id
                )
            )
        )
        for perm_code in wanted:
            perm = perm_by_code.get(perm_code)
            if perm is not None and perm.id not in existing:
                db.add(SysRolePermission(role_id=role.id, permission_id=perm.id))
                added += 1

    if added:
        db.commit()
        logger.info("已补齐角色权限：%d 条", added)


# ================================================================ 系统参数基线
# 与 alembic/versions/0001_init.py 的 configs 保持逐条一致（33 条）。
# 早期 SQLite 种子只写了 11 条，缺的 22 条里包含全部预警开关
# （alert.AL-0x.enabled）和处置/报表/通知时限，导致 SQLite 部署下
# 预警开关改了也不生效、报表时限取不到值。
_DEFAULT_CONFIGS: list[tuple[str, str, str, str]] = [
    # 预警阈值
    ("alert.AL-01.enabled", "true", "BOOLEAN", "进度滞后预警开关"),
    ("alert.AL-01.deviation", "-10", "INT", "滞后阈值（百分点）"),
    ("alert.AL-01.days", "3", "INT", "滞后持续天数"),
    ("alert.AL-02.enabled", "true", "BOOLEAN", "严重滞后预警开关"),
    ("alert.AL-02.deviation", "-25", "INT", "严重滞后阈值"),
    ("alert.AL-03.enabled", "true", "BOOLEAN", "进度停滞预警开关"),
    ("alert.AL-03.days", "21", "INT", "停滞判定天数"),
    ("alert.AL-05.enabled", "true", "BOOLEAN", "项目临期预警开关"),
    ("alert.AL-05.days", "30", "INT", "临期判定天数"),
    ("alert.AL-05.progress", "80", "INT", "临期进度阈值"),
    ("alert.AL-07.enabled", "true", "BOOLEAN", "连续未填预警开关"),
    ("alert.AL-07.periods", "3", "INT", "连续未填周期数"),
    ("alert.AL-08.enabled", "true", "BOOLEAN", "风险超期预警开关"),
    ("alert.AL-08.days", "7", "INT", "风险闭环默认天数"),
    ("alert.AL-11.enabled", "true", "BOOLEAN", "长期零进度预警开关"),
    ("alert.AL-11.days", "60", "INT", "零进度判定天数"),
    # 其余规则的开关（需求 3.3(1)：除 AL-09/AL-10 外都可关）
    ("alert.AL-04.enabled", "true", "BOOLEAN", "进度回调提示开关"),
    ("alert.AL-06.enabled", "true", "BOOLEAN", "单次报表逾期预警开关"),
    ("alert.AL-09.enabled", "true", "BOOLEAN", "责任空缺预警开关（不可关闭，仅占位）"),
    ("alert.AL-10.enabled", "true", "BOOLEAN", "计划缺失预警开关（不可关闭，仅占位）"),
    # 处置时限
    ("alert.red.deadline_days", "3", "INT", "红色预警处置天数"),
    ("alert.orange.deadline_days", "7", "INT", "橙色预警处置天数"),
    # 报表时限
    ("report.daily.enabled", "false", "BOOLEAN", "日报全局开关（项目可单独覆盖）"),
    ("report.weekly.deadline_weekday", "1", "INT", "周报截止星期（1=周一）"),
    ("report.weekly.deadline_hour", "12", "INT", "周报截止小时"),
    ("report.monthly.deadline_workday", "3", "INT", "月报截止第几个工作日"),
    ("report.monthly.deadline_hour", "18", "INT", "月报截止小时"),
    ("report.correct_days", "3", "INT", "填报人自行更正天数（提交后 N 天内）"),
    ("report.backfill_days", "3", "INT", "逾期后允许自行补填的天数"),
    ("report.close_need_monthly", "true", "BOOLEAN", "结项是否要求至少 1 份月报"),
    ("report.close_block_open_alert", "true", "BOOLEAN", "结项是否要求无未关闭预警"),
    # 汇总
    ("aggregate.school.mode", "EQUAL", "STRING", "全校进度汇总方式：EQUAL 等权"),
    ("aggregate.include_closed", "true", "BOOLEAN", "已结项子项目是否按100%计入"),
    # 通知
    ("notify.digest.hour", "8", "INT", "每日摘要小时"),
    ("notify.digest.minute", "30", "INT", "每日摘要分钟"),
    ("notify.sms.enabled", "false", "BOOLEAN", "短信开关（仅红色预警）"),
    ("notify.email.enabled", "true", "BOOLEAN", "邮件开关"),
    ("notify.orange.email", "true", "BOOLEAN", "橙色预警是否也发邮件"),
    # 系统
    ("system.audit_retention_years", "2", "INT", "审计日志保留年数"),
    ("system.session_idle_minutes", "30", "INT", "会话空闲超时分钟（预留，未接入）"),
    ("system.snapshot_retention_years", "3", "INT", "快照保留年数"),
]


def _ensure_configs(db) -> None:
    """补齐缺失的系统参数（幂等）

    背景：早期 SQLite 种子只灌了 11 条参数，缺 22 条（含全部预警开关），
    导致 SQLite 部署下改预警开关不生效。这里对已有库按基线补齐，
    已存在的参数一律不动（不覆盖用户改过的值）。
    """
    from sqlalchemy import select

    from app.models.system import SysConfig

    existing = set(db.scalars(select(SysConfig.key)))
    added = 0
    for key, value, value_type, remark in _DEFAULT_CONFIGS:
        if key not in existing:
            db.add(
                SysConfig(key=key, value=value, value_type=value_type, remark=remark)
            )
            added += 1

    if added:
        db.commit()
        logger.info("已补齐缺失的系统参数：%d 条", added)


def init_seed_data() -> None:
    """SQLite 首次建库时灌入基础数据

    PG 用 Alembic 迁移灌种子，SQLite 需要单独做。
    """
    if not is_sqlite(_url):
        return

    from sqlalchemy import select

    from app.models.system import SysConfig, SysPermission, SysRole, SysRolePermission

    with SessionLocal() as db:
        # 已有数据就跳过（但角色权限可能因历史版本缺失，单独兜底补齐）
        if db.scalar(select(SysRole).limit(1)):
            _ensure_permissions(db)
            _ensure_role_permissions(db)
            _ensure_configs(db)
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
        # 权限：从 app/core/permissions.py 的 ALL_PERMISSIONS 生成，不再手写清单。
        # 历史上手写清单漏了 report:grant（声明 39 项 / 种子 38 项），
        # 导致全新部署除超管外没人能开逾期补填。
        from app.core.permissions import ROLE_PERMISSIONS, permission_rows

        perms = permission_rows()
        perm_map = {}
        for code, name, ptype, sort in perms:
            p = SysPermission(code=code, name=name, type=ptype, sort=sort)
            db.add(p)
            db.flush()
            perm_map[code] = p.id

        # 各角色按矩阵授权（权威定义见 app/core/permissions.py）
        from app.core.permissions import ROLE_PERMISSIONS

        for role_code, role_id in role_map.items():
            for pc in ROLE_PERMISSIONS.get(role_code, ()):
                pid = perm_map.get(pc)
                if pid is not None:
                    db.add(SysRolePermission(role_id=role_id, permission_id=pid))

        # 默认参数（必须与 alembic/versions/0001_init.py 的 configs 完全一致，
        # 否则 SQLite 部署会缺参数：预警开关、处置时限、报表时限、通知开关全都不生效）
        configs = _DEFAULT_CONFIGS
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
