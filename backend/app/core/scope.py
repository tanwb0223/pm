"""数据范围过滤：所有列表查询必须经过这里

用法：
    stmt = select(ProjSub)
    stmt = apply_scope(stmt, user, ProjSub)
    rows = db.scalars(stmt).all()

模型需具备可识别的字段，按以下顺序尝试：
    1. major_id     （大项目范围）
    2. college_id   （学院范围）
    3. owner_id     （本人范围）
"""
from __future__ import annotations

from typing import Any, TypeVar

from sqlalchemy import Select, false, or_
from sqlalchemy.orm import InstrumentedAttribute

from app.core.deps import CurrentUser
from app.core.exceptions import ForbiddenError

T = TypeVar("T")


def _has(model: Any, name: str) -> InstrumentedAttribute | None:
    attr = getattr(model, name, None)
    return attr if isinstance(attr, InstrumentedAttribute) else None


def apply_scope(stmt: Select, user: CurrentUser, model: Any) -> Select:
    """按当前用户的数据范围给查询加过滤条件"""
    if user.has_all_scope():
        return stmt

    major_ids = user.major_ids()
    college_ids = user.college_ids()
    has_self = any(s.scope_type in ("SELF", "CUSTOM") for s in user.scopes)

    conds: list[Any] = []

    major_col = _has(model, "major_id")
    college_col = _has(model, "college_id")
    owner_col = _has(model, "owner_id")

    if major_ids and major_col is not None:
        conds.append(major_col.in_(major_ids))

    if college_ids and college_col is not None:
        conds.append(college_col.in_(college_ids))

    if has_self:
        if owner_col is not None:
            conds.append(owner_col == user.id)
        elif major_col is None and college_col is None:
            # 无归属字段的表，只能看自己创建的
            created_col = _has(model, "created_by")
            if created_col is not None:
                conds.append(created_col == user.id)

    if not conds:
        # 没有任何可匹配的范围：返回空集（安全默认）
        return stmt.where(false())

    return stmt.where(or_(*conds))


def can_access_sub(user: CurrentUser, sub: Any) -> bool:
    """单个子项目是否有权访问（用于详情页）"""
    if user.has_all_scope():
        return True
    if user.major_ids() and getattr(sub, "major_id", None) in user.major_ids():
        return True
    if user.college_ids() and getattr(sub, "college_id", None) in user.college_ids():
        return True
    if getattr(sub, "owner_id", None) == user.id:
        return True
    return False


# ---------------------------------------------------------------- 单条对象校验
def assert_sub_access(user: CurrentUser, sub: Any) -> None:
    """子项目详情/留痕/视图：无权访问直接 403"""
    if not can_access_sub(user, sub):
        raise ForbiddenError("无权访问该子项目")


def can_access_alert(user: CurrentUser, alert: Any) -> bool:
    """预警是否在本人数据范围内

    命中任一即放行：
    - 全校范围
    - 预警归属的大项目在本人 MAJOR 范围内
    - 预警归属的学院在本人 COLLEGE 范围内
    - 本人已认领
    - 预警关联子项目的负责人是本人（SELF 范围）
    """
    if user.has_all_scope():
        return True
    major_id = getattr(alert, "major_id", None)
    if major_id is not None and major_id in user.major_ids():
        return True
    college_id = getattr(alert, "college_id", None)
    if college_id is not None and college_id in user.college_ids():
        return True
    if getattr(alert, "claimed_by", None) == user.id:
        return True
    sub = getattr(alert, "sub", None)
    if sub is not None and getattr(sub, "owner_id", None) == user.id:
        return True
    return False


def assert_alert_access(user: CurrentUser, alert: Any) -> None:
    """预警详情与处置：无权操作直接 403"""
    if not can_access_alert(user, alert):
        raise ForbiddenError("无权访问该预警")


def assert_report_access(user: CurrentUser, report: Any) -> None:
    """报表详情：按所属子项目的数据范围校验"""
    sub = getattr(report, "sub", None)
    if sub is not None:
        assert_sub_access(user, sub)
        return
    if not user.has_all_scope():
        raise ForbiddenError("无权访问该报表")


# ---------------------------------------------------------------- 新建目标范围
def can_write_sub_target(user: CurrentUser, major_id: int | None, college_id: int | None) -> bool:
    """新建子项目时的目标范围校验（编辑/删除用 assert_sub_access）

    非全校范围时，目标大项目或目标学院至少有一个在本人授权范围内。
    """
    if user.has_all_scope():
        return True
    if major_id is not None and major_id in user.major_ids():
        return True
    if college_id is not None and college_id in user.college_ids():
        return True
    return False


def can_access_risk(user: CurrentUser, risk: Any, sub: Any = None) -> bool:
    """风险是否在本人数据范围内

    命中任一即放行：
    - 全校范围
    - 风险所属大项目在本人 MAJOR 范围内
    - 风险关联子项目所在学院在本人 COLLEGE 范围内
    - 本人是风险责任人（SELF 范围）
    """
    if user.has_all_scope():
        return True
    if getattr(risk, "major_id", None) in user.major_ids():
        return True
    if getattr(risk, "owner_id", None) == user.id:
        return True
    s = sub if sub is not None else getattr(risk, "sub", None)
    if s is not None and getattr(s, "college_id", None) in user.college_ids():
        return True
    return False


def assert_risk_access(user: CurrentUser, risk: Any, sub: Any = None) -> None:
    """风险详情与增删改：无权操作直接 403"""
    if not can_access_risk(user, risk, sub):
        raise ForbiddenError("无权访问该风险")
