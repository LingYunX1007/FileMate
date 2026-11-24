from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from pathlib import PurePosixPath

from sqlalchemy import delete, exists, func, or_, select

from . import filesystem as fs
from .common import Problem, authorized, digest, now, path_key, require
from .db import DocumentChunk, IndexedFile, ResultSet, Root, Task
from .documents import CONTENT_MESSAGES, Content, extract, make_excerpt


def root_data(row):
    return {"id": str(row.id), "path": row.path, "label": row.label, "scanned_at": row.scanned_at}


def file_data(row):
    modified_at = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=row.mtime_ns // 1000)
    return {"id": str(row.id), "root_id": str(row.root_id), "relative_path": row.relative_path,
            "extension": row.extension, "size": row.size, "mtime_ns": row.mtime_ns,
            "modified_at": modified_at.isoformat(),
            "version": row.version, "present": row.present, "content_status": row.content_status,
            "content_truncated": row.content_truncated}


def task_data(task):
    return {"id": str(task.id), "goal": task.goal, "session_id": task.session_id,
            "created_at": task.created_at}


def result_data(result):
    offset = result.query.get("offset", 0)
    limit = result.query.get("limit", 50)
    returned_count = len(result.items)
    has_more = offset + returned_count < result.total
    return {"id": str(result.id), "root_id": str(result.root_id), "query": result.query,
            "items": result.items, "total": result.total, "created_at": result.created_at,
            "offset": offset, "limit": limit, "returned_count": returned_count,
            "has_more": has_more, "next_offset": offset + returned_count if has_more else None}


def index_file(session, root, relative, existing=None, *, expected=None):
    if existing is None:
        existing = session.scalar(select(IndexedFile).where(IndexedFile.root_id == root.id,
                                                              IndexedFile.relative_path == relative))
    with fs.read_file(authorized(root), relative) as file:
        fingerprint = fs._fingerprint(file)
        if expected is not None and fingerprint != expected:
            raise fs.FileProblem("source_changed", "The file changed before its index could be updated.")
        if existing is not None and existing.fingerprint == fingerprint and existing.relative_path == relative:
            existing.present = True
            existing.path_key = path_key(relative)
            return existing
        extension = PurePosixPath(relative).suffix.lower()
        try:
            content = extract(file.stream, extension, fingerprint["size"])
        except OSError:
            content = Content("parse_error", [])

    if existing is None:
        existing = IndexedFile(root_id=root.id)
        session.add(existing)
    else:
        session.execute(delete(DocumentChunk).where(DocumentChunk.file_id == existing.id))
    existing.relative_path = relative
    existing.path_key = path_key(relative)
    existing.fingerprint = fingerprint
    existing.extension = extension
    existing.present = True
    existing.size = fingerprint["size"]
    existing.mtime_ns = fingerprint["mtime_ns"]
    existing.version = digest({"relative_path": relative, "fingerprint": fingerprint})
    existing.content_status = content.status
    existing.content_truncated = content.truncated
    session.flush()
    for index, (text, location) in enumerate(content.chunks):
        session.add(DocumentChunk(file_id=existing.id, ordinal=index, text=text, location=location))
    return existing


def scan_root(session, root):
    auth_root = authorized(root)
    indexed = {item.relative_path: item for item in session.scalars(select(IndexedFile).where(
        IndexedFile.root_id == root.id))}
    seen = set()
    pending = [()]
    inaccessible = []
    errors = []
    error_count = 0
    scanned = 0

    def record_error(relative, exc):
        nonlocal error_count
        error_count += 1
        if len(errors) < 200:
            errors.append({"relative_path": relative, "code": exc.code, "message": str(exc)})

    while pending:
        parts = pending.pop()
        try:
            with fs.scan_directory(auth_root, parts) as entries:
                for name, is_dir in entries:
                    relative = "/".join((*parts, name))
                    try:
                        fs.relative_parts(relative)
                        if is_dir:
                            pending.append((*parts, name))
                            continue
                        index_file(session, root, relative, indexed.get(relative))
                        seen.add(relative)
                        scanned += 1
                    except fs.FileProblem as exc:
                        record_error(relative, exc)
        except fs.FileProblem as exc:
            if not parts:
                raise
            relative = "/".join(parts)
            if exc.code in {"path_unavailable", "filesystem_error"}:
                inaccessible.append(relative + "/")
            record_error(relative, exc)

    for relative, row in indexed.items():
        if relative not in seen and not any(relative.startswith(prefix) for prefix in inaccessible):
            row.present = False
    root.scanned_at = now()
    return {"root_id": str(root.id), "scanned": scanned, "errors": errors, "error_count": error_count}


def inspect_root(session, root):
    files = list(session.scalars(select(IndexedFile).where(IndexedFile.root_id == root.id, IndexedFile.present)
                                 .order_by(IndexedFile.relative_path, IndexedFile.id)))
    extensions = Counter(item.extension for item in files)
    folders = Counter(item.relative_path.split("/", 1)[0] for item in files if "/" in item.relative_path)
    sample_limit = 30
    items = [file_data(item) for item in files[:sample_limit]]
    truncated = len(files) > len(items)
    return {**root_data(root), "root_id": str(root.id), "file_count": len(files),
            "total_size": sum(item.size for item in files), "extensions": dict(extensions.most_common()),
            "folders": [{"name": name, "file_count": count} for name, count in folders.most_common(40)],
            "items": items, "returned_count": len(items), "sample_limit": sample_limit,
            "truncated": truncated,
            "notice": ("Items are a sample, not a complete file list. Use search with directory and "
                       "follow next_offset until has_more is false before planning a whole directory."
                       if truncated else "All indexed files are included in items.")}


def _epoch_ns(value: date | datetime) -> int:
    when = value if isinstance(value, datetime) else datetime.combine(value, time.min)
    when = when.astimezone(UTC) if when.tzinfo is not None else when.replace(tzinfo=UTC)
    delta = when - datetime(1970, 1, 1, tzinfo=UTC)
    return ((delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds) * 1000


def search(session, body):
    require(session, Root, body.root_id)
    if body.task_id:
        require(session, Task, body.task_id)
    conditions = [IndexedFile.root_id == body.root_id, IndexedFile.present]
    if body.directory:
        conditions.append(IndexedFile.relative_path.startswith(body.directory + "/", autoescape=True))
    if body.extension:
        conditions.append(IndexedFile.extension == "." + body.extension.lower().lstrip("."))
    if body.modified_after is not None:
        conditions.append(IndexedFile.mtime_ns >= _epoch_ns(body.modified_after))
    if body.modified_before is not None:
        conditions.append(IndexedFile.mtime_ns < _epoch_ns(body.modified_before))
    keyword = body.query.strip().lower()
    if keyword:
        conditions.append(or_(
            func.lower(IndexedFile.relative_path).contains(keyword, autoescape=True),
            exists(select(DocumentChunk.file_id).where(DocumentChunk.file_id == IndexedFile.id,
                       func.lower(DocumentChunk.text).contains(keyword, autoescape=True))),
        ))
    total = session.scalar(select(func.count()).select_from(IndexedFile).where(*conditions))
    files = session.scalars(select(IndexedFile).where(*conditions)
                            .order_by(func.lower(IndexedFile.relative_path), IndexedFile.relative_path, IndexedFile.id)
                            .limit(body.limit).offset(body.offset))
    result = ResultSet(root_id=body.root_id, task_id=body.task_id, query=body.model_dump(mode="json"),
                       items=[file_data(file) for file in files], total=total)
    session.add(result)
    session.flush()
    return result_data(result)


def current_file(session, file_id, *, reading_chunks=False):
    if reading_chunks:
        # Keep an excerpt's chunks and version on the same index generation during a scan.
        file = session.scalar(select(IndexedFile).where(IndexedFile.id == file_id).with_for_update(read=True))
        if file is None:
            raise Problem(404, "not_found", "The requested file could not be found.")
    else:
        file = require(session, IndexedFile, file_id)
    if not file.present:
        raise Problem(409, "source_changed", "The file is no longer present in the index.")
    root = require(session, Root, file.root_id)
    if fs.fingerprint(authorized(root), file.relative_path) != file.fingerprint:
        raise Problem(409, "source_changed", "The file has changed. Scan its root again.")
    return file, root


def excerpt(session, file_id, start, limit):
    file, _ = current_file(session, file_id, reading_chunks=True)
    if file.content_status != "ready":
        raise Problem(422, file.content_status, CONTENT_MESSAGES[file.content_status])
    chunks = list(session.scalars(select(DocumentChunk).where(DocumentChunk.file_id == file.id)
                                  .order_by(DocumentChunk.ordinal)))
    return {"file_id": str(file.id), "version": file.version, "relative_path": file.relative_path,
            "truncated": file.content_truncated, **make_excerpt(chunks, start, limit)}
