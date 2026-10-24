"""冒烟测试 2：数据范围校验

对应问题：大项目 4 个写接口只有功能权限、没有数据范围，
MAJOR_OWNER / PROJECT_OWNER 能改任意大项目。这里锁住判定口径。
"""
from __future__ import annotations

import pytest

from app.core.deps import CurrentUser, Scope
from app.core.exceptions import ForbiddenError
from app.core.scope import (
    assert_major_access,
    can_access_major,
    can_create_major,
    can_edit_major_college,
)


class FakeMajor:
    def __init__(self, major_id: int = 7, owner_id: int | None = None) -> None:
        self.id = major_id
        self.owner_id = owner_id


def _user(all_scope=False, majors=(), colleges=(), uid=1, roles=(), perms=()) -> CurrentUser:
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
        roles=list(roles),
        permissions=set(perms),
        scopes=scopes,
    )


def test_all_scope_can_edit_anything():
    assert can_access_major(_user(all_scope=True), FakeMajor(owner_id=99))


def test_major_scope_only_own_major():
    u = _user(majors=[7])
    assert can_access_major(u, FakeMajor(7))
    assert not can_access_major(u, FakeMajor(8))


def test_college_scope_cannot_edit_major():
    """学院参与项目 ≠ 能改项目：写权限不能沿用读权限"""
    assert not can_access_major(_user(colleges=[3]), FakeMajor(7))


def test_owner_can_edit_own_major():
    assert can_access_major(_user(majors=[99], uid=1), FakeMajor(7, owner_id=1))


def test_assert_major_access_raises():
    with pytest.raises(ForbiddenError):
        assert_major_access(_user(majors=[8]), FakeMajor(7))


def test_college_owner_can_only_touch_own_college():
    major, u = FakeMajor(7), _user(colleges=[3])
    assert can_edit_major_college(u, major, 3)
    assert not can_edit_major_college(u, major, 4)


def test_major_scope_can_manage_all_colleges_of_that_major():
    assert can_edit_major_college(_user(majors=[7]), FakeMajor(7), 4)


def test_create_major_self_only():
    u = _user(majors=[7], uid=1)
    assert can_create_major(u, 1)
    assert not can_create_major(u, 2)
    assert can_create_major(_user(all_scope=True), 2)


# ---------------------------------------------------------------- 服务层接线
def test_service_update_major_checks_scope(db):
    """光有判定函数不够，服务层必须真的调用它"""
    from app.models.project import ProjMajor
    from app.schemas.project import MajorUpdateIn
    from app.services.project_service import ProjectService

    major = ProjMajor(name="别人的项目", type="其他", status="ACTIVE")
    db.add(major)
    db.commit()
    db.refresh(major)

    svc = ProjectService(db)
    # 授权范围里没有这个项目 → 403
    with pytest.raises(ForbiddenError):
        svc.update_major(major.id, MajorUpdateIn(name="改名"), _user(majors=[999]))
    # 全校范围 → 放行
    svc.update_major(major.id, MajorUpdateIn(name="改名"), _user(all_scope=True))
    db.commit()
    db.refresh(major)
    assert major.name == "改名"


def test_service_delete_major_checks_scope(db):
    from app.models.project import ProjMajor
    from app.services.project_service import ProjectService

    major = ProjMajor(name="待删项目", type="其他", status="ACTIVE")
    db.add(major)
    db.commit()
    db.refresh(major)

    with pytest.raises(ForbiddenError):
        ProjectService(db).delete_major(major.id, _user(majors=[999]))
    ProjectService(db).delete_major(major.id, _user(all_scope=True))
    db.commit()
    db.refresh(major)
    assert major.deleted_at is not None
