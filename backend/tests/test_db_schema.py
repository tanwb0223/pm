"""冒烟测试 1：新建库必须带齐索引

对应问题：索引原来只写在 Alembic 迁移里，SQLite 走 create_all 建表时
一条都不会建，默认部署的库全是全表扫描。这里锁住"模型声明 = 实际索引"。
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa

EXPECTED_INDEXES = {
    # 系统
    "idx_user_role_user",
    "idx_user_role_scope",
    "idx_holiday_date",
    "idx_audit_time",
    "idx_audit_op",
    "idx_audit_target",
    "idx_notify_receiver",
    "idx_notify_biz",
    # 项目台账
    "idx_major_owner",
    "idx_major_status",
    "idx_major_deleted",
    "idx_major_college_college",
    "idx_sub_major",
    "idx_sub_college",
    "idx_sub_owner",
    "idx_sub_status",
    "idx_sub_plan",
    "idx_sub_last_update",
    "idx_record_sub_time",
    "idx_record_time",
    "idx_snap_date",
    "idx_snap_dev",
    "idx_snap_stagnant",
    # 报表 / 附件
    "idx_report_period",
    "idx_report_sub",
    "idx_report_status",
    "ix_report_grace_sub_type",
    "idx_att_biz",
    "idx_att_uploader",
    "idx_att_created",
    "idx_att_sha256",
    # 预警 / 风险
    "idx_alert_dedup",
    "idx_alert_status",
    "idx_alert_scope",
    "idx_alert_deadline",
    "idx_alert_sub",
    "idx_action_alert",
    "idx_risk_major",
    "idx_risk_open",
    # 认证
    "idx_login_state_expires",
    "idx_login_state_code",
    "idx_identity_user",
}


def test_all_expected_indexes_declared_on_models():
    import app.models.alert  # noqa: F401
    import app.models.attachment  # noqa: F401
    import app.models.auth  # noqa: F401
    import app.models.project  # noqa: F401
    import app.models.report  # noqa: F401
    import app.models.system  # noqa: F401
    from app.db.session import Base

    declared = {ix.name for t in Base.metadata.tables.values() for ix in t.indexes}
    missing = EXPECTED_INDEXES - declared
    assert not missing, f"以下索引未在模型声明（SQLite 不会创建）：{sorted(missing)}"


def test_create_all_actually_builds_indexes(tmp_path):
    """模型声明了还要真建出来 —— 直接建库再查 sqlite_master"""
    import app.models.alert  # noqa: F401
    import app.models.attachment  # noqa: F401
    import app.models.auth  # noqa: F401
    import app.models.project  # noqa: F401
    import app.models.report  # noqa: F401
    import app.models.system  # noqa: F401
    from app.db.session import Base

    db_file = tmp_path / "index_check.db"
    engine = sa.create_engine(f"sqlite:///{db_file.as_posix()}")
    Base.metadata.create_all(engine)
    engine.dispose()

    with sa.create_engine(f"sqlite:///{db_file.as_posix()}").connect() as conn:
        names = {
            r[0]
            for r in conn.exec_driver_sql(
                "select name from sqlite_master where type='index' and name not like 'sqlite_%'"
            )
        }
    assert EXPECTED_INDEXES <= names, f"建库后缺失索引：{sorted(EXPECTED_INDEXES - names)}"


@pytest.mark.parametrize("table", ["alert", "prog_record", "report"])
def test_hot_query_no_full_scan(table):
    """高频查询必须走索引，不能再 SCAN"""
    import app.models.alert  # noqa: F401
    import app.models.attachment  # noqa: F401
    import app.models.auth  # noqa: F401
    import app.models.project  # noqa: F401
    import app.models.report  # noqa: F401
    import app.models.system  # noqa: F401
    import tempfile
    from pathlib import Path

    from app.db.session import Base

    path = Path(tempfile.mkdtemp()) / "plan.db"
    engine = sa.create_engine(f"sqlite:///{path.as_posix()}")
    Base.metadata.create_all(engine)

    queries = {
        "alert": "select * from alert where status='PENDING' and level='RED'",
        "prog_record": "select * from prog_record where sub_id=1 order by created_at desc limit 1",
        "report": "select * from report where sub_id=1 and type='WEEKLY'",
    }
    with engine.connect() as conn:
        plan = " ".join(
            str(row) for row in conn.exec_driver_sql("explain query plan " + queries[table])
        )
    engine.dispose()
    assert "SCAN" not in plan.upper(), f"{table} 仍是全表扫描：{plan}"
