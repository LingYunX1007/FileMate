import os
from uuid import uuid4

import pytest


@pytest.fixture
def backend():
    from alembic import command
    from alembic.config import Config
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    from services.filemate.api import create_app
    from services.filemate.config import Settings, postgres_url
    from services.filemate.db import make_sessions

    url = os.environ.get("FILEMATE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set FILEMATE_TEST_DATABASE_URL to a disposable PostgreSQL database")
    db_url = postgres_url(url)
    schema = "filemate_test_" + uuid4().hex
    database = create_engine(db_url)
    with database.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_url = make_url(db_url).update_query_dict({"options": f"-csearch_path={schema}"})
    sessions = make_sessions(scoped_url)
    engine = sessions.kw["bind"]
    try:
        with engine.begin() as connection:
            config = Config("alembic.ini")
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        settings = Settings(db_url, "tool-fixture-token", "admin-fixture-token")
        with TestClient(create_app(settings, sessions=sessions)) as client:
            yield sessions, client
    finally:
        engine.dispose()
        with database.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        database.dispose()


ADMIN = {"Authorization": "Bearer admin-fixture-token"}
TOOL = {"Authorization": "Bearer tool-fixture-token"}


def test_inspect_sample_is_explicit_and_directory_pages_build_complete_plan(backend, tmp_path):
    from datetime import datetime

    _, client = backend
    expected = [f"Test/{index:02}.txt" for index in range(12)]
    for folder, count in (("A", 22), ("Test", 12), ("Z", 17)):
        (tmp_path / folder).mkdir()
        for index in range(count):
            (tmp_path / folder / f"{index:02}.txt").write_text("sample", encoding="utf-8")
    root = valid(client.post("/v1/roots", headers=ADMIN, json={"path": str(tmp_path)}))
    valid(client.post(f"/v1/roots/{root['id']}/scan", headers=TOOL))
    overview = valid(client.get(f"/v1/roots/{root['id']}/inspect", headers=TOOL))
    assert overview["file_count"] == 51
    assert sum(item["relative_path"].startswith("Test/") for item in overview["items"]) == 8
    assert overview["returned_count"] == overview["sample_limit"] == 30
    assert overview["truncated"] is True
    assert "search" in overview["notice"].lower()
    task = valid(client.post("/v1/tasks", headers=TOOL, json={"goal": "Organize all Test files"}))
    first = valid(client.post("/v1/search", headers=TOOL, json={
        "root_id": root["id"], "directory": "Test", "limit": 8, "task_id": task["id"]}))
    assert (first["total"], first["offset"], first["limit"], first["returned_count"]) == (12, 0, 8, 8)
    assert first["has_more"] is True and first["next_offset"] == 8
    second = valid(client.post("/v1/search", headers=TOOL, json={
        "root_id": root["id"], "directory": "Test", "limit": 8, "offset": first["next_offset"]}))
    assert second["returned_count"] == 4
    assert second["has_more"] is False and second["next_offset"] is None
    files = first["items"] + second["items"]
    assert [file["relative_path"] for file in files] == expected
    for file in files:
        when = datetime.fromisoformat(file["modified_at"])
        assert when.utcoffset().total_seconds() == 0
        assert abs(when.timestamp() - file["mtime_ns"] / 1e9) < 0.00001
    stored = valid(client.get(f"/v1/tasks/{task['id']}", headers=TOOL))["result_sets"][0]
    assert stored["next_offset"] == 8 and stored["returned_count"] == 8
    plan = valid(client.post("/v1/plans", headers=TOOL, json={"title": "Complete Test folder", "items": [
        {"file_id": file["id"], "target_root_id": root["id"],
         "target_path": "Archive/" + file["relative_path"], "reason": "Keep original names"} for file in files]}))
    assert [item["source_path"] for item in plan["items"]] == expected
    assert [item["target_path"] for item in plan["items"]] == ["Archive/" + path for path in expected]
    assert valid(client.post(f"/v1/plans/{plan['id']}/validate", headers=TOOL))["valid"] is True
    assert all((tmp_path / path).exists() for path in expected)


def test_directory_search_is_recursive_literal_and_boundary_scoped(backend, tmp_path):
    _, client = backend
    paths = ["Test/a.txt", "Test/sub/b.txt", "Test2/c.txt", "Test_%/d.txt", "Test_AX/e.txt"]
    for path in paths:
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("Test shared keyword", encoding="utf-8")
    root = valid(client.post("/v1/roots", headers=ADMIN, json={"path": str(tmp_path)}))
    valid(client.post(f"/v1/roots/{root['id']}/scan", headers=TOOL))
    for directory, expected in (("Test", paths[:2]), ("Test\\sub", [paths[1]]), ("Test_%", [paths[3]]),
                                ("missing", [])):
        result = valid(client.post("/v1/search", headers=TOOL, json={
            "root_id": root["id"], "directory": directory, "query": "shared", "extension": "txt"}))
        assert [item["relative_path"] for item in result["items"]] == expected
        assert result["query"]["directory"] == directory.replace("\\", "/")
    beyond = valid(client.post("/v1/search", headers=TOOL, json={
        "root_id": root["id"], "directory": "Test", "offset": 20}))
    assert beyond["total"] == 2 and beyond["returned_count"] == 0
    assert beyond["has_more"] is False and beyond["next_offset"] is None
    for directory in ("..", "Test/../Z", "/Test", "C:\\Test", "C:Test", "\\\\server\\share", ".", "Test//sub"):
        valid(client.post("/v1/search", headers=TOOL, json={"root_id": root["id"], "directory": directory}), 422)
    overview = valid(client.get(f"/v1/roots/{root['id']}/inspect", headers=TOOL))
    assert overview["truncated"] is False and overview["returned_count"] == 5


def valid(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def fixture(client, tmp_path):
    path = tmp_path / "authorized"
    path.mkdir()
    (path / "contract.txt").write_text("合同条款，一号客户。\nSecond line.", encoding="utf-8")
    root = valid(client.post("/v1/roots", headers=ADMIN, json={"path": str(path), "label": "资料"}))
    valid(client.post(f"/v1/roots/{root['id']}/scan", headers=TOOL))
    task = valid(client.post("/v1/tasks", headers=TOOL, json={"goal": "整理合同", "session_id": "chat-1"}))
    query = {"root_id": root["id"], "task_id": task["id"], "query": "客户", "extension": "TXT"}
    result = valid(client.post("/v1/search", headers=TOOL, json=query))
    assert result["total"] == 1
    return path, root, task, result["items"][0]


def propose(client, root, task, file, target):
    excerpt = valid(client.get(f"/v1/files/{file['id']}/excerpt?limit=15", headers=TOOL))
    assert "合同条款" in excerpt["text"]
    item = {"file_id": file["id"], "target_root_id": root["id"], "target_path": target,
            "reason": "匹配合同条款", "evidence": {"file_id": file["id"],
            "version": excerpt["version"], "location": excerpt["location"]}}
    plan = valid(client.post("/v1/plans", headers=TOOL,
                             json={"task_id": task["id"], "title": "整理合同", "items": [item]}))
    return plan, item


def binding(plan):
    return {field: plan[field] for field in ("version", "hash")}


def test_version_bound_approval_and_undo(backend, tmp_path):
    from services.filemate.worker import run_once

    sessions, client = backend
    assert client.get("/v1/roots").status_code == 401
    assert client.post("/v1/roots", headers=TOOL, json={"path": str(tmp_path)}).status_code == 403
    path, root, task, file = fixture(client, tmp_path)
    plan, item = propose(client, root, task, file, "unused.txt")
    endpoint = f"/v1/plans/{plan['id']}"
    assert client.post(endpoint + "/approve", headers=TOOL, json=binding(plan)).status_code == 403
    assert client.post(endpoint + "/execute", headers=ADMIN, json=binding(plan)).status_code == 409
    valid(client.post(endpoint + "/approve", headers=ADMIN, json=binding(plan)))
    item["target_path"] = "客户/2026/合同.txt"
    revised = valid(client.post(endpoint + "/revise", headers=TOOL,
                                json={"version": plan["version"], "items": [item]}))
    assert revised["version"] == plan["version"] + 1
    assert revised["hash"] != plan["hash"]
    assert revised["status"] == "draft"
    assert client.post(endpoint + "/execute", headers=ADMIN, json=binding(plan)).status_code == 409
    assert client.post(endpoint + "/execute", headers=ADMIN, json=binding(revised)).status_code == 409
    valid(client.post(endpoint + "/approve", headers=ADMIN, json=binding(revised)))
    assert valid(client.post(endpoint + "/validate", headers=TOOL))["valid"]
    job = valid(client.post(endpoint + "/execute", headers=ADMIN, json=binding(revised)))
    assert valid(client.post(endpoint + "/execute", headers=ADMIN, json=binding(revised)))["id"] == job["id"]
    assert (path / "contract.txt").exists()
    assert run_once(sessions)
    report = valid(client.get(f"/v1/jobs/{job['id']}", headers=TOOL))
    assert report["status"] == "completed"
    assert {event["status"] for event in report["events"]} >= {"prepared", "completed"}
    target = path / "客户" / "2026" / "合同.txt"
    assert target.read_bytes().startswith("合同".encode())
    assert not (path / "contract.txt").exists()
    context = valid(client.get(f"/v1/tasks/{task['id']}", headers=TOOL))
    assert context["result_sets"][0]["items"][0]["relative_path"] == "contract.txt"
    assert context["plans"][0]["version"] == revised["version"]

    # An occupied original path blocks undo and survives unmodified.
    (path / "contract.txt").write_text("unrelated", encoding="utf-8")
    blocked = client.post(endpoint + "/undo", headers=TOOL)
    assert blocked.status_code == 409, blocked.text
    assert (path / "contract.txt").read_text(encoding="utf-8") == "unrelated"
    (path / "contract.txt").unlink()
    reverse = valid(client.post(endpoint + "/undo", headers=TOOL))
    assert reverse["id"] != plan["id"]
    assert reverse["status"] == "draft"
    assert reverse["items"][0]["target_path"] == "contract.txt"
    reverse_endpoint = f"/v1/plans/{reverse['id']}"
    assert client.post(reverse_endpoint + "/execute", headers=ADMIN,
                       json=binding(reverse)).status_code == 409
    valid(client.post(reverse_endpoint + "/approve", headers=ADMIN, json=binding(reverse)))
    valid(client.post(reverse_endpoint + "/execute", headers=ADMIN, json=binding(reverse)))
    assert run_once(sessions)
    assert (path / "contract.txt").read_bytes().startswith("合同".encode())
    assert not target.exists()


def test_worker_blocks_changed_source_after_queueing(backend, tmp_path):
    from services.filemate.worker import run_once

    sessions, client = backend
    path, root, task, file = fixture(client, tmp_path)
    plan, _ = propose(client, root, task, file, "target.txt")
    endpoint = f"/v1/plans/{plan['id']}"
    valid(client.post(endpoint + "/approve", headers=ADMIN, json=binding(plan)))
    job = valid(client.post(endpoint + "/execute", headers=ADMIN, json=binding(plan)))
    (path / "contract.txt").write_text("content changed", encoding="utf-8")
    assert run_once(sessions)
    report = valid(client.get(f"/v1/jobs/{job['id']}", headers=TOOL))
    assert report["status"] == "failed"
    assert "source_changed" in {event["code"] for event in report["events"]}
    assert (path / "contract.txt").read_text() == "content changed"
    assert not (path / "target.txt").exists()


def test_prepared_move_is_reconciled_after_crash_without_retry(backend, tmp_path, monkeypatch):
    from services.filemate import worker

    sessions, client = backend
    path, root, task, file = fixture(client, tmp_path)
    plan, _ = propose(client, root, task, file, "target.txt")
    endpoint = f"/v1/plans/{plan['id']}"
    valid(client.post(endpoint + "/approve", headers=ADMIN, json=binding(plan)))
    job = valid(client.post(endpoint + "/execute", headers=ADMIN, json=binding(plan)))

    def crash(*_args, **_kwargs):
        raise RuntimeError("crash before recording completed move")

    with monkeypatch.context() as patch:
        patch.setattr(worker, "finish_operation", crash)
        with pytest.raises(RuntimeError, match="crash before"):
            worker.run_once(sessions)
    assert (path / "target.txt").exists()
    assert not (path / "contract.txt").exists()
    job_path = f"/v1/jobs/{job['id']}"
    assert valid(client.get(job_path, headers=TOOL))["items"][0]["status"] == "prepared"
    assert client.post(job_path + "/recover", headers=TOOL).status_code == 403
    assert valid(client.post(job_path + "/recover", headers=ADMIN))["status"] == "completed"
    report = valid(client.get(job_path, headers=TOOL))
    assert report["items"][0]["status"] == "completed"
    assert (path / "target.txt").exists()
    assert not worker.run_once(sessions)


def test_queued_job_can_be_cancelled_at_operation_boundary(backend, tmp_path):
    from services.filemate.worker import run_once

    sessions, client = backend
    path, root, task, file = fixture(client, tmp_path)
    plan, _ = propose(client, root, task, file, "target.txt")
    endpoint = f"/v1/plans/{plan['id']}"
    valid(client.post(endpoint + "/approve", headers=ADMIN, json=binding(plan)))
    job = valid(client.post(endpoint + "/execute", headers=ADMIN, json=binding(plan)))
    job_path = f"/v1/jobs/{job['id']}"
    assert client.post(job_path + "/cancel", headers=TOOL).status_code == 403
    assert valid(client.post(job_path + "/cancel", headers=ADMIN))["cancel_requested"]
    run_once(sessions)
    assert valid(client.get(job_path, headers=TOOL))["status"] == "cancelled"
    assert (path / "contract.txt").exists()
    assert not (path / "target.txt").exists()


def test_non_postgresql_connection_is_rejected():
    from services.filemate.config import postgres_url

    with pytest.raises(ValueError, match="PostgreSQL"):
        postgres_url("sqlite:///filemate.sqlite")

def test_kernel_rename_never_overwrites_destination_created_after_check(tmp_path, monkeypatch):
    from services.filemate import filesystem as fs

    source = tmp_path / "source.txt"
    source.write_bytes(b"to move")
    target = tmp_path / "target.txt"
    root = fs.authorize_root(str(tmp_path))
    expected = fs.fingerprint(root, source.name)
    rename = fs._rename

    def concurrent_creation(*args):
        target.write_bytes(b"external")
        return rename(*args)

    monkeypatch.setattr(fs, "_rename", concurrent_creation)
    with pytest.raises(fs.FileProblem):
        fs.move(root, source.name, root, target.name, expected)
    assert source.read_bytes() == b"to move"
    assert target.read_bytes() == b"external"


def test_pdf_docx_locations_and_scanned_pdf_status(backend, tmp_path):
    from docx import Document
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    _, client = backend
    word = Document()
    word.add_paragraph("测试文档位置")
    word.add_table(1, 1).cell(0, 0).text = "合同附件"
    word.save(tmp_path / "memo.docx")

    pdf = PdfWriter()
    page = pdf.add_blank_page(width=300, height=300)
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): DictionaryObject({
            NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica")})})})
    contents = DecodedStreamObject()
    contents.set_data(b"BT /F1 12 Tf 10 50 Td (Invoice Activity) Tj ET")
    page[NameObject("/Contents")] = contents
    pdf.write(tmp_path / "invoice.pdf")
    blank = PdfWriter()
    blank.add_blank_page(width=300, height=300)
    blank.write(tmp_path / "scanned.pdf")

    root = valid(client.post("/v1/roots", headers=ADMIN, json={"path": str(tmp_path)}))
    scan = valid(client.post(f"/v1/roots/{root['id']}/scan", headers=TOOL))
    assert scan["scanned"] == 3
    assert scan["errors"] == []
    result = valid(client.post("/v1/search", headers=TOOL, json={"root_id": root["id"]}))
    ids = {item["relative_path"]: item["id"] for item in result["items"]}
    assert list(ids) == ["invoice.pdf", "memo.docx", "scanned.pdf"]
    for filename, text, kind in (("invoice.pdf", "Invoice Activity", "page"),
                                 ("memo.docx", "测试文档位置", "paragraph")):
        excerpt = valid(client.get(f"/v1/files/{ids[filename]}/excerpt", headers=TOOL))
        assert text in excerpt["text"]
        assert excerpt["location"]["kind"] == kind
    image_only = valid(client.get(f"/v1/files/{ids['scanned.pdf']}/excerpt", headers=TOOL), 422)
    assert image_only["detail"]["code"] == "needs_ocr"
    summary = valid(client.get(f"/v1/roots/{root['id']}/inspect", headers=TOOL))
    assert summary["extensions"] == {".pdf": 2, ".docx": 1}


def test_stale_evidence_rejected_when_evidence_differs_from_source(backend, tmp_path):
    _, client = backend
    path, root, task, source = fixture(client, tmp_path)
    evidence_path = path / "evidence.md"
    evidence_path.write_text("Original evidence", encoding="utf-8")
    valid(client.post(f"/v1/roots/{root['id']}/scan", headers=TOOL))
    result = valid(client.post("/v1/search", headers=TOOL, json={"root_id": root["id"], "extension": "md"}))
    evidence_file = result["items"][0]
    evidence_path.write_text("Changed evidence", encoding="utf-8")
    proposal = {"file_id": source["id"], "target_root_id": root["id"],
                "target_path": "target.txt", "reason": "Use independent evidence.",
                "evidence": {"file_id": evidence_file["id"], "version": evidence_file["version"]}}
    failure = valid(client.post("/v1/plans", headers=TOOL, json={
        "task_id": task["id"], "title": "Evidence check", "items": [proposal],
    }), 409)
    assert failure["detail"]["code"] == "stale_evidence"

def test_interrupted_worker_leaves_unchanged_source_and_does_not_retry(backend, tmp_path, monkeypatch):
    from services.filemate import worker

    sessions, client = backend
    path, root, task, file = fixture(client, tmp_path)
    plan, _ = propose(client, root, task, file, "target.txt")
    endpoint = f"/v1/plans/{plan['id']}"
    valid(client.post(endpoint + "/approve", headers=ADMIN, json=binding(plan)))
    job = valid(client.post(endpoint + "/execute", headers=ADMIN, json=binding(plan)))

    def crash(*_args, **_kwargs):
        raise RuntimeError("interrupted before move")

    with monkeypatch.context() as patch:
        patch.setattr(worker.fs, "move", crash)
        with pytest.raises(RuntimeError, match="interrupted before"):
            worker.run_once(sessions)

    assert worker.run_once(sessions)
    report = valid(client.get(f"/v1/jobs/{job['id']}", headers=TOOL))
    assert report["status"] == "failed"
    assert report["items"][0]["code"] == "not_applied"
    assert (path / "contract.txt").exists()
    assert not (path / "target.txt").exists()
    assert not worker.run_once(sessions)

def test_task_plan_exposes_the_queued_job_id(backend, tmp_path):
    _, client = backend
    _, root, task, file = fixture(client, tmp_path)
    plan, _ = propose(client, root, task, file, "target.txt")
    endpoint = f"/v1/plans/{plan['id']}"
    valid(client.post(endpoint + "/approve", headers=ADMIN, json=binding(plan)))
    job = valid(client.post(endpoint + "/execute", headers=ADMIN, json=binding(plan)))
    detail = valid(client.get(endpoint, headers=TOOL))
    context = valid(client.get(f"/v1/tasks/{task['id']}", headers=TOOL))
    assert detail["job_id"] == job["id"]
    assert context["plans"][0]["job_id"] == job["id"]


def test_revision_retains_original_source_binding_and_rejects_rescanned_changes(backend, tmp_path):
    _, client = backend
    path, root, task, file = fixture(client, tmp_path)
    other_path = path / "other.txt"
    other_path.write_text("Other file stays unchanged", encoding="utf-8")
    valid(client.post(f"/v1/roots/{root['id']}/scan", headers=TOOL))
    other_search = valid(client.post("/v1/search", headers=TOOL,
                                     json={"root_id": root["id"], "query": "other.txt"}))
    other_file = other_search["items"][0]
    items = [
        {"file_id": file["id"], "target_root_id": root["id"], "target_path": "one.txt", "reason": "First"},
        {"file_id": other_file["id"], "target_root_id": root["id"], "target_path": "two.txt", "reason": "Second"},
    ]
    plan = valid(client.post("/v1/plans", headers=TOOL,
                             json={"task_id": task["id"], "title": "Two files", "items": items}))
    endpoint = f"/v1/plans/{plan['id']}"
    (path / "contract.txt").write_text("Unreviewed replacement", encoding="utf-8")
    valid(client.post(f"/v1/roots/{root['id']}/scan", headers=TOOL))
    items[1]["target_path"] = "edited.txt"
    stale = valid(client.post(endpoint + "/revise", headers=TOOL,
                              json={"version": plan["version"], "items": items}), 409)
    assert stale["detail"]["code"] == "source_changed"
    assert valid(client.get(endpoint, headers=TOOL))["hash"] == plan["hash"]
    revised = valid(client.post(endpoint + "/revise", headers=TOOL,
                                json={"version": plan["version"], "items": [items[1]]}))
    assert revised["items"][0]["id"] == plan["items"][1]["id"]
    assert revised["items"][0]["source_version"] == plan["items"][1]["source_version"]
    assert revised["items"][0]["target_path"] == "edited.txt"

def test_ntfs_spelling_collisions_do_not_merge_distinct_files(backend, tmp_path):
    _, client = backend
    (tmp_path / "Straße.txt").write_text("Unicode spelling", encoding="utf-8")
    (tmp_path / "Strasse.txt").write_text("ASCII spelling", encoding="utf-8")
    assert len(list(tmp_path.iterdir())) == 2
    root = valid(client.post("/v1/roots", headers=ADMIN, json={"path": str(tmp_path)}))
    valid(client.post(f"/v1/roots/{root['id']}/scan", headers=TOOL))
    all_files = valid(client.post("/v1/search", headers=TOOL, json={"root_id": root["id"]}))
    assert all_files["total"] == 2
    paths = {item["relative_path"]: item["id"] for item in all_files["items"]}
    assert set(paths) == {"Straße.txt", "Strasse.txt"}
    assert len(set(paths.values())) == 2
    for word, filename in (("Unicode spelling", "Straße.txt"), ("ASCII spelling", "Strasse.txt")):
        result = valid(client.post("/v1/search", headers=TOOL, json={"root_id": root["id"], "query": word}))
        assert [item["id"] for item in result["items"]] == [paths[filename]]


def test_batch_move_preserves_distinct_ntfs_names_and_contents(backend, tmp_path):
    from services.filemate.worker import run_once

    sessions, client = backend
    contents = {"Straße.txt": "Unicode source", "Strasse.txt": "ASCII source"}
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    for filename, value in contents.items():
        (source_dir / filename).write_text(value, encoding="utf-8")
    root = valid(client.post("/v1/roots", headers=ADMIN, json={"path": str(tmp_path)}))
    valid(client.post(f"/v1/roots/{root['id']}/scan", headers=TOOL))
    result = valid(client.post("/v1/search", headers=TOOL, json={"root_id": root["id"]}))
    items = [{"file_id": file["id"], "target_root_id": root["id"],
              "target_path": file["relative_path"].replace("source/", "archive/"),
              "reason": "Archive the file under its original name."} for file in result["items"]]
    plan = valid(client.post("/v1/plans", headers=TOOL, json={"title": "Archive both spellings", "items": items}))
    endpoint = f"/v1/plans/{plan['id']}"
    valid(client.post(endpoint + "/approve", headers=ADMIN, json=binding(plan)))
    job = valid(client.post(endpoint + "/execute", headers=ADMIN, json=binding(plan)))
    assert run_once(sessions)
    report = valid(client.get(f"/v1/jobs/{job['id']}", headers=TOOL))
    assert report["status"] == "completed"
    assert not list(source_dir.iterdir())
    for filename, value in contents.items():
        assert (tmp_path / "archive" / filename).read_text(encoding="utf-8") == value


@pytest.mark.skipif(os.name != "nt", reason="Windows ordinal path matching")
@pytest.mark.parametrize(("targets", "issue"), [
    (("archive/Report.txt", "ARCHIVE/report.TXT"), "duplicate_target"),
    (("archive/Reports", "ARCHIVE/reports/summary.txt"), "target_conflict"),
])
def test_plan_rejects_case_insensitive_windows_target_conflicts(backend, tmp_path, targets, issue):
    _, client = backend
    for filename in ("first.txt", "second.txt"):
        (tmp_path / filename).write_text(filename, encoding="utf-8")
    root = valid(client.post("/v1/roots", headers=ADMIN, json={"path": str(tmp_path)}))
    valid(client.post(f"/v1/roots/{root['id']}/scan", headers=TOOL))
    result = valid(client.post("/v1/search", headers=TOOL, json={"root_id": root["id"]}))
    items = [{"file_id": file["id"], "target_root_id": root["id"], "target_path": target,
              "reason": "Validate target paths."} for file, target in zip(result["items"], targets, strict=True)]
    plan = valid(client.post("/v1/plans", headers=TOOL, json={"title": "Check collisions", "items": items}))
    rejected = valid(client.post(f"/v1/plans/{plan['id']}/approve", headers=ADMIN, json=binding(plan)), 409)
    assert issue in {item["code"] for item in rejected["detail"]["issues"]}


def test_excerpt_location_can_reopen_later_chunk(backend, tmp_path):
    from docx import Document

    _, client = backend
    document = Document()
    document.add_paragraph("This is the first paragraph.")
    document.add_paragraph("This is the cited paragraph.")
    document.save(tmp_path / "memo.docx")
    root = valid(client.post("/v1/roots", headers=ADMIN, json={"path": str(tmp_path)}))
    valid(client.post(f"/v1/roots/{root['id']}/scan", headers=TOOL))
    result = valid(client.post("/v1/search", headers=TOOL, json={"root_id": root["id"]}))
    endpoint = f"/v1/files/{result['items'][0]['id']}/excerpt"
    full = valid(client.get(endpoint, headers=TOOL))
    location = full["locations"][1]
    assert location["start"] == 30
    assert location["local_start"] == 0
    cited = valid(client.get(endpoint, params={"start": location["start"], "limit": 17}, headers=TOOL))
    assert cited["text"] == "This is the cited"


def test_session_identifier_can_find_previous_tasks(backend):
    _, client = backend
    earliest = valid(client.post("/v1/tasks", headers=TOOL, json={"goal": "First", "session_id": "chat-1"}))
    valid(client.post("/v1/tasks", headers=TOOL, json={"goal": "Independent", "session_id": "chat-2"}))
    newest = valid(client.post("/v1/tasks", headers=TOOL, json={"goal": "Second", "session_id": "chat-1"}))
    result = valid(client.get("/v1/tasks", headers=TOOL, params={"session_id": "chat-1"}))
    assert [item["id"] for item in result["items"]] == [newest["id"], earliest["id"]]


def test_worker_blocks_external_evidence_change_after_queueing(backend, tmp_path):
    from services.filemate.worker import run_once

    sessions, client = backend
    path, root, task, source = fixture(client, tmp_path)
    key = path / "evidence.md"
    key.write_text("Supporting evidence", encoding="utf-8")
    valid(client.post(f"/v1/roots/{root['id']}/scan", headers=TOOL))
    found = valid(client.post("/v1/search", headers=TOOL, json={"root_id": root["id"], "extension": "md"}))
    evidence = found["items"][0]
    proposal = {"file_id": source["id"], "target_root_id": root["id"], "target_path": "target.txt",
                "reason": "Requires independently cited evidence.",
                "evidence": {"file_id": evidence["id"], "version": evidence["version"]}}
    plan = valid(client.post("/v1/plans", headers=TOOL,
                             json={"task_id": task["id"], "title": "Evidence guard", "items": [proposal]}))
    endpoint = f"/v1/plans/{plan['id']}"
    valid(client.post(endpoint + "/approve", headers=ADMIN, json=binding(plan)))
    job = valid(client.post(endpoint + "/execute", headers=ADMIN, json=binding(plan)))
    key.write_text("Changed after queueing", encoding="utf-8")
    assert run_once(sessions)
    report = valid(client.get(f"/v1/jobs/{job['id']}", headers=TOOL))
    assert report["status"] == "failed"
    assert report["items"][0]["code"] == "stale_evidence"
    assert (path / "contract.txt").exists()
    assert not (path / "target.txt").exists()


def test_worker_can_use_evidence_moved_earlier_in_same_job(backend, tmp_path):
    from services.filemate.worker import run_once

    sessions, client = backend
    path, root, task, evidence = fixture(client, tmp_path)
    (path / "other.txt").write_text("Other source", encoding="utf-8")
    valid(client.post(f"/v1/roots/{root['id']}/scan", headers=TOOL))
    found = valid(client.post("/v1/search", headers=TOOL, json={"root_id": root["id"], "query": "other.txt"}))
    other = found["items"][0]
    proposals = [
        {"file_id": evidence["id"], "target_root_id": root["id"], "target_path": "archive/contract.txt",
         "reason": "Organize evidence"},
        {"file_id": other["id"], "target_root_id": root["id"], "target_path": "archive/other.txt",
         "reason": "Use evidence organized by this plan",
         "evidence": {"file_id": evidence["id"], "version": evidence["version"]}},
    ]
    plan = valid(client.post("/v1/plans", headers=TOOL,
                             json={"task_id": task["id"], "title": "Move evidence", "items": proposals}))
    endpoint = f"/v1/plans/{plan['id']}"
    valid(client.post(endpoint + "/approve", headers=ADMIN, json=binding(plan)))
    job = valid(client.post(endpoint + "/execute", headers=ADMIN, json=binding(plan)))
    assert run_once(sessions)
    report = valid(client.get(f"/v1/jobs/{job['id']}", headers=TOOL))
    assert report["status"] == "completed"
    assert (path / "archive" / "contract.txt").exists()
    assert (path / "archive" / "other.txt").exists()
