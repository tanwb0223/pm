"""alert_action.operator_id 允许为空（系统自动动作）

Revision ID: 0003_alert_action_nullable
Revises: 0002_auth
Create Date: 2026-10-19
"""
from __future__ import annotations

from alembic import op

revision = "0003_alert_nullable"
down_revision = "0002_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 系统自动动作（超时升级等）没有操作人，operator_id 需允许为空
    op.alter_column("alert_action", "operator_id", nullable=True)
    op.execute(
        "COMMENT ON COLUMN alert_action.operator_id IS '操作人；系统自动动作（如超时升级）为空'"
    )


def downgrade() -> None:
    op.execute("DELETE FROM alert_action WHERE operator_id IS NULL")
    op.alter_column("alert_action", "operator_id", nullable=False)
