from collections.abc import Generator
from datetime import date, datetime
from hmac import compare_digest
import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator
from sqlalchemy import or_, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException

from . import catalog, filesystem as fs, jobs, plans
from .common import Problem, path_key, require
from .config import Settings
from .db import IndexedFile, Job, Plan, ResultSet, Root, Task, WORK_LOCK, make_sessions


def _check_json(value):
    if isinstance(value, str) and ("\x00" in value or any(0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise ValueError("Null characters and invalid Unicode are unsupported.")
    if isinstance(value, dict):
        for key, entry in value.items():
            _check_json(key)
            _check_json(entry)
    elif isinstance(value, list):
        for entry in value:
            _check_json(entry)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    @field_validator("*")
    @classmethod
    def checked(cls, value):
        _check_json(value)
        return value


class RootInput(Input):
    path: str = Field(min_length=1, max_length=32000)
    label: str | None = Field(default=None, max_length=200)


class TaskInput(Input):
    goal: str = Field(min_length=1, max_length=10000)
    session_id: str | None = Field(default=None, max_length=500)


class SearchInput(Input):
    root_id: UUID
    query: str = Field(default="", max_length=1000)
    directory: str = Field(default="", max_length=32000)
    extension: str | None = Field(default=None, max_length=100)
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0, le=1_000_000)
    task_id: UUID | None = None
    modified_after: datetime | date | None = None
    modified_before: datetime | date | None = None

    @field_validator("directory")
    @classmethod
    def relative_directory(cls, value):
        if not value:
            return ""
        try:
            return fs.normalized_relative(value)
        except fs.FileProblem as exc:
            raise ValueError(str(exc)) from exc


class ItemInput(Input):
    file_id: UUID
    target_root_id: UUID
    target_path: str = Field(min_length=1, max_length=32000)
    reason: str = Field(min_length=1, max_length=3000)
    evidence: JsonValue = None

    @field_validator("evidence")
    @classmethod
    def bounded_evidence(cls, value):
        try:
            size = len(json.dumps(value, ensure_ascii=True, allow_nan=False))
        except (ValueError, RecursionError) as exc:
            raise ValueError("Evidence must contain finite JSON values.") from exc
        if size > 24000:
            raise ValueError("Evidence exceeds the allowed size.")
        return value


class PlanInput(Input):
    task_id: UUID | None = None
    title: str = Field(min_length=1, max_length=500)
    items: list[ItemInput] = Field(min_length=1, max_length=500)


class RevisionInput(Input):
    version: int = Field(ge=1)
    items: list[ItemInput] = Field(min_length=1, max_length=500)


class Binding(Input):
    version: int = Field(ge=1)
    hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def create_app(settings: Settings | None = None, *, sessions=None):
    settings = settings if settings is not None else Settings.from_env()
    sessions = sessions if sessions is not None else make_sessions(settings.database_url)
    app = FastAPI(title="FileMate", docs_url=None, redoc_url=None, openapi_url=None)

    def role(request: Request) -> str:
        scheme, delimiter, token = request.headers.get("authorization", "").partition(" ")
        if delimiter and scheme.lower() == "bearer":
            supplied = token.encode("utf-8")
            if compare_digest(supplied, settings.admin_token.encode("utf-8")):
                return "admin"
            if compare_digest(supplied, settings.tool_token.encode("utf-8")):
                return "tool"
        raise Problem(401, "unauthorized", "A valid FileMate service token is required.")

    def admin(identity: str = Depends(role)):
        if identity != "admin":
            raise Problem(403, "forbidden", "This operation requires the FileMate administrator token.")

    def transaction() -> Generator[Session, None, None]:
        with sessions.begin() as session:
            yield session

    def response(status, detail):
        return JSONResponse(status_code=status, content={"detail": detail})

    @app.exception_handler(Problem)
    def domain_error(_request, exc):
        return response(exc.status, exc.detail)

    @app.exception_handler(fs.FileProblem)
    def filesystem_error(_request, exc):
        status = 422 if exc.code == "invalid_path" else 409
        return response(status, {"code": exc.code, "message": str(exc)})

    @app.exception_handler(RequestValidationError)
    def request_error(_request, exc):
        fields = [{"field": ".".join(map(str, issue["loc"])), "message": issue["msg"]}
                  for issue in exc.errors()]
        return response(422, {"code": "invalid_request", "message": "Check the request fields.", "fields": fields})

    @app.exception_handler(OperationalError)
    def database_unavailable(_request, _exc):
        return response(503, {"code": "database_unavailable", "message": "PostgreSQL is unavailable."})

    @app.exception_handler(IntegrityError)
    def database_conflict(_request, _exc):
        return response(409, {"code": "conflict", "message": "The record was changed by another request."})

    @app.exception_handler(HTTPException)
    def routing_error(_request, exc):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return JSONResponse(status_code=exc.status_code, headers=exc.headers,
                            content={"detail": {"code": code, "message": str(exc.detail)}})

    router = APIRouter(prefix="/v1", dependencies=[Depends(role)])
    protected = [Depends(admin)]
    SessionDep = Annotated[Session, Depends(transaction, scope="function")]

    @router.get("/roots")
    def roots(session: SessionDep):
        return {"items": [catalog.root_data(root) for root in session.scalars(
            select(Root).order_by(Root.label, Root.path, Root.id))]}

    @router.post("/roots", dependencies=protected)
    def register_root(body: RootInput, session: SessionDep):
        root = fs.authorize_root(body.path)
        key = path_key(root.path)
        existing = session.scalar(select(Root).where(or_(Root.path_key == key, Root.path == root.path,
                                                        (Root.device == root.device) & (Root.inode == root.inode))))
        if existing is not None:
            if existing.device != root.device or existing.inode != root.inode:
                raise Problem(409, "root_changed", "A different directory already owns this registered path.")
            return catalog.root_data(existing)
        record = Root(path=root.path, path_key=key, device=root.device, inode=root.inode,
                      label=(body.label or "").strip() or root.path)
        session.add(record)
        session.flush()
        return catalog.root_data(record)

    @router.post("/roots/{root_id}/scan")
    def scan(root_id: UUID, session: SessionDep):
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": WORK_LOCK})
        return catalog.scan_root(session, require(session, Root, root_id))

    @router.get("/roots/{root_id}/inspect")
    def inspect(root_id: UUID, session: SessionDep):
        return catalog.inspect_root(session, require(session, Root, root_id))

    @router.post("/tasks")
    def add_task(body: TaskInput, session: SessionDep):
        task = Task(goal=body.goal, session_id=body.session_id)
        session.add(task)
        session.flush()
        return catalog.task_data(task)

    @router.get("/tasks/{task_id}")
    def task_context(task_id: UUID, session: SessionDep):
        task = require(session, Task, task_id)
        results = session.scalars(select(ResultSet).where(ResultSet.task_id == task_id)
                                  .order_by(ResultSet.created_at.desc(), ResultSet.id).limit(20))
        related = session.scalars(select(Plan).where(Plan.task_id == task_id)
                                  .order_by(Plan.created_at.desc(), Plan.id).limit(20))
        return {**catalog.task_data(task), "result_sets": [catalog.result_data(item) for item in results],
                "plans": [plans.plan_data(session, plan) for plan in related]}

    @router.get("/tasks")
    def session_tasks(session: SessionDep, session_id: str = Query(min_length=1, max_length=500)):
        if "\x00" in session_id:
            raise Problem(422, "invalid_request", "Enter a valid session ID.")
        related = session.scalars(select(Task).where(Task.session_id == session_id)
                                  .order_by(Task.created_at.desc(), Task.id).limit(10))
        return {"items": [catalog.task_data(task) for task in related]}

    @router.post("/search")
    def search(body: SearchInput, session: SessionDep):
        return catalog.search(session, body)

    @router.get("/files/{file_id}")
    def file_metadata(file_id: UUID, session: SessionDep):
        return catalog.file_data(require(session, IndexedFile, file_id))

    @router.get("/files/{file_id}/excerpt")
    def excerpt(file_id: UUID, session: SessionDep, start: int = Query(0, ge=0, le=2_000_000),
                limit: int = Query(4000, ge=1, le=12000)):
        return catalog.excerpt(session, file_id, start, limit)

    @router.post("/plans")
    def create_plan(body: PlanInput, session: SessionDep):
        return plans.new_plan(session, body.task_id, body.title, plans.make_items(session, body.items))

    @router.get("/plans")
    def list_plans(session: SessionDep):
        related = session.scalars(select(Plan).order_by(Plan.created_at.desc(), Plan.id).limit(100))
        return {"items": [plans.plan_data(session, plan) for plan in related]}

    @router.get("/plans/{plan_id}")
    def get_plan(plan_id: UUID, session: SessionDep):
        return plans.plan_data(session, require(session, Plan, plan_id))

    @router.post("/plans/{plan_id}/validate")
    def validate_plan(plan_id: UUID, session: SessionDep):
        snapshot = plans.current_version(session, require(session, Plan, plan_id))
        return plans.validation(session, snapshot)

    @router.post("/plans/{plan_id}/revise")
    def revise_plan(plan_id: UUID, body: RevisionInput, session: SessionDep):
        return plans.revise(session, require(session, Plan, plan_id, lock=True), body)

    @router.post("/plans/{plan_id}/approve", dependencies=protected)
    def approve_plan(plan_id: UUID, body: Binding, session: SessionDep):
        return plans.approve(session, require(session, Plan, plan_id, lock=True), body)

    @router.post("/plans/{plan_id}/execute", dependencies=protected)
    def execute_plan(plan_id: UUID, body: Binding, session: SessionDep):
        return jobs.job_data(plans.enqueue(session, require(session, Plan, plan_id, lock=True), body))

    @router.post("/plans/{plan_id}/undo")
    def undo_plan(plan_id: UUID, session: SessionDep):
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": WORK_LOCK})
        return plans.undo(session, require(session, Plan, plan_id))

    @router.get("/jobs")
    def list_jobs(session: SessionDep):
        return {"items": [jobs.job_data(job) for job in session.scalars(
            select(Job).order_by(Job.created_at.desc(), Job.id).limit(100))]}

    @router.get("/jobs/{job_id}")
    def job_report(job_id: UUID, session: SessionDep):
        return jobs.report(session, require(session, Job, job_id))

    @router.post("/jobs/{job_id}/recover", dependencies=protected)
    def recover(job_id: UUID):
        return jobs.recover_job(sessions, job_id)

    @router.post("/jobs/{job_id}/cancel", dependencies=protected)
    def cancel(job_id: UUID, session: SessionDep):
        job = require(session, Job, job_id, lock=True)
        if job.status in {"queued", "running"} and not job.cancel_requested:
            job.cancel_requested = True
            jobs.event(session, job, "cancel_requested", "Cancellation requested at the next operation boundary.")
        return jobs.job_data(job)

    app.include_router(router)
    return app
