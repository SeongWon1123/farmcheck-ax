# FarmCheck UI <-> API 응답 계약 (프런트 기준)

프런트는 아래 형태를 기대한다. 모든 경로는 `APP_BASE_PATH` 기준 상대 경로. 오류는 `{code, message, details}` JSON.
금지: 프런트 목업 응답으로 실제 fetch 대체.

## 정적 자원
- `GET {base}/` 은 `static/index.html` 을 반환하며 `<meta name="farmcheck-base-path" content="__BASE_PATH__">` 의 `__BASE_PATH__` 를 APP_BASE_PATH(예 `/farm`, 루트면 빈 문자열)로 치환한다. 치환이 안 되면 프런트는 빈 base 로 동작한다.
- HTML 은 `static/style.css`, `static/app.js` 를 **상대 경로**로 참조하므로 `{base}/` (끝 슬래시 포함) 로 제공하고 `{base}/static/*` 로 마운트한다.

## 공통
- 변경 응답(POST review/corrections/recheck/classify)은 **갱신된 레코드 상세**(GET 상세와 같은 스키마)를 200으로 반환한다.
- 409 `{code:"revision_conflict", message, details:{current_revision?, current_workspace_revision?}}`: 프런트는 최신본 다시 불러오기를 안내한다. 입력 초안은 유지.
- 검증 실패 400/422 `{code, message, details}`; 모델 실패는 200 + `model.status="failed"` 또는 502/503 오류 둘 다 표시 가능.
- 시각·숫자는 서버가 준 문자열을 그대로 표시한다(Decimal 은 문자열).

## GET /api/bootstrap
```json
{"workspace_revision":12,"mode":"synthetic|green_tea",
 "summary":{"total":3872,"candidates":33,"held":1,"needs_review":2,"resolved":0,
            "baseline_records":33,"baseline_groups":24,"current_candidate_groups":24},
 "rule_registry":[{"rule_id":"PH_SCREEN","version":"1","fields":["environment.ph"],
   "assumed_unit":null,"rationale":"...","not_concluded":"...","next_question":"..."}],
 "agent_status":{"status":"disabled_by_owner","message":"..."}}
```

## GET /api/records?filter=&page=&page_size=&q=
`filter` = candidates|all|held|needs_review|resolved.
`scope` = actual|demo|all, API 기본값 all. 화면 기본값은 actual이다.
actual은 비공개 가져오기와 실제 서버 자료, demo는 합성·주입 시연 자료다.
응답의 `summary`는 선택한 scope 안에서 검색·필터·페이지 적용 전의
total/candidates/held/needs_review/resolved 수를 제공한다.
화면은 이 요약만 사용하며 실제 자료 집계에 시연 기록을 합산하지 않는다.
```json
{"items":[{"id":"...","label":"...","crop":"녹차","growth_week":3,"origin":"actual|injected|recorded|synthetic",
  "candidate":true,"matched_rules":["PH_SCREEN"],"group_id":"g1|null",
  "review_state":"unreviewed|reviewed|needs_review","resolution_state":"open|held|resolved",
  "revision":4,"data_revision":1,"env_date_raw":"2024-05-01 10:00:00"}],
 "page":1,"page_size":25,"total":33,"workspace_revision":12}
```

## GET /api/records/{id}  (상세)
```json
{"id":"...","crop":"녹차","origin":"actual","growth_week":3,"revision":4,"data_revision":1,"workspace_revision":12,
 "source":{"dataset_id":"...","member_path":"...","raw_hash":"sha256","source_group_id":"...",
   "image":{"available":true,"actual_width":380,"actual_height":380,"declared_width":3840,"declared_height":2160}},
 "values":[{"field_path":"environment.ph","label":"pH","raw":"14.5","parsed":"14.5",
   "status":"missing|blank|dash|invalid|zero|number","raw_unit":"dS/m|null",
   "unit_status":"unknown|source_declared","unit_source_ref":"caption|null","assumed_unit":null,
   "effective":"14.5","corrected":false}],
 "time":{"photo_raw":"240501100000","photo_parsed":"2024-05-01 10:00:00","env_raw":"...","env_parsed":"...",
   "timezone_status":"unknown","delta_seconds":5,"delta_label":"기록된 시각 차이·시간대 미확인","reason":null},
 "checks":[{"rule_id":"PH_SCREEN","version":"1","outcome":"matched|not_matched|not_evaluable",
   "reason":"...","fields":["environment.ph"],"not_concluded":"...","next_question":"..."}],
 "group":{"id":"g1","size":2,"key":{"env_date":"...","ph":"..","ec":"..","co2":"..","temp":".."},
   "members":[{"id":"...","review_state":"..","resolution_state":".."}],"note":"..."},
 "peers":[{"id":"...","growth_week":3,"timestamp":"...","values":{"ph":"6.1","ec":"..."},
   "unit_status":"unknown","origin":"actual"}],"peers_note":"...",
 "review":{"review_state":"...","resolution_state":"...",
   "hold":{"reason":"...","next_action":"...","actor":"...","at":"..."},
   "resolution":{"evidence_note":"...","evidence_refs":["..."],"actor":"...","at":"..."},
   "recheck_needed":true,"last_recheck_data_revision":1},
 "model":{"status":"none|pending|ok|failed|stale|not_adopted","run_id":null,"crop":"녹차","growth_week":3,
   "confidence":0.91,"message":"...","based_on":{"data_revision":1,"revision":3},"difference":false},
 "correctable_fields":[{"field":"environment.ph","label":"pH"}],
 "evidence_candidates":[{"ref":"rule:PH_SCREEN@1","label":"..."}],
 "history":[{"at":"...","actor":"...","type":"correction|review|hold|resolve|recheck|classify","summary":"...","before":"...","after":"..."}]}
```
`GET /api/records/{id}/image`: 이미지 바이트(380x380). 불가 시 404 → 프런트가 대체 문구 표시.

### 결과·흐름 개편의 추가 필드

- `model.crop_confidence`, `model.growth_confidence`: 각각의 분류 점수 문자열 또는 null.
  과거 `confidence`는 작물 점수이며 호환 보존한다. 없는 주차 점수는 추측하지 않는다.
- `model_attempt`: 최근 시도 또는 null. 재시도 실패 시에도 `model`에는
  이전에 채택한 성공 결과가 유지된다. 처음부터 실패한 경우에는 model에 실패를 표시한다.
- `comparison`: status(equal/different/unavailable), current_week/model_week(문자열 또는 null),
  week_matches/crop_matches(boolean 또는 null). 서버의 정확한 수치 비교를 사용하며
  프런트에서 Number로 재계산하지 않는다.
- `peers[].crop`: 비교 대상의 작물. 같은 정규화 작물·주차·단위 조건을 모두 만족한다.

사진 추론과 보정 이후 화면은 반환된 revision으로 `/recheck`를 순차 호출한다.
규칙 갱신만 자동화하며 reviewed/hold/resolve는 사람이 명시적으로 저장한다.
보고서 생성 요청의 record_ids는 현재 기록 1건 또는 체크한 ID 목록이다.
화면에서는 null(전체 후보 자동 포함)을 보내지 않는다.

## 요청 본문(계획 고정 형태)
- review: `{"expected_revision":4,"action":"reviewed|hold|resolve","note":str,"next_action":str,"evidence_note":str,"evidence_refs":[str]}`
  (hold: `note`=사유 + `next_action` 필수; resolve: `evidence_note` 필수, `evidence_refs`는 `evidence_candidates[].ref` 중 선택)
- corrections: `{"expected_revision":4,"field":"environment.ph","value":"7.1","reason":"..."}`
- recheck / classify: `{"expected_revision":4}`
- handovers: `{"expected_workspace_revision":12,"record_ids":null|[id]}`

## GET /api/groups/{id}
`{"id","key":{...},"interpretation":"...","members":[{"id","review_state","resolution_state","origin"}]}`

## POST /api/handovers -> 200/201, GET /api/handovers/{id}
```json
{"id":"h1","created_at":"...","workspace_revision":12,"sha256":"...","rule_version":"...",
 "scope":{"description":"후보 + 처리된 기록","record_ids":["..."],"count":5},
 "counts":{"open":2,"held":1,"needs_review":2,"resolved":0},
 "questions":[{"record_id":"...","state":"held","question":"...","next_action":"..."}]}
```
다운로드: `GET /api/handovers/{id}/download?format=json|csv|html`.

## Agent
- `GET /api/agent/status` -> `{"status":"disabled_by_owner","message":"..."}`
- `POST /api/agent/context` (본문 `{"record_ids":null|[..]}`) -> 읽기 전용 컨텍스트 JSON (프런트는 그대로 표시).
- `POST /api/agent/query` -> 503 `disabled_by_owner`. 프런트는 호출하지 않고 입력을 비활성 표시한다.
