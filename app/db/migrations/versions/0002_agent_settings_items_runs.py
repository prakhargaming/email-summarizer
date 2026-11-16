"""agent_settings, action_items, agent_runs

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_settings",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("focus_text", sa.Text(), server_default=""),
        sa.Column("timezone", sa.Text(), nullable=False, server_default="America/New_York"),
        sa.Column("notify_hour_local", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "action_items",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("gmail_thread_id", sa.Text(), nullable=False),
        sa.Column("gmail_message_id", sa.Text()),
        sa.Column("sender", sa.Text()),
        sa.Column("subject", sa.Text()),
        sa.Column("summary", sa.Text()),
        sa.Column("why_it_matters", sa.Text()),
        sa.Column("suggested_action", sa.Text()),
        sa.Column("priority", sa.SmallInteger()),
        sa.Column("due_hint", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decision_note", sa.Text()),
        sa.Column("run_id", sa.Uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "gmail_thread_id"),
        sa.CheckConstraint("status in ('pending','accepted','rejected','done')"),
    )
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("emails_seen", sa.Integer()),
        sa.Column("items_created", sa.Integer()),
        sa.Column("status", sa.Text()),
        sa.Column("error_code", sa.Text()),
    )
    for table in ("agent_settings", "action_items", "agent_runs"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("agent_runs")
    op.drop_table("action_items")
    op.drop_table("agent_settings")
