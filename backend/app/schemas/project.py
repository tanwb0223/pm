"""Pydantic 请求/响应模型：项目台账"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

MajorType = Literal[
    "产业学院共建", "实训基地建设", "订单班与现代学徒制", "课程与教材共建", "双师基地建设", "其他"
]
MajorStatus = Literal["ACTIVE", "CLOSED", "ABORTED"]
SubStatus = Literal["NOT_STARTED", "IN_PROGRESS", "ABORTED", "CLOSED"]


# ---------------------------------------------------------------- 学院
class CollegeIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    code: str | None = Field(default=None, max_length=32)
    parent_id: int | None = None
    sort: int = 0


class CollegeOut(BaseModel):
    id: int
    name: str
    code: str | None
    parent_id: int | None
    sort: int

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------- 大项目
class MajorIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    type: MajorType = "其他"
    partner_company: str | None = Field(default=None, max_length=255)
    build_period: str | None = Field(default=None, max_length=64)
    owner_id: int | None = None
    status: MajorStatus = "ACTIVE"
    original_start: date | None = None
    original_end: date | None = None
    budget_total: Decimal | None = None
    budget_used: Decimal | None = None

    @model_validator(mode="after")
    def check_dates(self):
        if self.original_start and self.original_end and self.original_start > self.original_end:
            raise ValueError("原始计划开始日期不能晚于结束日期")
        return self


class MajorUpdateIn(MajorIn):
    """编辑时全部可选"""

    name: str | None = Field(default=None, max_length=255)
    type: MajorType | None = None


class MajorOut(BaseModel):
    id: int
    name: str
    type: str
    partner_company: str | None
    build_period: str | None
    owner_id: int | None
    owner_name: str | None = None
    status: str
    original_start: date | None
    original_end: date | None
    budget_total: Decimal | None
    budget_used: Decimal | None
    acceptance_status: str
    college_count: int = 0
    sub_count: int = 0
    incomplete_reason: dict | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------- 学院分工
class MajorCollegeIn(BaseModel):
    college_id: int
    college_owner_id: int | None = None


class MajorCollegeOut(BaseModel):
    college_id: int
    college_name: str
    college_owner_id: int | None
    college_owner_name: str | None = None
    sub_count: int = 0


# ---------------------------------------------------------------- 子项目
class SubIn(BaseModel):
    major_id: int
    college_id: int
    name: str = Field(min_length=1, max_length=255)
    owner_id: int | None = None
    plan_start: date | None = None
    plan_end: date | None = None
    weight: Decimal | None = None
    status: SubStatus = "NOT_STARTED"

    @field_validator("weight")
    @classmethod
    def check_weight(cls, v: Decimal | None):
        if v is not None and v < 0:
            raise ValueError("权重不能为负")
        return v

    @model_validator(mode="after")
    def check_plan(self):
        if self.plan_start and self.plan_end and self.plan_start > self.plan_end:
            raise ValueError("计划开始日期不能晚于结束日期")
        return self


class SubUpdateIn(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    owner_id: int | None = None
    plan_start: date | None = None
    plan_end: date | None = None
    weight: Decimal | None = None
    status: SubStatus | None = None


class SubOut(BaseModel):
    id: int
    major_id: int
    major_name: str | None = None
    college_id: int
    college_name: str | None = None
    name: str
    owner_id: int | None
    owner_name: str | None = None
    plan_start: date | None
    plan_end: date | None
    weight: Decimal | None
    status: str
    current_progress: int
    last_update_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------- 完整性检查
class IncompleteItem(BaseModel):
    code: str
    label: str
    detail: str = ""


class CompletenessOut(BaseModel):
    major_id: int
    complete: bool
    items: list[IncompleteItem] = []
    summary: dict[str, int] = {}
