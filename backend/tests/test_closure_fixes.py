"""冒烟测试 3：本轮闭环修复的回归锁定

对应 .workbuddy/fix 的修复清单与 doc/闭环缺口汇总-20261024.md。
目标不是覆盖率，而是把「曾经断掉、容易再断」的几处口径钉死：

1. 报表截止时间是北京时区语义（不是 UTC 直译），周报 12:00 / 月报 18:00
2. 权限矩阵按需求附录二对齐（学院负责人能建子项目/结项、总负责人不能作废）
3. 全新库的权限种子包含 report:grant（历史上漏过）
4. 数据范围：ProjMajor 走「本人负责 / 参与学院」两条路，不再是空集
5. ProgRecord.event_type 区分 FILL / URGE / REPORT，催办不污染「上次说明」
6. 站内消息：INNER 投递会落 SysMessage
7. 登录限流落库（SysUser.failed_login_count / locked_until 存在且可清零）
"""
from __future__ import annotations

from datetime import date, datetime, UTC

import pytest
from sqlalchemy import select

from app.core.permissions import ALL_PERMISSIONS, ROLE_PERMISSIONS


# ================================================================ 1 截止时间时区
def test_deadline_uses_beijing_timezone(db):
    """周报截止 = 北京次周一 12:00；月报 = 北京 18:00；日报 = 北京次日 00:00

    早期实现用 `tzinfo=UTC` 直译本地时刻，导致整体晚 8 小时
    （周报实际北京 20:00、月报次日 02:00），及时率边界全部偏移。
    """
    from app.services.calendar_service import SHANGHAI
    from app.services.report_service import ReportService

    svc = ReportService(db)

    weekly = svc.deadline_of("WEEKLY", date(2026, 10, 12))  # 周一
    local = weekly.astimezone(SHANGHAI)
    assert (local.hour, local.minute) == (12, 0)
    assert local.weekday() == 0  # 次周一

    daily = svc.deadline_of("DAILY", date(2026, 10, 20))
    dlocal = daily.astimezone(SHANGHAI)
    assert (dlocal.hour, dlocal.minute) == (0, 0)
    assert dlocal.date() == date(2026, 10, 21)

    monthly = svc.deadline_of("MONTHLY", date(2026, 9, 1))
    mlocal = monthly.astimezone(SHANGHAI)
    assert mlocal.hour == 18


# ================================================================ 2 权限矩阵
def test_permission_matrix_matches_requirements():
    """按需求 2.2 / 附录二核对关键格子

    - 学院负责人：能建/编辑本学院子项目（project:sub:edit）、能结项确认
      （project:sub:close）、能催办（progress:urge）、能开补填（report:grant）
    - 项目总负责人：**不能**作废报表（需求 3.4：作废只有系统管理员）
    - 子项目负责人：能认领也能关闭本人相关预警（3.3(4) 已认领可关闭）
    - 查看者：能看报表（需求 2.2「查看看板与报表」）
    """
    college = set(ROLE_PERMISSIONS["COLLEGE_OWNER"])
    assert "project:sub:edit" in college
    assert "project:sub:close" in college
    assert "progress:urge" in college
    assert "report:grant" in college

    project_owner = set(ROLE_PERMISSIONS["PROJECT_OWNER"])
    assert "report:void" not in project_owner
    assert "export:ledger" in project_owner

    sub_owner = set(ROLE_PERMISSIONS["SUB_OWNER"])
    assert {"alert:claim", "alert:close"} <= sub_owner

    viewer = set(ROLE_PERMISSIONS["VIEWER"])
    assert "report" in viewer
    assert "export:ledger" in viewer  # 需求 4.5：校领导可导出
    # 但填报人不应有导出权（附录二：填报人导出=—）
    assert "export:ledger" not in set(ROLE_PERMISSIONS["REPORTER"])


def test_all_permissions_have_metadata():
    """权限点全集与元数据一一对应（防止再次出现声明 39 项、种子 38 项）"""
    from app.core.permissions import PERMISSION_META, permission_rows

    assert set(ALL_PERMISSIONS) == set(PERMISSION_META)
    rows = permission_rows()
    assert len(rows) == len(ALL_PERMISSIONS)
    assert {r[0] for r in rows} == set(ALL_PERMISSIONS)
    # report:grant / project:sub:close 这两个历史上漏过
    codes = {r[0] for r in rows}
    assert "report:grant" in codes
    assert "project:sub:close" in codes


# ================================================================ 3 数据范围
def _user(all_scope=False, majors=(), colleges=(), uid=1, perms=()):
    from app.core.deps import CurrentUser, Scope

    scopes = []
    if all_scope:
        scopes.append(Scope(scope_type="ALL", scope_id=None))
    scopes += [Scope(scope_type="MAJOR", scope_id=m) for m in majors]
    scopes += [Scope(scope_type="COLLEGE", scope_id=c) for c in colleges]
    return CurrentUser(
        id=uid,
        staff_no="T001",
        name="测试",
        college_id=None,
        roles=[],
        permissions=set(perms),
        scopes=scopes,
    )


def test_apply_scope_major_model_by_owner_and_college(db):
    """大项目列表：MAJOR 范围按 id、COLLEGE 范围按参与学院，不能返回空集

    早期 apply_scope 对 ProjMajor 找不到 major_id/college_id 列，
    直接命中 where(false())，于是 MAJOR/COLLEGE 范围用户的大项目列表恒为空。
    """
    from app.core.scope import apply_scope
    from app.models.project import ProjMajor, ProjMajorCollege

    m1 = ProjMajor(name="项目一", type="其他", owner_id=1)
    m2 = ProjMajor(name="项目二", type="其他", owner_id=2)
    db.add_all([m1, m2])
    db.flush()
    db.add(ProjMajorCollege(major_id=m2.id, college_id=5))
    db.flush()

    # MAJOR 范围：只看 id 命中的那个
    stmt = apply_scope(select(ProjMajor), _user(majors=[m1.id]), ProjMajor)
    ids = {m.id for m in db.scalars(stmt)}
    assert ids == {m1.id}

    # COLLEGE 范围：看参与该学院的项目
    stmt = apply_scope(select(ProjMajor), _user(colleges=[5]), ProjMajor)
    ids = {m.id for m in db.scalars(stmt)}
    assert ids == {m2.id}

    # 全校范围：全部可见
    stmt = apply_scope(select(ProjMajor), _user(all_scope=True), ProjMajor)
    assert len({m.id for m in db.scalars(stmt)}) == 2


# ================================================================ 4 留痕事件类型
def test_prog_record_event_type_default_fill(db):
    """ProgRecord 默认 FILL；催办/报表同步要显式标 URGE/REPORT"""
    from app.models.project import ProgRecord, ProjMajor, ProjSub

    major = ProjMajor(name="M", type="其他")
    db.add(major)
    db.flush()
    sub = ProjSub(major_id=major.id, college_id=1, name="S")
    db.add(sub)
    db.flush()

    rec = ProgRecord(sub_id=sub.id, progress=10, operator_id=1)
    db.add(rec)
    db.flush()
    assert rec.event_type == "FILL"


# ================================================================ 5 站内消息
def test_inner_notify_creates_sys_message(db):
    """INNER 投递必须落一条 SysMessage，否则收件人永远读不到

    早期 deliver() 对 INNER 只把 notify_log 置 SUCCESS 就返回。
    """
    from app.models.alert import Alert
    from app.models.system import NotifyLog, SysMessage, SysUser
    from app.services.notify_service import NotifyService

    user = SysUser(staff_no="T900", name="收件人", status="ACTIVE")
    db.add(user)
    db.flush()
    alert = Alert(
        alert_type="AL-01",
        level="ORANGE",
        status="PENDING",
        trigger_at=datetime.now(UTC),
        dedup_key="x:AL-01",
    )
    db.add(alert)
    db.flush()

    row = NotifyLog(
        biz_type="ALERT",
        biz_id=alert.id,
        channel="INNER",
        receiver_id=user.id,
        status="SUCCESS",
    )
    db.add(row)
    db.flush()

    NotifyService(db).deliver(row.id)
    db.flush()

    msgs = list(db.scalars(select(SysMessage).where(SysMessage.receiver_id == user.id)))
    assert len(msgs) == 1
    assert msgs[0].biz_type == "ALERT"
    assert msgs[0].title
    assert msgs[0].body
    assert msgs[0].read_at is None


def test_message_mark_read_only_own(db):
    """只能标记自己的消息已读，越权必须 403"""
    from app.core.exceptions import ForbiddenError
    from app.models.system import SysUser
    from app.services.message_service import MessageService

    u1 = SysUser(staff_no="T901", name="甲", status="ACTIVE")
    u2 = SysUser(staff_no="T902", name="乙", status="ACTIVE")
    db.add_all([u1, u2])
    db.flush()

    svc = MessageService(db)
    msg = svc.push(u2.id, biz_type="URGE", biz_id=1, title="催办", body="请更新进度")
    db.flush()

    with pytest.raises(ForbiddenError):
        svc.mark_read(_user(uid=u1.id), msg.id)

    # 本人可以
    svc.mark_read(_user(uid=u2.id), msg.id)
    assert msg.read_at is not None


# ================================================================ 6 登录限流落库
def test_login_throttle_persists_on_user(db):
    """限流计数落库：多 worker / 重启后仍然有效，且可清零

    早期用进程内字典：2 个 worker 等效把阈值放大 2 倍，重启即清零，
    管理员也无法手动解锁。
    """
    from app.core import login_guard
    from app.models.system import SysUser

    user = SysUser(staff_no="T903", name="丙", status="ACTIVE")
    db.add(user)
    db.flush()

    # 记录失败若干次
    for _ in range(3):
        login_guard.record_failure(db, user)
    db.flush()
    assert user.failed_login_count == 3

    # 成功登录会清零
    login_guard.record_success(db, user)
    db.flush()
    assert not user.failed_login_count
    assert user.locked_until is None


# ================================================================ 7 报表放行
def test_grace_kind_backfill_and_correct(db):
    """补填放行(BACKFILL) 与 更正放行(CORRECT) 互相独立

    早期只有一张「补填放行」，超期更正用 project:sub:edit 冒充
    「学院负责人开权限」，学院负责人恰恰没这个权限点 —— 超期更正对所有人死路。
    """
    from app.models.project import ProjMajor, ProjSub
    from app.models.report import ReportGrace

    major = ProjMajor(name="M2", type="其他")
    db.add(major)
    db.flush()
    sub = ProjSub(major_id=major.id, college_id=1, name="S2")
    db.add(sub)
    db.flush()

    g1 = ReportGrace(
        sub_id=sub.id,
        type="WEEKLY",
        kind="BACKFILL",
        reason="补交",
        granted_by=1,
        expires_on=date.today(),
    )
    g2 = ReportGrace(
        sub_id=sub.id,
        type="WEEKLY",
        kind="CORRECT",
        reason="改错",
        granted_by=1,
        expires_on=date.today(),
    )
    db.add_all([g1, g2])
    db.flush()

    kinds = {
        g.kind
        for g in db.scalars(select(ReportGrace).where(ReportGrace.sub_id == sub.id))
    }
    assert kinds == {"BACKFILL", "CORRECT"}
