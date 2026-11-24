from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import select, text

from . import filesystem as fs
from .catalog import index_file
from .common import Problem, authorized, now, require
from .db import Approval, IndexedFile, Job, Operation, OperationEvent, Plan, PlanVersion, Root, WORK_LOCK
from .plans import UNFINISHED, public_item


def job_data(job):
    return {"id": str(job.id), "plan_id": str(job.plan_id), "version": job.plan_version,
            "hash": job.plan_hash, "status": job.status, "cancel_requested": job.cancel_requested,
            "created_at": job.created_at, "finished_at": job.finished_at}


def operation_data(operation):
    return {**public_item(operation.item), "id": str(operation.id), "item_id": operation.item["id"],
            "position": operation.position, "status": operation.status,
            "code": operation.code, "message": operation.message}


def report(session, job):
    items = session.scalars(select(Operation).where(Operation.job_id == job.id).order_by(Operation.position))
    events = session.scalars(select(OperationEvent).where(OperationEvent.job_id == job.id).order_by(OperationEvent.id))
    return {**job_data(job), "items": [operation_data(item) for item in items], "events": [
        {"id": event.id, "operation_id": str(event.operation_id) if event.operation_id else None,
         "status": event.status, "code": event.code, "message": event.message,
         "created_at": event.created_at}
        for event in events
    ]}


@contextmanager
def work_lock(sessions):
    engine = sessions.kw["bind"]
    with engine.connect() as lock:
        acquired = lock.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": WORK_LOCK})
        lock.commit()
        try:
            yield lock if acquired else None
        finally:
            if acquired and not lock.invalidated:
                lock.rollback()
                lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": WORK_LOCK})
                lock.commit()


def event(session, job, status, message, *, operation=None, code=None):
    if operation is not None:
        operation.status = status
        operation.code = code
        operation.message = message
    session.add(OperationEvent(job_id=job.id, operation_id=operation.id if operation else None,
                               status=status, code=code, message=message))


def set_job_status(session, job, status, message):
    job.status = status
    job.finished_at = now() if status not in UNFINISHED else None
    require(session, Plan, job.plan_id).status = status
    event(session, job, status, message)


def finish_job(session, job):
    operations = list(session.scalars(select(Operation).where(Operation.job_id == job.id)
                                      .order_by(Operation.position)))
    statuses = {operation.status for operation in operations}
    if statuses & {"prepared", "needs_recovery"}:
        status, message = "needs_recovery", "One or more moves require filesystem reconciliation."
    elif statuses == {"completed"}:
        status, message = "completed", "All moves were verified."
    elif "completed" in statuses and "failed" in statuses:
        status, message = "partial_failure", "Some moves completed and others failed."
    elif "cancelled" in statuses and job.cancel_requested:
        status, message = "cancelled", "Execution stopped at an operation boundary."
    elif "completed" in statuses:
        status, message = "partial_failure", "Some moves completed before execution was interrupted."
    else:
        status, message = "failed", "No moves completed."
    set_job_status(session, job, status, message)


def check_binding(session, job):
    snapshot = session.get(PlanVersion, (job.plan_id, job.plan_version))
    approval = session.get(Approval, (job.plan_id, job.plan_version))
    plan = require(session, Plan, job.plan_id)
    if (snapshot is None or approval is None or plan.version != job.plan_version
            or job.plan_hash != snapshot.hash or snapshot.hash != approval.hash):
        raise RuntimeError("The queued execution does not match its durable approval.")
    return snapshot


def paths_for(session, item):
    return (authorized(require(session, Root, item["source_root_id"])), item["source_path"],
            authorized(require(session, Root, item["target_root_id"])), item["target_path"],
            item["source_fingerprint"])


def update_catalog(session, item):
    source = session.get(IndexedFile, UUID(item["file_id"]))
    if source is not None and source.fingerprint == item["source_fingerprint"]:
        source.present = False
    target_root = require(session, Root, item["target_root_id"])
    try:
        index_file(session, target_root, item["target_path"], expected=item["source_fingerprint"])
    except fs.FileProblem:
        # A subsequent external file change can leave the catalog pending its next scan.
        pass


def reconcile(session, job):
    if job.status not in {"running", "needs_recovery"}:
        return job
    operations = list(session.scalars(select(Operation).where(Operation.job_id == job.id)
                                      .order_by(Operation.position)))
    for operation in operations:
        if operation.status in {"prepared", "needs_recovery"}:
            state = fs.recover(*paths_for(session, operation.item))
            if state == "applied":
                event(session, job, "completed", "Confirmed the prepared move by identity and content.",
                      operation=operation, code="recovered")
                update_catalog(session, operation.item)
            elif state == "not_applied":
                event(session, job, "failed", "The prepared move did not occur. It was not retried.",
                      operation=operation, code="not_applied")
            else:
                event(session, job, "needs_recovery", "The source and destination require manual inspection.",
                      operation=operation, code="ambiguous")
        elif operation.status == "pending":
            event(session, job, "cancelled", "Skipped after interrupted execution.", operation=operation,
                  code="interrupted")
    finish_job(session, job)
    return job


def recover_job(sessions, job_id):
    with work_lock(sessions) as lock:
        if lock is None:
            raise Problem(409, "worker_busy", "File operations are in progress. Try recovery again later.")
        with sessions.begin() as session:
            return job_data(reconcile(session, require(session, Job, job_id, lock=True)))
