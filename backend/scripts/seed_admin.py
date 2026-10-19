# -*- coding: utf-8 -*-
"""初始化管理员账号（幂等，可重复执行）

用法：
    python scripts/seed_admin.py [工号] [密码]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.system import SysRole, SysUser, SysUserRole  # noqa: E402


def main() -> None:
    staff_no = sys.argv[1] if len(sys.argv) > 1 else "admin"
    password = sys.argv[2] if len(sys.argv) > 2 else "Admin@123"

    db = SessionLocal()
    try:
        user = db.scalar(select(SysUser).where(SysUser.staff_no == staff_no))
        if user is None:
            user = SysUser(
                staff_no=staff_no,
                name="系统管理员",
                password_hash=hash_password(password),
                status="ACTIVE",
            )
            db.add(user)
            db.flush()
            print(f"已创建用户：{staff_no}")
        else:
            user.password_hash = hash_password(password)
            print(f"已重置密码：{staff_no}")

        role = db.scalar(select(SysRole).where(SysRole.code == "SYS_ADMIN"))
        if role is None:
            raise RuntimeError("缺少 SYS_ADMIN 角色，请先执行 alembic upgrade head")

        exists = db.scalar(
            select(SysUserRole).where(
                SysUserRole.user_id == user.id, SysUserRole.role_id == role.id
            )
        )
        if exists is None:
            db.add(SysUserRole(user_id=user.id, role_id=role.id, scope_type="ALL", scope_id=None))
            print("已授予系统管理员角色")

        db.commit()
        print(f"完成。登录：{staff_no} / {password}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
