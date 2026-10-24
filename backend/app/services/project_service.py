"""项目台账服务：大项目、学院分工、子项目、完整性检查"""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessError, ForbiddenError, NotFoundError
from app.core.scope import apply_scope, assert_major_access, can_edit_major_college
from app.db.dialect import ilike
from app.models.change_log import ProjChangeLog
from app.models.project import ProjMajor, ProjMajorCollege, ProjSub
from app.models.system import SysCollege, SysUser
from app.schemas.project import (
    CollegeIn,
    CompletenessOut,
    IncompleteItem,
    MajorIn,
    MajorUpdateIn,
    SubIn,
    SubUpdateIn,
)
from app.services.config_service import ConfigService

# 所有子项目合法的状态迁移
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "NOT_STARTED": {"IN_PROGRESS", "ABORTED"},
    "IN_PROGRESS": {"ABORTED", "CLOSED"},
    "ABORTED": {"IN_PROGRESS"},
    "CLOSED": set(),
}

# 需要写变更留痕的关键字段（需求 6.3 第 20 项）
# 只记录「会影响考核/汇总口径」的字段：计划时间、权重、负责人、状态。
# 名称/备注等描述性字段改错影响小，留在 audit_log 即可。
_TRACKED_CHANGE_FIELDS: tuple[str, ...] = (
    "plan_start",
    "plan_end",
    "weight",
    "owner_id",
    "status",
)


def _change_value(v: Any) -> str | None:
    """变更值统一转字符串（日期用 ISO、Decimal 去掉多余尾零）"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
        # 9.00 → 9，避免前端展示 "9.00 → 10.00" 的噪声
        return format(v.normalize(), "f")
    return str(v)


class ProjectService:
    def __init__(self, db: Session):
        self.db = db

    # ============================================================ 学院
    def list_colleges(self) -> list[SysCollege]:
        return list(self.db.scalars(select(SysCollege).order_by(SysCollege.sort, SysCollege.id)))

    def create_college(self, body: CollegeIn) -> SysCollege:
        if body.code:
            exists = self.db.scalar(select(SysCollege).where(SysCollege.code == body.code))
            if exists:
                raise BusinessError(f"学院编码已存在：{body.code}")
        if body.parent_id is not None:
            self._check_parent_college(body.parent_id, None)
        c = SysCollege(**body.model_dump())
        self.db.add(c)
        self.db.flush()
        return c

    def update_college(self, college_id: int, body: CollegeIn) -> SysCollege:
        c = self.db.get(SysCollege, college_id)
        if c is None:
            raise NotFoundError("学院不存在")
        if body.code and body.code != c.code:
            exists = self.db.scalar(
                select(SysCollege).where(
                    SysCollege.code == body.code, SysCollege.id != college_id
                )
            )
            if exists:
                raise BusinessError(f"学院编码已存在：{body.code}")
        if body.parent_id is not None:
            self._check_parent_college(body.parent_id, college_id)
        for k, v in body.model_dump().items():
            setattr(c, k, v)
        self.db.flush()
        return c

    def _check_parent_college(self, parent_id: int, self_id: int | None) -> None:
        """上级组织校验：必须存在、不能是自己、不能形成环"""
        if self_id is not None and parent_id == self_id:
            raise BusinessError("上级组织不能是自己")
        seen: set[int] = set()
        cur: int | None = parent_id
        while cur is not None:
            if cur in seen:
                raise BusinessError("上级组织存在循环引用")
            seen.add(cur)
            node = self.db.get(SysCollege, cur)
            if node is None:
                raise BusinessError("上级组织不存在")
            if self_id is not None and node.id == self_id:
                raise BusinessError("上级组织不能是自己的下级")
            cur = node.parent_id

    def delete_college(self, college_id: int) -> None:
        c = self.db.get(SysCollege, college_id)
        if c is None:
            raise NotFoundError("学院不存在")

        # 1) 仍被大项目引用
        used = self.db.scalar(
            select(func.count()).select_from(ProjMajorCollege).where(
                ProjMajorCollege.college_id == college_id
            )
        )
        if used:
            raise BusinessError("该学院已有参与项目，不能删除")

        # 2) 仍有子项目归属（正常流程下先有参与项目，这里兜底）
        subs = self.db.scalar(
            select(func.count()).select_from(ProjSub).where(ProjSub.college_id == college_id)
        )
        if subs:
            raise BusinessError("该学院下还有子项目，不能删除")

        # 3) 仍有用户归属
        users = self.db.scalar(
            select(func.count()).select_from(SysUser).where(SysUser.college_id == college_id)
        )
        if users:
            raise BusinessError(f"该学院下还有 {users} 名用户，请先调整其所属学院")

        # 4) 仍有下级组织
        children = self.db.scalar(
            select(func.count()).select_from(SysCollege).where(
                SysCollege.parent_id == college_id
            )
        )
        if children:
            raise BusinessError("该学院下还有下级组织，请先删除或调整下级组织")

        self.db.delete(c)
        self.db.flush()

    # ============================================================ 大项目
    def _major_base_query(self) -> Select:
        return select(ProjMajor).where(ProjMajor.deleted_at.is_(None))

    def list_majors(
        self,
        user: Any,
        *,
        keyword: str | None = None,
        type_: str | None = None,
        status: str | None = None,
        owner_id: int | None = None,
        college_id: int | None = None,
        created_from: date | None = None,
        created_to: date | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[ProjMajor], int]:
        stmt = self._major_base_query()
        stmt = apply_scope(stmt, user, ProjMajor)

        if keyword:
            stmt = stmt.where(ilike(ProjMajor.name, keyword))
        if type_:
            stmt = stmt.where(ProjMajor.type == type_)
        if status:
            stmt = stmt.where(ProjMajor.status == status)
        if owner_id:
            stmt = stmt.where(ProjMajor.owner_id == owner_id)
        if college_id:
            stmt = stmt.where(
                ProjMajor.id.in_(
                    select(ProjMajorCollege.major_id).where(
                        ProjMajorCollege.college_id == college_id
                    )
                )
            )
        # 立项时间区间筛选（P1-13）：前端台账页按「立项年份」筛项目。
        # created_at 是 UTC datetime，用半开区间 [from 00:00, to+1day 00:00) 比较，
        # 避免把 to 当天创建的项目漏掉（按 date 直接 <= 会漏时分秒）。
        if created_from:
            stmt = stmt.where(
                ProjMajor.created_at >= datetime.combine(created_from, time.min, tzinfo=UTC)
            )
        if created_to:
            stmt = stmt.where(
                ProjMajor.created_at
                < datetime.combine(created_to + timedelta(days=1), time.min, tzinfo=UTC)
            )

        total = self.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = list(
            self.db.scalars(
                stmt.order_by(ProjMajor.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return rows, total

    def get_major(self, major_id: int, user: Any | None = None) -> ProjMajor:
        m = self.db.get(ProjMajor, major_id)
        if m is None or m.deleted_at is not None:
            raise NotFoundError("大项目不存在")
        if user is not None and not user.has_all_scope():
            # 读权限口径 = 写权限口径（can_access_major）+ 「本学院参与」
            # 早期这里自写一套判定，只认 user.major_ids() 与参与学院，
            # **不认 owner_id**：SELF 范围的大项目负责人列表能看到自己的项目
            # （apply_scope 的 has_self 分支用了 owner_id），点详情却 403。
            # 读权限不应比写权限更严，这里补上 owner_id 分支。
            if self._can_read_major(m, user):
                return m
            raise ForbiddenError("无权访问该项目")
        return m

    def _can_read_major(self, m: ProjMajor, user: Any) -> bool:
        """大项目读权限：MAJOR 授权 / 本人是负责人 / 本学院参与 / 全校范围"""
        from app.core.scope import can_access_major

        if can_access_major(user, m):
            return True
        college_ids = user.college_ids()
        if not college_ids:
            return False
        in_college = self.db.scalar(
            select(func.count()).select_from(ProjMajorCollege).where(
                ProjMajorCollege.major_id == m.id,
                ProjMajorCollege.college_id.in_(college_ids),
            )
        )
        return bool(in_college)

    def create_major(self, body: MajorIn, operator: Any) -> ProjMajor:
        if body.owner_id:
            if self.db.get(SysUser, body.owner_id) is None:
                raise BusinessError("总负责人不存在")
        # 数据范围：非全校范围时只能给自己建项目
        # （否则拿到 project:major:edit 的人可以替任意人立项）
        if not operator.has_all_scope():
            if body.owner_id is not None and body.owner_id != operator.id:
                raise ForbiddenError("无权为其他人新建大项目")
            if body.owner_id is None:
                body = body.model_copy(update={"owner_id": operator.id})
        m = ProjMajor(**body.model_dump())
        # 立项时锁定原始计划（第 38 项）
        m.original_start = body.original_start
        m.original_end = body.original_end
        self.db.add(m)
        self.db.flush()
        return m

    def update_major(self, major_id: int, body: MajorUpdateIn, operator: Any) -> ProjMajor:
        m = self.db.get(ProjMajor, major_id)
        if m is None or m.deleted_at is not None:
            raise NotFoundError("大项目不存在")
        # 数据范围：只能改自己授权范围内的大项目
        assert_major_access(operator, m)
        data = body.model_dump(exclude_unset=True)
        # change_reason 只用于写留痕，不是业务字段（P1-9）
        change_reason = data.pop("change_reason", None)

        # 原始计划不允许通过常规编辑修改（第 38 项：变更走变更登记）
        data.pop("original_start", None)
        data.pop("original_end", None)

        # 已结项的大项目不允许再改业务字段（含改名、改负责人），避免结项后数据漂移。
        # 注意：status 本身要放行，否则结项这个动作自己会被拦住。
        if m.status == "CLOSED":
            blocked = [k for k in data if k != "status"]
            if blocked:
                raise BusinessError("大项目已结项，不能再修改")

        # 结项前校验：必须先完成或中止所有子项目，否则结项后仍有在跑的任务
        if data.get("status") == "CLOSED" and m.status != "CLOSED":
            unfinished = self.db.scalar(
                select(func.count()).select_from(ProjSub).where(
                    ProjSub.major_id == major_id,
                    ProjSub.deleted_at.is_(None),
                    ProjSub.status.notin_(("CLOSED", "ABORTED")),
                )
            )
            if unfinished:
                raise BusinessError(
                    f"还有 {unfinished} 个子项目未结项，请先完成或中止后再结项"
                )

        # 变更留痕（P1-9）：先记旧值，再 setattr，逐字段落 ProjChangeLog。
        # 只记关键字段；值未变化的不记（避免前端重复提交产生噪声流水）。
        for field in _TRACKED_CHANGE_FIELDS:
            if field not in data:
                continue
            old = getattr(m, field, None)
            new = data[field]
            if _change_value(old) == _change_value(new):
                continue
            self._write_change_log(
                target_type="MAJOR",
                target_id=major_id,
                field=field,
                old=old,
                new=new,
                reason=change_reason,
                operator=operator,
            )

        for k, v in data.items():
            setattr(m, k, v)

        # 结项时清理上一次的异议字段：否则重新结项后，上一轮「异议退回」的
        # 时间/原因还挂在项目上，前端会误显示「本项目曾被异议退回」。
        if data.get("status") == "CLOSED":
            m.close_objection_at = None
            m.close_objection_by = None
            m.close_objection_reason = None

        self.db.flush()
        return m

    def raise_objection(self, major_id: int, reason: str, operator: Any) -> ProjMajor:
        """大项目结项异议（需求 6.3 第 27 项：结项后 7 天内可退回）

        为什么用 updated_at 近似「结项时间」：
        `proj_major` 没有单独的 closed_at 列（迁移 0008 未加），而结项动作
        本身就会刷新 updated_at（onupdate=func.now()），因此「updated_at 在
        7 天内」在正常流程下等价于「最近一次状态变更在 7 天内」。
        更严格的做法是补 closed_at 字段，但会牵动 A 组的迁移编号，暂不引入。

        口径：只有 CLOSED 状态可提异议；退回后状态回 ACTIVE，并记录异议人/时间/原因。
        """
        from app.core.scope import assert_major_access

        m = self.db.get(ProjMajor, major_id)
        if m is None or m.deleted_at is not None:
            raise NotFoundError("大项目不存在")
        assert_major_access(operator, m)
        if m.status != "CLOSED":
            raise BusinessError("只有已结项的大项目才能提出结项异议")
        # 7 天窗口：updated_at 为最近一次修改时间（含结项那次）
        closed_recently = m.updated_at is not None and (
            datetime.now(UTC) - m.updated_at <= timedelta(days=7)
        )
        if not closed_recently:
            raise BusinessError("结项已超过 7 天，不能再提出异议")
        if not (reason or "").strip():
            raise BusinessError("请填写异议原因")

        m.status = "ACTIVE"
        m.close_objection_at = datetime.now(UTC)
        m.close_objection_by = operator.id
        m.close_objection_reason = reason.strip()[:500]
        self._write_change_log(
            target_type="MAJOR",
            target_id=major_id,
            field="status",
            old="CLOSED",
            new="ACTIVE",
            reason=f"结项异议退回：{reason.strip()[:200]}",
            operator=operator,
        )
        self.db.flush()
        return m

    def list_changes(
        self, target_type: str, target_id: int, limit: int = 50
    ) -> list[ProjChangeLog]:
        """对象变更历史（前端详情页「变更记录」抽屉）"""
        return list(
            self.db.scalars(
                select(ProjChangeLog)
                .where(
                    ProjChangeLog.target_type == target_type,
                    ProjChangeLog.target_id == target_id,
                )
                .order_by(ProjChangeLog.created_at.desc(), ProjChangeLog.id.desc())
                .limit(limit)
            )
        )

    def _write_change_log(
        self,
        *,
        target_type: str,
        target_id: int,
        field: str,
        old: Any,
        new: Any,
        reason: str | None,
        operator: Any,
    ) -> None:
        """写一条字段级变更流水（P1-9）"""
        self.db.add(
            ProjChangeLog(
                target_type=target_type,
                target_id=target_id,
                field=field,
                old_value=_change_value(old),
                new_value=_change_value(new),
                reason=(reason or None),
                operator_id=getattr(operator, "id", None),
            )
        )

    def delete_major(self, major_id: int, operator: Any) -> None:
        m = self.db.get(ProjMajor, major_id)
        if m is None or m.deleted_at is not None:
            raise NotFoundError("大项目不存在")
        # 数据范围：只能删自己授权范围内的大项目
        assert_major_access(operator, m)
        sub_count = self.db.scalar(
            select(func.count()).select_from(ProjSub).where(
                ProjSub.major_id == major_id, ProjSub.deleted_at.is_(None)
            )
        )
        if sub_count:
            raise BusinessError("项目下还有子项目，请先删除子项目")
        m.deleted_at = datetime.now(UTC)
        self.db.flush()

    # ============================================================ 学院分工
    def list_major_colleges(self, major_id: int) -> list[dict]:
        rows = self.db.execute(
            select(ProjMajorCollege, SysCollege)
            .join(SysCollege, SysCollege.id == ProjMajorCollege.college_id)
            .where(ProjMajorCollege.major_id == major_id)
            .order_by(SysCollege.sort, SysCollege.id)
        ).all()

        out = []
        for mc, col in rows:
            owner = self.db.get(SysUser, mc.college_owner_id) if mc.college_owner_id else None
            sub_count = self.db.scalar(
                select(func.count()).select_from(ProjSub).where(
                    ProjSub.major_id == major_id,
                    ProjSub.college_id == col.id,
                    ProjSub.deleted_at.is_(None),
                )
            ) or 0
            out.append(
                {
                    "college_id": col.id,
                    "college_name": col.name,
                    "college_owner_id": mc.college_owner_id,
                    "college_owner_name": owner.name if owner else None,
                    "sub_count": sub_count,
                }
            )
        return out

    def add_major_college(self, major_id: int, body, operator: Any) -> None:
        major = self.get_major(major_id)
        # 数据范围：项目在我范围内，或操作的是本学院
        if not can_edit_major_college(operator, major, body.college_id):
            raise ForbiddenError("无权修改该项目的参与学院")
        if self.db.get(SysCollege, body.college_id) is None:
            raise BusinessError("学院不存在")
        if body.college_owner_id and self.db.get(SysUser, body.college_owner_id) is None:
            raise BusinessError("学院负责人不存在")

        exists = self.db.get(ProjMajorCollege, (major_id, body.college_id))
        if exists:
            if body.college_owner_id is not None:
                exists.college_owner_id = body.college_owner_id
        else:
            self.db.add(
                ProjMajorCollege(
                    major_id=major_id,
                    college_id=body.college_id,
                    college_owner_id=body.college_owner_id,
                )
            )
        self.db.flush()

    def remove_major_college(self, major_id: int, college_id: int, operator: Any) -> None:
        major = self.get_major(major_id)
        # 数据范围：项目在我范围内，或操作的是本学院
        if not can_edit_major_college(operator, major, college_id):
            raise ForbiddenError("无权修改该项目的参与学院")
        sub_count = self.db.scalar(
            select(func.count()).select_from(ProjSub).where(
                ProjSub.major_id == major_id,
                ProjSub.college_id == college_id,
                ProjSub.deleted_at.is_(None),
            )
        )
        if sub_count:
            raise BusinessError("该学院下还有子项目，请先删除子项目")
        mc = self.db.get(ProjMajorCollege, (major_id, college_id))
        if mc is None:
            raise NotFoundError("该学院未参与此项目")
        self.db.delete(mc)
        self.db.flush()

    # ============================================================ 子项目
    def list_subs(
        self,
        user: Any,
        *,
        major_id: int | None = None,
        college_id: int | None = None,
        status: str | None = None,
        owner_id: int | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[ProjSub], int]:
        stmt = select(ProjSub).where(ProjSub.deleted_at.is_(None))
        stmt = apply_scope(stmt, user, ProjSub)

        if major_id:
            stmt = stmt.where(ProjSub.major_id == major_id)
        if college_id:
            stmt = stmt.where(ProjSub.college_id == college_id)
        if status:
            stmt = stmt.where(ProjSub.status == status)
        if owner_id:
            stmt = stmt.where(ProjSub.owner_id == owner_id)
        if keyword:
            stmt = stmt.where(ilike(ProjSub.name, keyword))

        total = self.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = list(
            self.db.scalars(
                stmt.order_by(ProjSub.id.desc()).offset((page - 1) * page_size).limit(page_size)
            )
        )
        return rows, total

    def get_sub(self, sub_id: int) -> ProjSub:
        s = self.db.get(ProjSub, sub_id)
        if s is None or s.deleted_at is not None:
            raise NotFoundError("子项目不存在")
        return s

    def create_sub(self, body: SubIn) -> ProjSub:
        major = self.db.get(ProjMajor, body.major_id)
        if major is None or major.deleted_at is not None:
            raise BusinessError("大项目不存在")
        # 已结项/已中止的大项目不能再分解新子项目，否则「已结项」形同虚设
        if major.status in ("CLOSED", "ABORTED"):
            raise BusinessError(f"大项目已{major.status == 'CLOSED' and '结项' or '中止'}，不能再新建子项目")
        if self.db.get(SysCollege, body.college_id) is None:
            raise BusinessError("学院不存在")
        # ★ 硬规则：子项目必须同时归属大项目和学院，且学院必须已参与该项目
        mc = self.db.get(ProjMajorCollege, (body.major_id, body.college_id))
        if mc is None:
            raise BusinessError("该学院尚未参与此大项目，请先在学院分工中添加")
        if body.owner_id and self.db.get(SysUser, body.owner_id) is None:
            raise BusinessError("子项目负责人不存在")

        s = ProjSub(**body.model_dump())
        # 若计划时间已填且未锁原始计划，一并锁定
        s.original_start = body.plan_start
        s.original_end = body.plan_end
        self.db.add(s)
        self.db.flush()
        return s

    def update_sub(self, sub_id: int, body: SubUpdateIn, operator: Any | None = None) -> ProjSub:
        """编辑子项目

        operator 用于结项确认人校验与变更留痕。早期接口没传 operator，
        结项只校验进度 100%（带着 18 条未关闭预警也能结项）。
        """
        s = self.get_sub(sub_id)
        data = body.model_dump(exclude_unset=True)
        # change_reason 只用于写留痕，不是业务字段（P1-9）
        change_reason = data.pop("change_reason", None)

        # 结项后只读（P0-4）：除 status 外一律拒绝。
        # 早期实测 CLOSED 后还能改名称/计划/权重，与「结项=归档」的定位不符。
        # status 要放行：状态机里 CLOSED 没有后继状态，实际会被 _ALLOWED_TRANSITIONS 拦住，
        # 但保留这一条是为了错误文案能落到「状态不允许迁移」而不是「已结项不能修改」。
        if s.status == "CLOSED":
            blocked = [k for k in data if k != "status"]
            if blocked:
                raise BusinessError("子项目已结项，只能查看，不能再修改")

        # 状态迁移校验
        new_status = data.get("status")
        if new_status and new_status != s.status:
            allowed = _ALLOWED_TRANSITIONS.get(s.status, set())
            if new_status not in allowed:
                raise BusinessError(
                    f"状态不允许从「{s.status}」直接变为「{new_status}」"
                )
            if new_status == "CLOSED":
                # 结项三条件（文档 6.1 第 6 项，P0-4）
                self._check_close_conditions(s, operator)
                s.closed_at = datetime.now(UTC)

        # 计划时间变更：只改 plan_*，不动 original_*（第 38 项）
        # 变更留痕（P1-9）：记录 plan_start/plan_end/weight/owner_id/status 的改前改后值
        for field in _TRACKED_CHANGE_FIELDS:
            if field not in data:
                continue
            old = getattr(s, field, None)
            new = data[field]
            if _change_value(old) == _change_value(new):
                continue
            self._write_change_log(
                target_type="SUB",
                target_id=sub_id,
                field=field,
                old=old,
                new=new,
                reason=change_reason,
                operator=operator,
            )

        for k, v in data.items():
            setattr(s, k, v)

        if s.plan_start and s.plan_end and s.plan_start > s.plan_end:
            raise BusinessError("计划开始日期不能晚于结束日期")

        self.db.flush()
        return s

    def close_sub(
        self,
        sub_id: int,
        operator: Any,
        remark: str | None = None,
    ) -> ProjSub:
        """结项确认（独立接口 POST /subs/{id}/close，P0-4）

        与 update_sub 的 CLOSED 分支共用同一套校验，避免两个入口口径不一致。
        """
        s = self.get_sub(sub_id)
        if s.status == "CLOSED":
            raise BusinessError("该子项目已结项")
        allowed = _ALLOWED_TRANSITIONS.get(s.status, set())
        if "CLOSED" not in allowed:
            raise BusinessError(
                f"当前状态「{s.status}」不允许结项，请先更新进度进入进行中"
            )
        self._check_close_conditions(s, operator)
        old_status = s.status
        s.status = "CLOSED"
        s.closed_at = datetime.now(UTC)
        self._write_change_log(
            target_type="SUB",
            target_id=sub_id,
            field="status",
            old=old_status,
            new="CLOSED",
            reason=(remark or "结项确认"),
            operator=operator,
        )
        self.db.flush()
        return s

    def _check_close_conditions(self, s: ProjSub, operator: Any | None) -> None:
        """子项目结项三条件（文档 6.1 第 6 项）

        1. 进度 100%（原有条件）
        2. 至少 1 份已提交月报（参数 report.close_need_monthly 控制）
        3. 无未关闭预警（参数 report.close_block_open_alert 控制）
        4. 学院负责人确认（管理员/全校范围放行）
        """
        # 1) 进度
        if s.current_progress != 100:
            raise BusinessError("进度未达 100%，不能结项")

        cfg = ConfigService(self.db)

        # 2) 材料：至少一份 SUBMITTED 月报
        if cfg.get_bool("report.close_need_monthly", True):
            from app.models.report import Report

            monthly = self.db.scalar(
                select(func.count()).select_from(Report).where(
                    Report.sub_id == s.id,
                    Report.type == "MONTHLY",
                    Report.status == "SUBMITTED",
                )
            )
            if not monthly:
                raise BusinessError("结项前至少需要提交 1 份月报")

        # 3) 无未关闭预警（PENDING/CLAIMED）
        if cfg.get_bool("report.close_block_open_alert", True):
            from app.models.alert import Alert

            open_alerts = self.db.scalar(
                select(func.count()).select_from(Alert).where(
                    Alert.sub_id == s.id,
                    Alert.status.in_(("PENDING", "CLAIMED")),
                )
            )
            if open_alerts:
                raise BusinessError(
                    f"还有 {open_alerts} 条未关闭预警，请先处置后再结项"
                )

        # 4) 学院负责人确认：该子项目所属学院的负责人，或全校范围/管理员
        if operator is not None and not operator.has_all_scope():
            college_owner_ids = set(
                self.db.scalars(
                    select(ProjMajorCollege.college_owner_id).where(
                        ProjMajorCollege.major_id == s.major_id,
                        ProjMajorCollege.college_id == s.college_id,
                        ProjMajorCollege.college_owner_id.is_not(None),
                    )
                )
            )
            if operator.id not in college_owner_ids:
                raise ForbiddenError("结项需学院负责人确认")


    def delete_sub(self, sub_id: int) -> None:
        s = self.get_sub(sub_id)
        if s.status == "CLOSED":
            raise BusinessError("已结项的子项目不能删除")
        s.deleted_at = datetime.now(UTC)
        self.db.flush()

    # ============================================================ 完整性检查
    def check_completeness(self, major_id: int, persist: bool = True) -> CompletenessOut:
        """检查 4 项：是否配置学院、学院是否有负责人、子项目是否有负责人、是否有计划时间"""
        major = self.get_major(major_id)
        items: list[IncompleteItem] = []

        colleges = self.db.execute(
            select(ProjMajorCollege, SysCollege)
            .join(SysCollege, SysCollege.id == ProjMajorCollege.college_id)
            .where(ProjMajorCollege.major_id == major_id)
        ).all()

        if not colleges:
            items.append(
                IncompleteItem(code="NO_COLLEGE", label="未配置参与学院", detail="至少添加一个学院")
            )
        else:
            no_owner = [c.name for mc, c in colleges if not mc.college_owner_id]
            if no_owner:
                items.append(
                    IncompleteItem(
                        code="COLLEGE_NO_OWNER",
                        label="学院未指定负责人",
                        detail="、".join(no_owner),
                    )
                )

        subs = list(
            self.db.scalars(
                select(ProjSub).where(
                    ProjSub.major_id == major_id, ProjSub.deleted_at.is_(None)
                )
            )
        )
        if not subs:
            items.append(
                IncompleteItem(code="NO_SUB", label="未分解子项目", detail="至少创建一个子项目")
            )
        else:
            no_owner_subs = [s.name for s in subs if not s.owner_id]
            if no_owner_subs:
                items.append(
                    IncompleteItem(
                        code="SUB_NO_OWNER",
                        label="子项目未指定负责人",
                        detail="、".join(no_owner_subs[:10]),
                    )
                )
            no_plan = [s.name for s in subs if not (s.plan_start and s.plan_end)]
            if no_plan:
                items.append(
                    IncompleteItem(
                        code="SUB_NO_PLAN",
                        label="子项目未设置计划起止时间",
                        detail="、".join(no_plan[:10]),
                    )
                )

        result = CompletenessOut(
            major_id=major_id,
            complete=not items,
            items=items,
            summary={
                "colleges": len(colleges),
                "subs": len(subs),
                "subs_no_owner": len([s for s in subs if not s.owner_id]),
                "subs_no_plan": len([s for s in subs if not (s.plan_start and s.plan_end)]),
            },
        )

        if persist:
            major.incomplete_reason = {
                "complete": result.complete,
                "items": [i.model_dump() for i in items],
                "checked_at": datetime.now(UTC).isoformat(),
            }
            self.db.flush()

        return result
