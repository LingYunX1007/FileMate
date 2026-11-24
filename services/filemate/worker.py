"""Run the durable filesystem worker with `python -m services.filemate.worker`."""

import logging
import os
import time

from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from . import filesystem as fs
from .common import Problem, require
from .db import Job, Operation, make_sessions
from .jobs import check_binding, event, finish_job, paths_for, reconcile, set_job_status, update_catalog, work_lock
from .plans import verify_evidence


logger = logging.getLogger(__name__)


def next_operation(sessions, job_id):
    with sessions.begin() as session:
        job = require(session, Job, job_id, lock=True)
        pending = list(session.scalars(select(Operation).where(Operation.job_id == job.id,
                                                               Operation.status == "pending")
                                       .order_by(Operation.position)))
        if not pending or job.cancel_requested:
            for operation in pending:
                event(session, job, "cancelled", "Cancelled before starting the move.", operation=operation,
                      code="cancelled")
            finish_job(session, job)
            return None
        snapshot = check_binding(session, job)
        for operation in pending:
            item = operation.item
            if snapshot.items[operation.position] != item:
                raise RuntimeError("The queued operation does not match its approved plan item.")
            try:
                verify_evidence(session, item["evidence"], job_id=job.id, source_item=item)
            except Problem as exc:
                event(session, job, "failed", str(exc), operation=operation, code=exc.detail["code"])
                continue
            paths = paths_for(session, item)
            event(session, job, "prepared", "Move intent persisted before touching the filesystem.",
                  operation=operation)
            return operation.id, paths
        finish_job(session, job)
        return None


def finish_operation(sessions, job_id, operation_id, failure):
    with sessions.begin() as session:
        job = require(session, Job, job_id, lock=True)
        operation = require(session, Operation, operation_id)
        if failure is None:
            event(session, job, "completed", "Moved and verified the destination.", operation=operation)
            update_catalog(session, operation.item)
            return True
        if failure.code == "verify_failed":
            event(session, job, "needs_recovery", str(failure), operation=operation, code=failure.code)
            reconcile(session, job)
            return False
        event(session, job, "failed", str(failure), operation=operation, code=failure.code)
        return True


def run_once(sessions):
    with work_lock(sessions) as lock:
        if lock is None:
            return False
        with sessions.begin() as session:
            interrupted = session.scalar(select(Job).where(Job.status == "running")
                                        .order_by(Job.created_at).with_for_update().limit(1))
            if interrupted is not None:
                reconcile(session, interrupted)
                return True
            job = session.scalar(select(Job).where(Job.status == "queued").order_by(Job.created_at, Job.id)
                                 .with_for_update().limit(1))
            if job is None:
                return False
            check_binding(session, job)
            job_id = job.id
            set_job_status(session, job, "running", "Worker started the approved plan.")

        while selected := next_operation(sessions, job_id):
            operation_id, paths = selected
            try:
                # Check the connection holding the lock after hashing and before each mutation stage.
                fs.move(*paths, before_write=lambda: lock.scalar(text("SELECT 1")))
            except fs.FileProblem as exc:
                failure = exc
            else:
                failure = None
            if not finish_operation(sessions, job_id, operation_id, failure):
                break
        return True


def main():
    logging.basicConfig(level=logging.INFO)
    sessions = make_sessions(os.environ["FILEMATE_DATABASE_URL"])
    try:
        while True:
            try:
                if run_once(sessions):
                    continue
            except OperationalError:
                logger.warning("PostgreSQL is unavailable. The worker will retry before moving files.")
            except Exception:
                logger.exception("An execution was interrupted. The worker will reconcile persisted operations.")
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        sessions.kw["bind"].dispose()


if __name__ == "__main__":
    main()
