# Internal API contract v1

Base `/v1`. JSON. UUID identifiers. Errors `{detail:{code,message}}`.
Bearer `FILEMATE_TOOL_TOKEN` for read/investigation/planning; bearer `FILEMATE_ADMIN_TOKEN` for UI management and execution. Admin can also read. Neither token is returned to browser/model. Backend only loopback. Host authenticates user and enforces same-origin mutations, then calls admin endpoints. Agent tools never receive admin token.

- GET /roots -> {items:[{id,path,label}]}; POST /roots {path,label} admin
- POST /roots/{id}/scan -> {root_id,scanned,errors}
- GET /roots/{id}/inspect -> root summary with file_count, items (first 30 samples), returned_count, sample_limit, truncated, notice. folders is a bounded summary, not a complete listing.
- POST /tasks {goal,session_id?} -> {id,...}
- GET /tasks?session_id=... -> {items:[latest session tasks]} for resume when task ID is absent
- GET /tasks/{id} -> task including latest result sets/plans
- POST /search {root_id,directory:'',query:'',extension?,limit:50,offset:0,task_id?,modified_after?,modified_before?} -> {id,items:[{id,relative_path,size,mtime_ns,modified_at,...}],total,offset,limit,returned_count,has_more,next_offset}
- GET /files/{id} -> metadata
- GET /files/{id}/excerpt?start=0&limit=4000 -> {file_id,version,text,location,...}
- POST /plans {task_id?,title,items:[{file_id,target_root_id,target_path,reason,evidence?}]} -> plan
- GET /plans -> {items:[plans]}; GET /plans/{id} -> plan with id,version,hash,status,items
- POST /plans/{id}/validate -> {valid,issues:[{code,message,item_id?}]}
- POST /plans/{id}/revise {version,items:[same shape]} -> new plan version
- POST /plans/{id}/approve {version,hash} admin -> approved plan
- POST /plans/{id}/execute {version,hash} admin -> {id,status,plan_id}
- POST /plans/{id}/undo -> new unapproved reverse plan
- GET /jobs -> {items:[jobs]}; GET /jobs/{id} -> job with operation items/events
- POST /jobs/{id}/recover admin -> recovery status
- POST /jobs/{id}/cancel admin -> cancellation request (at operation boundaries)

No tool executes arbitrary code or arbitrary path reads. Root registration admin-only; tools can scan existing roots. Jobs run via separate worker. Approval and execution are separate UI commands; duplicate execution returns existing job. All writes version-checked.

`directory` is a validated relative directory using the indexed spelling, recursively matching only paths beneath `directory/` (Test excludes Test2). Empty means the whole authorized root. Backslashes normalize to slashes; absolute/traversal paths are rejected. Query may be empty for full enumeration. Page limit is 1..200; continue with `next_offset` until `has_more` is false. Pagination is over the current index, not a cross-request snapshot; after a concurrent scan, recount or restart enumeration if inconsistent.

`modified_at` is ISO 8601 UTC, while `mtime_ns` remains for compatibility. `target_path` is relative to `target_root_id`, including the destination filename: authorized root `D:/资料` + `Test/Word文档/a.docx` means `D:/资料/Test/Word文档/a.docx`.

Host-only GET `/filemate/generation-status` uses dsh owner authentication (not business bearer tokens); returns at most 20 `{sessionId,attemptId,reason,stoppedAt}` stop notices, no prompts or credentials. Other methods return 405. Notices are process-local; the cancellation reason is durable in dsh's `turn/end` record. Browser dismissal includes stop time because dsh attempt IDs can repeat after agent reattachment.
