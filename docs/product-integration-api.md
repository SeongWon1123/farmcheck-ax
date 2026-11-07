# Product integration API

All /api responses are private, no-store. Bootstrap creates the HttpOnly workspace cookie; subsequent requests must include it. Imported records never enter the public Catalog. Existing review, correction, recheck, stale-response adoption and immutable exports remain authoritative.

## Import
POST /api/imports with JSON (not multipart):
`{expected_workspace_revision: int, image_name: string, image_base64: string, annotation_name: string, annotation_json: string}`.
Response: `{record_id, record_revision, workspace_revision, source: {image_sha256, json_sha256, origin: 'uploaded'}, warnings: string[]}`.
Only plain base64 JPEG, at most 10 MiB / 16 million pixels; JSON at most 1 MiB UTF-8, entire HTTP import body at most 15 MiB. JSON nesting is capped at 32 and 50,000 nodes. EXIF/GPS, XMP, IPTC and JPEG comment metadata are rejected: export a metadata-free JPEG and retry. No transcoding occurs. Names must be plain filenames with matching stems, annotation images.imagepath must exactly equal image_name. No paths or traversal. JSON remains byte-for-byte UTF-8 unchanged. Duplicate keys, nonstandard numbers, invalid Unicode and pathological numeric tokens are rejected. JSON number tokens are capped before int/Decimal parsing at 256 characters and absolute exponent 1024; numeric strings receive the same validation before normalization. Imported scenario fields never enable synthetic fixtures. Dimension mismatches warn, never correct source metadata. Atomic revision conflict: 409 workspace_conflict; duplicate import: 409 duplicate_import. Per-workspace cap: 32 pairs and 128 MiB total source bytes. No global deduplication side channels.

Bootstrap adds capabilities.import_pair=true and model_connection identity. Existing GET /api/records?filter=all includes only the current workspace imports plus public sample records. Detail has server-owned source.synthetic (false for uploaded bytes or server ZIP catalogs, true for public sample SVGs). It also has source.origin='uploaded', source.image_sha256/json_sha256 and warnings; original JSON remains source.raw_json. GET /api/records/{id}/image is cookie-protected private/no-store. Wrong workspace record/group/run/snapshot access is 404.

## Model
GET /api/model/status -> {enabled:true,status:'ready'|'unavailable',model:{profile,name,version,uri,serving_id},latency_ms,checked_at,error?}. Server-only FARM_MODEL_PROFILE allowlist: provided (default original service) or pilot. No arbitrary URLs, endpoint URLs or secrets exposed. Health is bounded and does not perform inference.
POST /api/records/{id}/classify retains {expected_revision}. Uploaded JPEGs work in synthetic catalog mode; sample SVGs do not. Image bytes alone leave the server under an opaque generated filename. Adopted detail.model and model-runs receipts retain model_identity, image_sha256, json_sha256, execution_type='real_inference', elapsed_ms, run_id and revision binding. No annotation labels/captions/paths are sent. Predictions are not gold labels or automatic corrections. Failures and stale replies remain explicit and cannot erase manual decisions.

## Saved reports
GET /api/handovers -> {items:[{snapshot_id,created_at,sha256,workspace_revision}]} newest first. Existing GET /api/handovers/{snapshot_id} and /download?format=json|csv|html are workspace scoped. JSON downloads are the exact immutable capture, including source hashes and model identity, unaffected by subsequent edits or profile changes.

## Operational boundaries
Workspace cookies are opaque bearer capabilities, not user accounts or enterprise authentication. Source blobs and snapshots persist in the existing SQLite database, unencrypted like existing workflow state; no deletion/retention UI is added. Duplicate detection is deliberately conservative: an already imported image OR exact JSON hash rejects the new pair within that workspace, without revision advancement. Historical model rows without provenance are not retroactively relabeled; newly executed inference receives the full identity receipt. Health accepts bounded JSON status ready/ok only and indicates reachability, not model quality. No accuracy or improvement claims are made.

## Verification status
Focused tests were added in tests/test_product_integration.py. They were not executed: this environment cannot spawn the configured Python/uv environment. No installations or access escalation were attempted. Shell git status/diff checks were also blocked by filesystem sandbox permissions; final review used direct source reads. Runtime, lint and type-check results remain unverified.


## 인증키 분리 보완 (2026-11-07)
HTTP 인증은 공개 workspace ID가 아닌 v2 HttpOnly 쿠키로 수행합니다. DB에는 인증키 SHA-256 매핑만 저장합니다. 기존 DB의 이전 쿠키는 bootstrap에서 한 번 교환할 수 있으며 교환 후 원래 식별자는 인증·재취득에 사용할 수 없습니다. 새 작업공간의 공개 식별자는 처음부터 인증 수단이 아닙니다. 출처 ID와 기존 불변 보고서는 변경하지 않습니다. 기존 브라우저는 새로고침한 뒤 보고서를 전달해야 합니다.
