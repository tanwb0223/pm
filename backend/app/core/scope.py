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

from sqlalchemy import Select, false, or_, select
from sqlalchemy.orm import InstrumentedAttribute

from app.core.deps import CurrentUser
from app.core.exceptions import ForbiddenError

T = TypeVar("T")


def _has(model: Any, name: str) -> InstrumentedAttribute | None:
    attr = getattr(model, name, None)
    return attr if isinstance(attr, InstrumentedAttribute) else None


def apply_scope(stmt: Select, user: CurrentUser, model: Any) -> Select:
    """按当前用户的数据范围给查询加过滤条件

    ★ ProjMajor（大项目）需要特判：这张表本身**没有** major_id / college_id 列，
    它的「归属」有两层含义：
      - MAJOR 范围：大项目 id 就在授权列表里；
      - COLLEGE 范围：该学院参与了这个大项目（关系在 proj_major_college）。
    早期按通用逻辑走，major_col/college_col 都是 None，而 has_self 分支又只认
    owner_id，于是 MAJOR/COLLEGE 范围用户 `list_majors` 全部命中 where(false())，
    大项目下拉框恒为空（前端 AppLayout 的字典就靠这个接口）。
    """
    if user.has_all_scope():
        return stmt

    major_ids = user.major_ids()
    college_ids = user.college_ids()
    has_self = any(s.scope_type in ("SELF", "CUSTOM") for s in user.scopes)

    # ---------------- ProjMajor 特判（P0-2）----------------
    # 延迟导入：scope.py 被 models 层间接引用，模块级导入会形成循环。
    from app.models.project import ProjMajor, ProjMajorCollege

    if model is ProjMajor or getattr(model, "__tablename__", None) == "proj_major":
        conds: list[Any] = []
        if major_ids:
            conds.append(model.id.in_(major_ids))
        if college_ids:
            # 该学院参与的（可读）：学院参与 ≠ 可写，写权限仍由 can_access_major 控制
            conds.append(
                model.id.in_(
                    select(ProjMajorCollege.major_id).where(
                        ProjMajorCollege.college_id.in_(college_ids)
                    )
                )
            )
        if has_self:
            conds.append(model.owner_id == user.id)
        if not conds:
            return stmt.where(false())
        return stmt.where(or_(*conds))

    conds = []

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


# ---------------------------------------------------------------- 大项目
def can_access_major(user: CurrentUser, major: Any) -> bool:
    """大项目的**写权限**范围

    只认三种：全校范围 / 大项目在本人 MAJOR 授权内 / 本人是该大项目负责人。
    注意与 ProjectService.get_major 的读权限不同：读允许"本学院参与的项目"，
    写不允许 —— 学院参与某个大项目，不代表能改它的名称和负责人。
    """
    if user.has_all_scope():
        return True
    major_id = getattr(major, "id", None)
    if major_id is not None and major_id in user.major_ids():
        return True
    if getattr(major, "owner_id", None) == user.id:
        return True
    return False


def assert_major_access(user: CurrentUser, major: Any) -> None:
    """大项目编辑/删除：无权操作直接 403"""
    if not can_access_major(user, major):
        raise ForbiddenError("无权操作该项目")


def can_edit_major_college(
    user: CurrentUser, major: Any, college_id: int | None
) -> bool:
    """增删大项目的参与学院

    非全校范围时，只要"该项目在本人 MAJOR 范围内"或"操作的是本学院"即可：
    学院负责人能给自己学院挂到项目上，但不能替别的学院做决定。
    """
    if user.has_all_scope():
        return True
    if can_access_major(user, major):
        return True
    if college_id is not None and college_id in user.college_ids():
        return True
    return False


def can_create_major(user: CurrentUser, owner_id: int | None) -> bool:
    """新建大项目：非全校范围时，负责人必须是自己"""
    if user.has_all_scope():
        return True
    return owner_id == user.id


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
    - 本人是风险关联子项目的负责人（SELF 范围）

    最后一条与写权限 `_assert_risk_write_scope` 对齐：子项目负责人可以给
    自己负责的子项目登记风险，却因为列表/详情只认 risk.owner_id 而看不到
    自己登记的风险，属于「写得进、读不出」的口径断裂。
    """
    if user.has_all_scope():
        return True
    if getattr(risk, "major_id", None) in user.major_ids():
        return True
    if getattr(risk, "owner_id", None) == user.id:
        return True
    s = sub if sub is not None else getattr(risk, "sub", None)
    if s is not None:
        if getattr(s, "college_id", None) in user.college_ids():
            return True
        # SELF 范围：本人负责的子项目上的风险也可见
        if getattr(s, "owner_id", None) == user.id:
            return True
    return False


def assert_risk_access(user: CurrentUser, risk: Any, sub: Any = None) -> None:
    """风险详情与增删改：无权操作直接 403"""
    if not can_access_risk(user, risk, sub):
        raise ForbiddenError("无权访问该风险")
