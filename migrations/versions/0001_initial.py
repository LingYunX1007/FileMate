"""Initial FileMate schema.

Revision ID: 0001
Revises:
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def created():
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade():
    op.create_table(
        "roots", sa.Column("id", sa.Uuid(), primary_key=True), created(),
        sa.Column("path", sa.Text(), nullable=False), sa.Column("path_key", sa.String(64), unique=True, nullable=False),
        sa.Column("label", sa.Text(), nullable=False), sa.Column("device", sa.String(32), nullable=False),
        sa.Column("inode", sa.String(32), nullable=False), sa.UniqueConstraint("device", "inode"),
        sa.Column("scanned_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "files", sa.Column("id", sa.Uuid(), primary_key=True), created(),
        sa.Column("root_id", sa.Uuid(), sa.ForeignKey("roots.id"), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False), sa.Column("path_key", sa.String(64), nullable=False),
        sa.Column("extension", sa.Text(), nullable=False), sa.Column("present", sa.Boolean(), nullable=False),
        sa.Column("fingerprint", JSONB(), nullable=False), sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("mtime_ns", sa.BigInteger(), nullable=False), sa.Column("version", sa.String(64), nullable=False),
        sa.Column("content_status", sa.String(32), nullable=False),
        sa.Column("content_truncated", sa.Boolean(), nullable=False), sa.UniqueConstraint("root_id", "path_key"),
    )
    op.create_index("ix_files_root_id", "files", ["root_id"])
    op.create_table(
        "document_chunks", sa.Column("file_id", sa.Uuid(), sa.ForeignKey("files.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("ordinal", sa.Integer(), primary_key=True), sa.Column("text", sa.Text(), nullable=False),
        sa.Column("location", JSONB(), nullable=False),
    )
    op.create_table(
        "tasks", sa.Column("id", sa.Uuid(), primary_key=True), created(),
        sa.Column("goal", sa.Text(), nullable=False), sa.Column("session_id", sa.Text()),
    )
    op.create_index("ix_tasks_session_id", "tasks", ["session_id"])
    op.create_table(
        "result_sets", sa.Column("id", sa.Uuid(), primary_key=True), created(),
        sa.Column("root_id", sa.Uuid(), sa.ForeignKey("roots.id"), nullable=False),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id")),
        sa.Column("query", JSONB(), nullable=False), sa.Column("items", JSONB(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
    )
    op.create_index("ix_result_sets_task_id", "result_sets", ["task_id"])
    op.create_table(
        "plans", sa.Column("id", sa.Uuid(), primary_key=True), created(),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id")),
        sa.Column("undo_of", sa.Uuid(), sa.ForeignKey("plans.id")), sa.Column("title", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False), sa.Column("status", sa.String(32), nullable=False),
    )
    op.create_index("ix_plans_task_id", "plans", ["task_id"])
    op.create_table(
        "plan_versions", sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("plans.id"), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True), created(),
        sa.Column("hash", sa.String(64), nullable=False), sa.Column("items", JSONB(), nullable=False),
    )
    op.create_table(
        "approvals", sa.Column("plan_id", sa.Uuid(), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True), created(),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["plan_id", "version"], ["plan_versions.plan_id", "plan_versions.version"]),
    )
    op.create_table(
        "jobs", sa.Column("id", sa.Uuid(), primary_key=True), created(),
        sa.Column("plan_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("plan_version", sa.Integer(), nullable=False), sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False), sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["plan_id", "plan_version"], ["approvals.plan_id", "approvals.version"]),
    )
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_table(
        "operations", sa.Column("id", sa.Uuid(), primary_key=True), created(),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("jobs.id"), nullable=False), sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False), sa.Column("item", JSONB(), nullable=False),
        sa.Column("code", sa.String(64)), sa.Column("message", sa.Text()), sa.UniqueConstraint("job_id", "position"),
    )
    op.create_index("ix_operations_job_id", "operations", ["job_id"])
    op.create_table(
        "operation_events", sa.Column("id", sa.BigInteger(), primary_key=True), created(),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("operation_id", sa.Uuid(), sa.ForeignKey("operations.id")),
        sa.Column("status", sa.String(32), nullable=False), sa.Column("code", sa.String(64)),
        sa.Column("message", sa.Text(), nullable=False),
    )
    op.create_index("ix_operation_events_job_id", "operation_events", ["job_id"])


def downgrade():
    for table in ("operation_events", "operations", "jobs", "approvals", "plan_versions", "plans",
                  "result_sets", "tasks", "document_chunks", "files", "roots"):
        op.drop_table(table)
