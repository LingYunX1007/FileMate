from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, ForeignKeyConstraint, String, Text, UniqueConstraint
from sqlalchemy import create_engine, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import postgres_url


class Base(DeclarativeBase):
    pass


class Created:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Root(Created, Base):
    __tablename__ = "roots"
    __table_args__ = (UniqueConstraint("device", "inode"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    path: Mapped[str] = mapped_column(Text)
    path_key: Mapped[str] = mapped_column(String(64), unique=True)
    label: Mapped[str] = mapped_column(Text)
    device: Mapped[str] = mapped_column(String(32))
    inode: Mapped[str] = mapped_column(String(32))
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IndexedFile(Created, Base):
    __tablename__ = "files"
    __table_args__ = (UniqueConstraint("root_id", "path_key"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    root_id: Mapped[UUID] = mapped_column(ForeignKey("roots.id"), index=True)
    relative_path: Mapped[str] = mapped_column(Text)
    path_key: Mapped[str] = mapped_column(String(64))
    extension: Mapped[str] = mapped_column(Text)
    present: Mapped[bool] = mapped_column(default=True)
    fingerprint: Mapped[dict] = mapped_column(JSONB)
    size: Mapped[int] = mapped_column(BigInteger)
    mtime_ns: Mapped[int] = mapped_column(BigInteger)
    version: Mapped[str] = mapped_column(String(64))
    content_status: Mapped[str] = mapped_column(String(32))
    content_truncated: Mapped[bool] = mapped_column(default=False)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    file_id: Mapped[UUID] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), primary_key=True)
    ordinal: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    location: Mapped[dict] = mapped_column(JSONB)


class Task(Created, Base):
    __tablename__ = "tasks"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    goal: Mapped[str] = mapped_column(Text)
    session_id: Mapped[str | None] = mapped_column(Text, index=True)


class ResultSet(Created, Base):
    __tablename__ = "result_sets"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    root_id: Mapped[UUID] = mapped_column(ForeignKey("roots.id"))
    task_id: Mapped[UUID | None] = mapped_column(ForeignKey("tasks.id"), index=True)
    query: Mapped[dict] = mapped_column(JSONB)
    items: Mapped[list] = mapped_column(JSONB)
    total: Mapped[int]


class Plan(Created, Base):
    __tablename__ = "plans"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    task_id: Mapped[UUID | None] = mapped_column(ForeignKey("tasks.id"), index=True)
    undo_of: Mapped[UUID | None] = mapped_column(ForeignKey("plans.id"))
    title: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String(32), default="draft")


class PlanVersion(Created, Base):
    __tablename__ = "plan_versions"

    plan_id: Mapped[UUID] = mapped_column(ForeignKey("plans.id"), primary_key=True)
    version: Mapped[int] = mapped_column(primary_key=True)
    hash: Mapped[str] = mapped_column(String(64))
    items: Mapped[list] = mapped_column(JSONB)


class Approval(Created, Base):
    __tablename__ = "approvals"
    __table_args__ = (ForeignKeyConstraint(["plan_id", "version"], ["plan_versions.plan_id", "plan_versions.version"]),)

    plan_id: Mapped[UUID] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(primary_key=True)
    hash: Mapped[str] = mapped_column(String(64))


class Job(Created, Base):
    __tablename__ = "jobs"
    __table_args__ = (ForeignKeyConstraint(["plan_id", "plan_version"], ["approvals.plan_id", "approvals.version"]),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    plan_id: Mapped[UUID] = mapped_column(unique=True)
    plan_version: Mapped[int]
    plan_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), index=True, default="queued")
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Operation(Created, Base):
    __tablename__ = "operations"
    __table_args__ = (UniqueConstraint("job_id", "position"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id"), index=True)
    position: Mapped[int]
    status: Mapped[str] = mapped_column(String(32), default="pending")
    item: Mapped[dict] = mapped_column(JSONB)
    code: Mapped[str | None] = mapped_column(String(64))
    message: Mapped[str | None] = mapped_column(Text)


class OperationEvent(Created, Base):
    __tablename__ = "operation_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id"), index=True)
    operation_id: Mapped[UUID | None] = mapped_column(ForeignKey("operations.id"))
    status: Mapped[str] = mapped_column(String(32))
    code: Mapped[str | None] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)


WORK_LOCK = 1068407401


def make_sessions(url):
    engine = create_engine(postgres_url(url), pool_pre_ping=True, connect_args={"connect_timeout": 5})
    return sessionmaker(bind=engine, expire_on_commit=False)
