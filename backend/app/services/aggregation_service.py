"""进度汇总计算（文档 4.1 + 确认事项第 19/20/21 项）

口径约定：
- 子项目进度 = 最后一次填报值
- 大项目进度 = Σ(子项目进度 × 权重) / Σ权重
- 学院进度   = 该学院在该大项目内子项目按权重加权
- 全校进度   = 各大项目等权平均（一期，界面注明）

分母口径：
- 只计 IN_PROGRESS 与 CLOSED
- CLOSED 按 100% 计入，保留权重
- NOT_STARTED / ABORTED 不计入
- Σ权重 = 0 时退化为等权
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.project import ProjMajor, ProjSub
from app.services.config_service import ConfigService

# 计入汇总的状态
_COUNTED_STATUSES = ("IN_PROGRESS", "CLOSED")


class AggregationService:
    def __init__(self, db: Session):
        self.db = db
        self.config = ConfigService(db)

    # ------------------------------------------------------------ 子项目
    def counted_subs(
        self,
        major_id: int | None = None,
        college_id: int | None = None,
        include_closed: bool | None = None,
    ) -> list[ProjSub]:
        if include_closed is None:
            include_closed = self.config.get_bool("aggregate.include_closed", True)
        statuses = _COUNTED_STATUSES if include_closed else ("IN_PROGRESS",)
        stmt = select(ProjSub).where(
            ProjSub.deleted_at.is_(None),
            ProjSub.status.in_(statuses),
        )
        if major_id is not None:
            stmt = stmt.where(ProjSub.major_id == major_id)
        if college_id is not None:
            stmt = stmt.where(ProjSub.college_id == college_id)
        return list(self.db.scalars(stmt))

    @staticmethod
    def _weighted(subs: list[ProjSub]) -> int:
        """加权平均；权重缺失按等权；全为 0 退化为等权"""
        if not subs:
            return 0
        weights: list[Decimal] = []
        values: list[int] = []
        for s in subs:
            # 已结项按 100% 计入
            values.append(100 if s.status == "CLOSED" else s.current_progress)
            w = s.weight
            # 权重缺失按等权（1）。早期按 0 计：同一项目里只要有人设了权重，
            # 没设权重的子项目就白算 0 分子，把汇总进度整体拉低。
            weights.append(Decimal(w) if w is not None else Decimal(1))

        total_w = sum(weights)
        if total_w <= 0:
            # 全都没设权重（或都是 0）→ 等权
            return int(sum(values) / len(values))

        acc = sum(Decimal(v) * w for v, w in zip(values, weights))
        return int((acc / total_w).quantize(Decimal("1")))

    # ------------------------------------------------------------ 对外
    def major_progress(self, major_id: int) -> int:
        return self._weighted(self.counted_subs(major_id=major_id))

    def college_progress(self, major_id: int, college_id: int) -> int:
        return self._weighted(self.counted_subs(major_id=major_id, college_id=college_id))

    def school_progress(self) -> int:
        """全校进度：各大项目等权平均（一期不按经费折算）"""
        major_ids = list(
            self.db.scalars(
                select(ProjMajor.id).where(
                    ProjMajor.deleted_at.is_(None), ProjMajor.status == "ACTIVE"
                )
            )
        )
        if not major_ids:
            return 0
        progresses = [self.major_progress(mid) for mid in major_ids]
        return int(sum(progresses) / len(progresses))

    def breakdown(self, major_id: int) -> dict:
        """大项目进度构成：用于详情页展示"""
        subs = self.counted_subs(major_id=major_id)
        colleges = {}
        for s in subs:
            colleges.setdefault(s.college_id, []).append(s)

        return {
            "major_progress": self._weighted(subs),
            "college_progress": {
                cid: self._weighted(items) for cid, items in colleges.items()
            },
            "counted_subs": len(subs),
            "detail": [
                {
                    "sub_id": s.id,
                    "name": s.name,
                    "college_id": s.college_id,
                    "weight": float(s.weight) if s.weight is not None else None,
                    "progress": 100 if s.status == "CLOSED" else s.current_progress,
                    "status": s.status,
                }
                for s in subs
            ],
        }
