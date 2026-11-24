from collections import defaultdict, deque
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select

from . import filesystem as fs
from .catalog import current_file, index_file
from .common import Problem, authorized, digest, require
from .db import Approval, Job, Operation, OperationEvent, Plan, PlanVersion, Root, Task


EDITABLE = {"draft", "approved"}
UNFINISHED = {"queued", "running", "needs_recovery"}


def current_version(session, plan):
    return session.get(PlanVersion, (plan.id, plan.version))


def public_item(item):
    return {key: value for key, value in item.items() if key != "source_fingerprint"}


def plan_data(session, plan, *, version=None):
    snapshot = version or current_version(session, plan)
    job_id = None if plan.status in EDITABLE else session.scalar(select(Job.id).where(Job.plan_id == plan.id))
    return {"id": str(plan.id), "task_id": str(plan.task_id) if plan.task_id else None,
            "title": plan.title, "undo_of": str(plan.undo_of) if plan.undo_of else None,
            "version": snapshot.version, "hash": snapshot.hash, "status": plan.status,
            "job_id": str(job_id) if job_id else None,
            "created_at": plan.created_at, "items": [public_item(item) for item in snapshot.items]}


def verify_evidence(session, evidence, *, job_id=None, source_item=None):
    references = evidence if isinstance(evidence, list) else [evidence]
    for reference in references:
        if not isinstance(reference, dict) or not {"file_id", "version"}.intersection(reference):
            continue
        try:
            file_id = UUID(reference["file_id"])
            version = reference["version"]
        except (TypeError, KeyError, AttributeError, ValueError):
            raise Problem(422, "invalid_evidence", "Evidence references require a valid file_id and version.") from None
        if not isinstance(version, str):
            raise Problem(422, "invalid_evidence", "Evidence references require a valid file_id and version.")
        if source_item and str(file_id) == source_item["file_id"] and version == source_item["source_version"]:
            # The move checks this same approved fingerprint while holding the source file.
            continue
        try:
            moved = None if job_id is None else session.scalar(select(Operation.item).where(
                Operation.job_id == job_id, Operation.status == "completed",
                Operation.item["file_id"].astext == str(file_id),
                Operation.item["source_version"].astext == version,
            ))
            if moved is None:
                file, _ = current_file(session, file_id)
                matches = version == file.version
            else:
                root = require(session, Root, moved["target_root_id"])
                matches = fs.fingerprint(authorized(root), moved["target_path"]) == moved["source_fingerprint"]
        except (Problem, fs.FileProblem):
            raise Problem(409, "stale_evidence", "The evidence file is missing or has changed.") from None
        if not matches:
            raise Problem(409, "stale_evidence", "The evidence file version has changed.")


def _make_item(file, target_root, target, reason, evidence, *, retained=None):
    source = {"file_id": str(file.id), "source_root_id": str(file.root_id),
              "source_path": file.relative_path, "source_version": file.version,
              "source_fingerprint": file.fingerprint}
    if retained is None:
        item_id = str(uuid4())
    else:
        item_id = retained["id"]
        if any(retained[key] != value for key, value in source.items()):
            raise Problem(409, "source_changed", "A retained source changed since the previous plan version.",
                          item_id=item_id)
        source = {key: retained[key] for key in source}
    return {"id": item_id, **source, "target_root_id": str(target_root.id),
            "target_path": fs.normalized_relative(target), "reason": reason, "evidence": evidence}


def make_items(session, proposals, *, previous=()):
    items = []
    checked_files = {}
    previous_by_file = defaultdict(deque)
    for item in previous:
        previous_by_file[item["file_id"]].append(item)
    for proposal in proposals:
        if proposal.file_id not in checked_files:
            checked_files[proposal.file_id] = current_file(session, proposal.file_id)[0]
        file = checked_files[proposal.file_id]
        target_root = require(session, Root, proposal.target_root_id)
        candidates = previous_by_file.get(str(file.id))
        retained = candidates.popleft() if candidates else None
        item = _make_item(file, target_root, proposal.target_path, proposal.reason, proposal.evidence,
                          retained=retained)
        verify_evidence(session, proposal.evidence)
        items.append(item)
    return items


def _save_version(session, plan, items):
    snapshot = PlanVersion(plan_id=plan.id, version=plan.version, items=items, hash=digest({
        "plan_id": str(plan.id), "version": plan.version, "title": plan.title, "items": items,
    }))
    session.add(snapshot)
    session.flush()
    return plan_data(session, plan, version=snapshot)


def new_plan(session, task_id, title, items, *, undo_of=None):
    if task_id:
        require(session, Task, task_id)
    plan = Plan(task_id=task_id, title=title, undo_of=undo_of)
    session.add(plan)
    session.flush()
    return _save_version(session, plan, items)


def revise(session, plan, body):
    if body.version != plan.version:
        raise Problem(409, "stale_plan", "The plan version changed. Reload before editing.")
    if plan.status not in EDITABLE:
        raise Problem(409, "plan_locked", "This plan already has an execution job.")
    items = make_items(session, body.items, previous=current_version(session, plan).items)
    plan.version += 1
    plan.status = "draft"
    return _save_version(session, plan, items)


def validation(session, snapshot):
    issues = []
    sources = set()
    targets = []

    def issue(item_id, code, message):
        issues.append({"item_id": item_id, "code": code, "message": message})

    for item in snapshot.items:
        item_id = item["id"]
        before = item["source_fingerprint"]
        source_identity = before["device"], before["inode"]
        if source_identity in sources:
            issue(item_id, "duplicate_source", "The same file appears more than once.")
        sources.add(source_identity)
        source = require(session, Root, item["source_root_id"])
        target = require(session, Root, item["target_root_id"])
        destination = (Path(target.path) / item["target_path"]).as_posix()
        if any(fs.paths_equal(destination, earlier) for earlier, _ in targets):
            issue(item_id, "duplicate_target", "Multiple items use the same destination.")
        targets.append((destination, item_id))
        try:
            fs.check_move(authorized(source), item["source_path"], authorized(target), item["target_path"], before)
            verify_evidence(session, item["evidence"])
        except fs.FileProblem as exc:
            issue(item_id, exc.code, str(exc))
        except Problem as exc:
            issue(item_id, exc.detail["code"], str(exc))

    for destination, item_id in targets:
        if any(len(parent) < len(destination) and destination[len(parent)] == "/"
               and fs.paths_equal(destination[:len(parent)], parent) for parent, _ in targets):
            issue(item_id, "target_conflict", "A destination file would also be a parent directory.")

    return {"valid": not issues, "issues": issues}


def _bound(session, plan, body):
    snapshot = current_version(session, plan)
    if body.version != snapshot.version or body.hash != snapshot.hash:
        raise Problem(409, "stale_plan", "The version or hash does not match the current plan.")
    return snapshot


def _require_valid(session, snapshot):
    result = validation(session, snapshot)
    if not result["valid"]:
        raise Problem(409, "invalid_plan", "Resolve the plan validation issues before continuing.",
                      issues=result["issues"])


def approve(session, plan, body):
    snapshot = _bound(session, plan, body)
    if plan.status not in EDITABLE:
        raise Problem(409, "plan_locked", "This plan already has an execution job.")
    _require_valid(session, snapshot)
    approval = session.get(Approval, (plan.id, plan.version))
    if approval is None:
        session.add(Approval(plan_id=plan.id, version=plan.version, hash=snapshot.hash))
    plan.status = "approved"
    return plan_data(session, plan, version=snapshot)


def enqueue(session, plan, body):
    snapshot = _bound(session, plan, body)
    existing = session.scalar(select(Job).where(Job.plan_id == plan.id))
    if existing is not None:
        return existing
    approval = session.get(Approval, (plan.id, plan.version))
    if plan.status != "approved" or approval is None or approval.hash != snapshot.hash:
        raise Problem(409, "approval_required", "An administrator must approve this plan version first.")
    _require_valid(session, snapshot)
    job = Job(plan_id=plan.id, plan_version=plan.version, plan_hash=snapshot.hash)
    session.add(job)
    session.flush()
    for position, item in enumerate(snapshot.items):
        session.add(Operation(job_id=job.id, position=position, item=item))
    session.add(OperationEvent(job_id=job.id, status="queued", message="Approved plan queued for execution."))
    plan.status = "queued"
    return job


def undo(session, plan):
    job = session.scalar(select(Job).where(Job.plan_id == plan.id))
    if job is None or job.status in UNFINISHED:
        raise Problem(409, "undo_unavailable", "The plan needs a finished execution before it can be undone.")
    operations = session.scalars(select(Operation).where(Operation.job_id == job.id,
                                                         Operation.status == "completed")
                                 .order_by(Operation.position.desc()))
    items = []
    for operation in operations:
        item = operation.item
        original = require(session, Root, item["source_root_id"])
        current = require(session, Root, item["target_root_id"])
        expected = item["source_fingerprint"]
        try:
            fs.check_move(authorized(current), item["target_path"], authorized(original), item["source_path"], expected)
            file = index_file(session, current, item["target_path"], expected=expected)
        except fs.FileProblem as exc:
            raise Problem(409, "undo_conflict", "The completed move cannot be reversed safely.",
                          issues=[{"item_id": item["id"], "code": exc.code, "message": str(exc)}]) from exc
        items.append(_make_item(file, original, item["source_path"],
                                f"Reverse completed operation {item['id']}.", None))
    if not items:
        raise Problem(409, "undo_unavailable", "This execution has no completed moves to reverse.")
    return new_plan(session, plan.task_id, f"Undo {plan.title}", items, undo_of=plan.id)
