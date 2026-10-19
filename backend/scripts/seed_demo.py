# -*- coding: utf-8 -*-
"""生成演示数据（幂等，可重复执行）

用于本地开发、演示、验收前造数据。

用法：
    python scripts/seed_demo.py            # 生成演示数据
    python scripts/seed_demo.py --clean    # 先清空业务数据再生成

生成的规模：
    4 个学院 / 4 个教师 / 3 个大项目 / 7 个子项目 / 17 条预警 / 4 份报表 / 2 条风险
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.project import (  # noqa: E402
    ProgRecord,
    ProgSnapshot,
    ProjMajor,
    ProjMajorCollege,
    ProjSub,
)
from app.models.report import Report  # noqa: E402
from app.models.system import SysCollege, SysHoliday, SysRole, SysUser, SysUserRole  # noqa: E402
from app.services.alert_service import AlertService  # noqa: E402
from app.services.progress_service import ProgressService  # noqa: E402

COLLEGES = [
    ("物流管理学院", "WL"),
    ("运输管理学院", "YS"),
    ("智能制造学院", "ZN"),
    ("信息工程学院", "XX"),
]

TEACHERS = [
    ("T001", "张伟", "物流管理学院"),
    ("T002", "李娜", "运输管理学院"),
    ("T003", "王强", "智能制造学院"),
    ("T004", "赵敏", "信息工程学院"),
]

MAJORS = [
    ("智慧物流产教融合实训基地建设项目", "实训基地建设", "顺丰速运", "张伟"),
    ("智能制造产业学院共建项目", "产业学院共建", "海尔智造", "王强"),
    ("数字商贸订单班与现代学徒制项目", "订单班与现代学徒制", "京东物流", "李娜"),
]

# (大项目序号, 学院, 子项目名, 负责人, 计划开始偏移, 计划结束偏移, 权重, 当前进度)
SUBS = [
    (0, "物流管理学院", "实训场地改造", "张伟", -60, 60, 3, 35),
    (0, "物流管理学院", "课程体系开发", "张伟", -30, 90, 2, 60),
    (0, "运输管理学院", "校企联合教材", "李娜", -45, 45, 1, 10),
    (1, "智能制造学院", "产线实训设备采购", "王强", -70, 50, 3, 45),
    (1, "信息工程学院", "工业互联网平台搭建", "赵敏", -20, 100, 2, 15),
    (2, "物流管理学院", "订单班招生与培养", "张伟", -50, 70, 2, 55),
    (2, "信息工程学院", "电商运营实训系统", "赵敏", -40, 80, 1, 50),
]

MAJOR_COLLEGES = {
    0: ["物流管理学院", "运输管理学院"],
    1: ["智能制造学院", "信息工程学院"],
    2: ["物流管理学院", "信息工程学院"],
}


def clean(db) -> None:
    """清空业务数据（保留角色、权限、参数等系统种子）"""
    db.execute(
        text(
            "TRUNCATE audit_log, alert_action, alert, report, prog_snapshot, prog_record, "
            "proj_sub, proj_major_college, proj_major, sys_college, risk, notify_log, "
            "sys_user_role, sys_user, sys_holiday RESTART IDENTITY CASCADE"
        )
    )
    db.execute(text("DELETE FROM sys_role WHERE is_system = false"))
    db.commit()
    print("已清空业务数据")


def main() -> None:
    db = SessionLocal()
    today = date.today()

    def d(offset: int) -> date:
        return today + timedelta(days=offset)

    try:
        if "--clean" in sys.argv:
            clean(db)

        # ---------------------------------------------------------- 学院
        college_ids: dict[str, int] = {}
        for name, code in COLLEGES:
            c = db.scalar(select(SysCollege).where(SysCollege.code == code))
            if c is None:
                c = SysCollege(name=name, code=code, sort=0)
                db.add(c)
                db.flush()
            college_ids[name] = c.id
        print(f"学院：{len(college_ids)} 个")

        # ---------------------------------------------------------- 教师
        user_ids: dict[str, int] = {}
        for staff_no, name, college_name in TEACHERS:
            u = db.scalar(select(SysUser).where(SysUser.staff_no == staff_no))
            if u is None:
                u = SysUser(
                    staff_no=staff_no,
                    name=name,
                    college_id=college_ids[college_name],
                    password_hash=hash_password("Test@123"),
                    status="ACTIVE",
                )
                db.add(u)
                db.flush()
            user_ids[name] = u.id

            # 授予子项目负责人角色（SELF 范围）
            role = db.scalar(select(SysRole).where(SysRole.code == "SUB_OWNER"))
            if role and not db.scalar(
                select(SysUserRole).where(
                    SysUserRole.user_id == u.id, SysUserRole.role_id == role.id
                )
            ):
                db.add(
                    SysUserRole(
                        user_id=u.id, role_id=role.id, scope_type="SELF", scope_id=None
                    )
                )
        db.flush()
        print(f"教师：{len(user_ids)} 个（密码 Test@123）")

        # ---------------------------------------------------------- 大项目
        major_ids: list[int] = []
        for name, type_, company, owner_name in MAJORS:
            m = db.scalar(select(ProjMajor).where(ProjMajor.name == name))
            if m is None:
                m = ProjMajor(
                    name=name,
                    type=type_,
                    partner_company=company,
                    build_period="2026-2028",
                    owner_id=user_ids[owner_name],
                    status="ACTIVE",
                    original_start=d(-90),
                    original_end=d(180),
                )
                db.add(m)
                db.flush()
            major_ids.append(m.id)
        print(f"大项目：{len(major_ids)} 个")

        # ---------------------------------------------------------- 学院分工
        for idx, college_names in MAJOR_COLLEGES.items():
            for cname in college_names:
                exists = db.get(ProjMajorCollege, (major_ids[idx], college_ids[cname]))
                if exists is None:
                    owner_name = next(
                        (t[1] for t in TEACHERS if t[2] == cname), None
                    )
                    db.add(
                        ProjMajorCollege(
                            major_id=major_ids[idx],
                            college_id=college_ids[cname],
                            college_owner_id=user_ids.get(owner_name) if owner_name else None,
                        )
                    )
        db.flush()
        print("学院分工：已完成")

        # ---------------------------------------------------------- 子项目
        progress_svc = ProgressService(db)
        sub_ids: list[int] = []
        for mi, cname, name, owner, ps, pe, weight, progress in SUBS:
            s = db.scalar(
                select(ProjSub).where(
                    ProjSub.major_id == major_ids[mi], ProjSub.name == name
                )
            )
            if s is None:
                s = ProjSub(
                    major_id=major_ids[mi],
                    college_id=college_ids[cname],
                    name=name,
                    owner_id=user_ids[owner],
                    plan_start=d(ps),
                    plan_end=d(pe),
                    original_start=d(ps),
                    original_end=d(pe),
                    weight=weight,
                    status="IN_PROGRESS",
                )
                db.add(s)
                db.flush()
            sub_ids.append(s.id)

            # 首次填报
            if not db.scalar(select(ProgRecord).where(ProgRecord.sub_id == s.id)):
                class _Op:
                    id = user_ids[owner]
                    roles = ["SUB_OWNER"]
                    permissions = {"progress:update"}

                    def has_perm(self, code: str) -> bool:
                        return True

                progress_svc.update_progress(
                    s.id,
                    progress=progress,
                    note="按计划推进",
                    no_change=False,
                    rollback_reason=None,
                    operator=_Op(),
                )
        db.flush()
        print(f"子项目：{len(sub_ids)} 个（含首期进度填报）")

        # ---------------------------------------------------------- 快照 + 预警
        for offset in (2, 1, 0):
            progress_svc.generate_snapshots(d(-offset))
        db.flush()

        scan_result = AlertService(db).scan(today)
        print(f"预警：扫描生成 {scan_result['created']} 条，合并 {scan_result['merged']} 条")

        # ---------------------------------------------------------- 报表
        report_count = 0
        for sid in sub_ids[:4]:
            exists = db.scalar(
                select(Report).where(
                    Report.sub_id == sid,
                    Report.type == "WEEKLY",
                    Report.period_start == today - timedelta(days=today.weekday()),
                )
            )
            if exists is None:
                db.add(
                    Report(
                        sub_id=sid,
                        type="WEEKLY",
                        period_start=today - timedelta(days=today.weekday()),
                        period_end=today - timedelta(days=today.weekday()) + timedelta(days=6),
                        progress=30,
                        content={
                            "summary": "本周按计划推进，完成阶段性任务。",
                            "next_week": "继续推进下一阶段",
                        },
                        status="SUBMITTED",
                    )
                )
                report_count += 1
        db.flush()
        print(f"报表：{report_count} 份")

        # ---------------------------------------------------------- 风险
        from app.models.alert import Risk

        risk_specs = [
            (major_ids[0], "HIGH", "合作企业设备投入延迟，影响实训场地改造进度", "张伟", -5),
            (major_ids[1], "MEDIUM", "平台供应商技术方案待确认", "王强", 10),
        ]
        risk_count = 0
        for mid, level, desc, owner, close_offset in risk_specs:
            exists = db.scalar(select(Risk).where(Risk.description == desc))
            if exists is None:
                db.add(
                    Risk(
                        major_id=mid,
                        level=level,
                        description=desc,
                        owner_id=user_ids[owner],
                        plan_close_date=d(close_offset),
                        status="OPEN",
                    )
                )
                risk_count += 1
        db.flush()

        db.commit()
        print(f"风险：{risk_count} 条")
        print("\n演示数据生成完成")
        print("  管理员：admin / Admin@123")
        print("  教师：  T001~T004 / Test@123")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
