"use strict";
(() => {
  const metaBase = document.querySelector('meta[name="farmcheck-base-path"]');
  const rawBase = metaBase ? metaBase.content : "";
  const BASE = rawBase.includes("__BASE_PATH__") ? "" : rawBase.replace(/\/+$/, "");

  const L = {
    origin: { actual: "실제 기록", injected: "주입 시험값", recorded: "재생 기록", synthetic: "합성 시연" },
    review: { unreviewed: "미검토", reviewed: "검토함", needs_review: "재검토 필요" },
    resolution: { open: "열림", held: "보류", resolved: "사람이 근거를 기록해 해소 처리" },
    status: { missing: "값 없음", blank: "공백", dash: "대시(-)", invalid: "해석 불가", zero: "숫자 0", number: "숫자" },
    unit: { unknown: "단위 미확인", source_declared: "출처 선언 단위" },
    outcome: { matched: "해당", not_matched: "해당 없음", not_evaluable: "평가 불가" },
    model: { none: "분류 전", pending: "요청 중", ok: "결과 있음", failed: "실패", stale: "오래된 결과(미채택)", not_adopted: "미채택" },
    history: { correction: "수정", review: "검토", hold: "보류", resolve: "해소", recheck: "재검사", classify: "모델 분류" },
    mode: { synthetic: "합성 시연 데이터", green_tea: "녹차 로컬 검토" },
    fields: { "environment.ph": "pH", "environment.ec": "EC", "environment.co2": "CO2", "environment.temp": "온도", "environment.humidity": "습도", "growth.growth_cycle": "주차", "images.class_name": "작물", env_date: "환경 시각", origin: "자료 종류" },
    rules: { PH_SCREEN: "pH 원문 확인", REQUIRED_NUMERIC: "필수 값 확인", MODEL_RECORD_DIFFERENCE: "사진·기록 대조" },
    scenarios: { shared_candidate_a: "공유 측정 기록 A", shared_candidate_b: "공유 측정 기록 B", boundary_exact_14: "pH 경계값 기록", boundary_above_14: "pH 정밀 소수 기록", injected_week_difference: "주차 불일치 시연", model_failure: "모델 실패 시연", missing_required: "필수 값 누락 기록", zero_is_numeric: "숫자 0 기록" },
  };
  const tone = (o) => ({ actual: "actual", injected: "injected", recorded: "recorded", synthetic: "synthetic" }[o] || "muted");

  const S = {
    boot: null, filter: "candidates", q: "", page: 1, pageSize: 25, list: null,
    id: null, detail: null, picked: new Set(), drafts: {}, handover: null, view: "records",
    loadingDetail: false, conflict: false,
  };
  const $ = (id) => document.getElementById(id);

  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "value") el.value = v;
      else if (v === true) el.setAttribute(k, "");
      else el.setAttribute(k, String(v));
    }
    for (const kid of kids.flat()) {
      if (kid === null || kid === undefined || kid === false) continue;
      el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }
  const badge = (text, cls, glyph) => h("span", { class: "badge " + (cls || ""), "data-glyph": glyph || "" , text });
  const originBadge = (o) => badge(L.origin[o] || String(o ?? "출처 미상"), tone(o), "● ");
  const val = (v) => (v === null || v === undefined || v === "" ? "—" : String(v));
  const enc = encodeURIComponent;
  const fieldName = (key) => L.fields[key] || key;
  const shortId = (id) => String(id || "").slice(0, 10);
  const recordName = (r) => L.scenarios[r.scenario] || r.label || "기록 " + shortId(r.id);
  function rawText(value, presence) {
    if (presence === "absent") return "키 없음";
    if (value === null) return "null";
    if (value === undefined) return "값 없음";
    if (value === "") return '"" (공백)';
    return typeof value === "string" || typeof value === "object" ? JSON.stringify(value) : String(value);
  }
  function identifier(value, label = "전체 ID") {
    return h("details", { class: "identifier" },
      h("summary", { title: label + " 펼치기", text: shortId(value) + "…" }),
      h("div", { class: "identifier-full" }, h("span", { class: "cell-label", text: label }),
        h("code", { text: val(value) })));
  }
  function shortTime(value) {
    if (/^\d{12}$/.test(value || "")) return value.slice(2, 4) + "." + value.slice(4, 6) + " " + value.slice(6, 8) + ":" + value.slice(8, 10);
    return val(value);
  }

  class ApiError extends Error {
    constructor(status, body) {
      super((body && body.message) || "요청에 실패했습니다.");
      this.status = status; this.code = (body && body.code) || (status ? "http_" + status : "network");
      this.details = body && body.details;
    }
  }
  async function api(method, path, body) {
    let res;
    try {
      res = await fetch(BASE + path, {
        method, credentials: "same-origin",
        headers: Object.assign({ Accept: "application/json" }, body !== undefined ? { "Content-Type": "application/json" } : {}),
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
    } catch (e) {
      throw new ApiError(0, { code: "network", message: "서버에 연결하지 못했습니다. 연결을 확인한 뒤 다시 시도하세요." });
    }
    let data = null;
    const text = await res.text();
    if (text) { try { data = JSON.parse(text); } catch (e) { data = null; } }
    if (!res.ok) throw new ApiError(res.status, data || { message: "서버가 JSON이 아닌 응답을 보냈습니다 (HTTP " + res.status + ")." });
    return data;
  }

  function showError(err, opts) {
    const box = $("notice");
    box.replaceChildren();
    box.className = "notice";
    box.hidden = false;
    const conflict = err.status === 409;
    const body = h("div", { class: "notice-body" },
      h("strong", { text: conflict ? "다른 곳에서 먼저 변경되었습니다 (409)" : "오류: " + err.message }),
      conflict ? h("p", { text: "화면의 정보가 오래되었습니다. 최신 내용을 불러온 뒤 입력한 내용을 확인하고 다시 저장하세요. 입력 초안은 유지됩니다." }) : null,
      conflict ? h("p", { class: "muted-text", text: err.message }) : null,
      h("details", {}, h("summary", { text: "오류 코드·상세" }),
        h("code", { text: err.code + (err.status ? " / HTTP " + err.status : "") }),
        err.details ? h("pre", { class: "json", text: JSON.stringify(err.details, null, 2) }) : null));
    box.append(body);
    if (conflict) box.append(h("button", { type: "button", class: "btn", text: "최신 내용 불러오기", onclick: async () => { hideError(); await refreshAll(); } }));
    box.append(h("button", { type: "button", class: "btn", text: "닫기", onclick: hideError }));
    if (opts && opts.focus !== false) box.scrollIntoView({ block: "nearest" });
  }
  function hideError() { $("notice").hidden = true; }
  function showInfo(msg) {
    const box = $("notice");
    box.className = "notice info"; box.hidden = false;
    box.replaceChildren(h("div", { class: "notice-body", text: msg }), h("button", { type: "button", class: "btn", text: "닫기", onclick: hideError }));
  }

  /* ---------- header & summary ---------- */
  function renderTop() {
    const b = S.boot, box = $("topBadges");
    box.replaceChildren();
    if (!b) return;
    box.append(badge(L.mode[b.mode] || String(b.mode), b.mode === "synthetic" ? "synthetic" : "actual", b.mode === "synthetic" ? "◆ " : "● "));
    const ag = b.agent_status || {};
    box.append(h("span", { class: "service-status", text: ag.status === "disabled_by_owner" ? "수동 검토 사용 가능 · AI 연동 예정" : "AI 상태: " + String(ag.status ?? "상태 미상") }));
  }
  function renderSummary() {
    const s = (S.boot && S.boot.summary) || {}, box = $("summary");
    box.replaceChildren();
    const stat = (n, t, filter) => h("button", { type: "button", class: "stat", "aria-pressed": String(S.filter === filter),
      onclick: () => { S.filter = filter; S.page = 1; $("filterSelect").value = filter; renderSummary(); loadList(); } },
      h("b", { text: val(n) }), h("span", { text: t }));
    box.append(stat(s.candidates, "확인 후보", "candidates"), stat(s.needs_review, "재검토 필요", "needs_review"), stat(s.held, "보류", "held"));
    box.append(h("p", { class: "stat-note", text: "현재 " + val(s.current_candidate_groups) + "개 후보 묶음 · 사진 기록 단위" }));
    if (s.baseline_records != null) box.append(h("details", { class: "stat-note baseline-note" },
      h("summary", { text: "집계 기준 보기" }), h("p", { text: "최초 pH 확인 대상: 사진 기록 " + val(s.baseline_records) + "개 / 묶음 " + val(s.baseline_groups) + "개. 작물 문제나 센서 고장 횟수가 아닙니다." })));
    if (S.boot.source_problems?.length) box.append(h("details", { class: "stat-note" },
      h("summary", { text: "원본 연결·해석 문제 " + S.boot.source_problems.length + "개 (검토 목록과 별도 보존)" }),
      ...S.boot.source_problems.map((p) => h("p", { text: p.member_path + " · " + p.kind + " · " + p.message + " · SHA256 " + p.raw_hash }))));
  }

  /* ---------- list ---------- */
  async function loadList() {
    const body = $("listBody");
    body.setAttribute("aria-busy", "true");
    body.replaceChildren(h("div", { class: "skeleton" }), h("div", { class: "skeleton" }), h("div", { class: "skeleton" }));
    try {
      const qs = "filter=" + enc(S.filter) + "&page=" + S.page + "&page_size=" + S.pageSize + (S.q ? "&q=" + enc(S.q) : "");
      S.list = await api("GET", "/api/records?" + qs);
      renderList(); if (!S.detail) renderDetail();
    } catch (e) {
      body.replaceChildren(h("div", { class: "empty" }, h("p", { text: "목록을 불러오지 못했습니다: " + e.message }), h("button", { class: "btn", type: "button", text: "다시 시도", onclick: loadList })));
      body.setAttribute("aria-busy", "false");
      showError(e, { focus: false });
    }
  }
  function renderList() {
    const body = $("listBody"), pager = $("pager"), L2 = S.list || { items: [] };
    body.setAttribute("aria-busy", "false");
    body.replaceChildren(); pager.replaceChildren();
    if (!L2.items.length) {
      body.append(h("div", { class: "empty" }, h("p", { text: S.q ? "검색 결과가 없습니다." : "이 범위에 해당하는 기록이 없습니다." }),
        h("button", { type: "button", class: "btn", text: "전체 기록 보기", onclick: () => { S.filter = "all"; S.q = ""; S.page = 1; $("filterSelect").value = "all"; $("searchInput").value = ""; renderSummary(); loadList(); } })));
      return;
    }
    for (const r of L2.items) {
      const open = h("button", { type: "button", class: "row", "aria-current": String(r.id === S.id), onclick: () => selectRecord(r.id) },
        h("div", { class: "row-title" }, h("span", { text: recordName(r), title: r.label || r.id })),
        h("div", { class: "row-meta" },
          originBadge(r.origin),
          r.growth_week != null ? badge(r.growth_week + "주차", "muted") : null,
          badge(L.review[r.review_state] || r.review_state, r.review_state === "needs_review" ? "warn" : "muted"),
          badge(L.resolution[r.resolution_state] === L.resolution.resolved ? "해소 처리" : (L.resolution[r.resolution_state] || r.resolution_state), r.resolution_state === "held" ? "warn" : "muted"),
          ...(r.matched_rules || []).map((m) => badge(L.rules[m] || m, "bad"))),
        h("div", { class: "row-foot", text: "ID " + shortId(r.id) + " · " + val(r.env_date_raw) }));
      const cb = h("label", { class: "check" }, h("input", { type: "checkbox", "aria-label": (r.label || r.id) + " 인수인계 범위에 포함", ...(S.picked.has(r.id) ? { checked: "" } : {}),
        onchange: (ev) => { ev.target.checked ? S.picked.add(r.id) : S.picked.delete(r.id); } }));
      body.append(h("div", { class: "row-wrap" }, cb, open));
    }
    const pages = Math.max(1, Math.ceil((L2.total || 0) / (L2.page_size || S.pageSize)));
    pager.append(
      h("button", { type: "button", class: "btn", text: "이전", disabled: S.page <= 1, onclick: () => { S.page--; loadList(); } }),
      h("span", { text: S.page + " / " + pages + "쪽 · 총 " + val(L2.total) + "건" }),
      h("button", { type: "button", class: "btn", text: "다음", disabled: S.page >= pages, onclick: () => { S.page++; loadList(); } }));
  }

  /* ---------- detail ---------- */
  async function selectRecord(id) {
    S.id = id; S.drafts = {}; S.conflict = false;
    document.body.dataset.pane = "detail";
    renderList();
    await loadDetail();
    $("detail").focus?.();
  }
  async function loadDetail() {
    const box = $("detail");
    box.replaceChildren(h("div", { class: "skeleton" }), h("div", { class: "skeleton" }), h("div", { class: "skeleton" }));
    try {
      S.detail = await api("GET", "/api/records/" + enc(S.id));
      renderDetail();
    } catch (e) {
      box.replaceChildren(backBtn(), h("div", { class: "empty" }, h("p", { text: "기록을 불러오지 못했습니다: " + e.message }), h("button", { class: "btn", type: "button", text: "다시 시도", onclick: loadDetail })));
      showError(e, { focus: false });
    }
  }
  function backBtn() { return h("button", { type: "button", class: "btn back", text: "← 목록으로", onclick: () => { document.body.dataset.pane = "list"; } }); }
  function applyDetail(d) {
    if (d && d.id) { S.detail = d; S.id = d.id; if (S.boot && d.workspace_revision != null) { S.boot.workspace_revision = d.workspace_revision; renderTop(); } renderDetail(); }
  }
  async function refreshAll() {
    try { S.boot = await api("GET", "/api/bootstrap"); renderTop(); renderSummary(); } catch (e) { showError(e); }
    await loadList();
    if (S.id) await loadDetail();
  }

  function welcomeCard() {
    return h("section", { class: "welcome" },
      h("p", { class: "eyebrow", text: "기록 검토 시작하기" }),
      h("h2", { text: "확인한 근거와 남은 질문을 함께 남기세요" }),
      h("p", { text: "기록을 고르고 원문을 비교한 뒤, 검토 결과를 다음 담당자에게 전달합니다." }),
      h("ol", { class: "welcome-steps" },
        h("li", {}, h("strong", { text: "근거 확인" }), h("span", { text: "사진과 원문·적용값을 비교합니다." })),
        h("li", {}, h("strong", { text: "판단 기록" }), h("span", { text: "검토, 보류 또는 보정 이유를 남깁니다." })),
        h("li", {}, h("strong", { text: "인수인계" }), h("span", { text: "미해결 질문까지 고정본에 담습니다." }))),
      h("button", { type: "button", class: "btn primary", disabled: !S.list?.items?.length, text: "첫 기록 검토하기", onclick: () => { if (S.list?.items[0]) selectRecord(S.list.items[0].id); } }),
      h("p", { class: "help", text: "확인 후보는 다시 살펴볼 기록입니다. 작물 이상이나 센서 고장을 뜻하지 않습니다." }));
  }

  function renderDetail() {
    const d = S.detail, box = $("detail");
    box.replaceChildren();
    if (!d) { box.append(welcomeCard()); return; }
    const rv = d.review || {};
    box.append(backBtn(),
      h("section", { class: "card lead" },
        h("div", { class: "detail-heading" }, h("div", {}, h("p", { class: "eyebrow", text: "기록 검토" }), h("h2", { text: recordName(d) })), identifier(d.id, "기록 ID")),
        h("div", { class: "head-row" },
          originBadge(d.origin),
          d.growth_week != null ? badge(d.growth_week + "주차", "muted") : null,
          badge("검토: " + (L.review[rv.review_state] || val(rv.review_state)), rv.review_state === "needs_review" ? "warn" : "muted"),
          badge("처리: " + (L.resolution[rv.resolution_state] || val(rv.resolution_state)), rv.resolution_state === "held" ? "warn" : "muted"),
          rv.recheck_needed ? badge("재검사 필요", "warn", "▲ ") : null,
          h("span", { class: "version-note", text: "기록 버전 " + val(d.revision) + " · 데이터 " + val(d.data_revision) })),
        h("p", { class: "muted-text", text: "‘검토함’은 사람이 살펴봤다는 뜻이며 문제가 해결되었다는 뜻이 아닙니다. 처리 상태는 별도로 기록됩니다." }),
        rv.hold && rv.hold.next_action ? h("div", { class: "check-item" }, h("strong", { text: "보류에서 남긴 확인 질문" }), h("p", { text: "사유: " + val(rv.hold.reason) }), h("p", { text: "다음 조치: " + val(rv.hold.next_action) })) : null,
        rv.resolution_state === "resolved" && rv.resolution ? h("div", { class: "check-item" }, h("strong", { text: "사람이 근거를 기록해 해소 처리" }), h("p", { text: val(rv.resolution.evidence_note) }),
          (rv.resolution.evidence_refs || []).length ? h("p", { class: "mono", text: "근거: " + rv.resolution.evidence_refs.join(", ") }) : null) : null),
      checksCard(d), mainCard(d), actionsCard(d), groupCard(d), peersCard(d), sourceCard(d), modelCard(d), historyCard(d));
    if (S.conflict) box.prepend(h("div", { class: "notice" }, "이 화면은 오래된 버전일 수 있습니다. 위 알림에서 최신 내용을 불러오세요."));
  }

  function checksCard(d) {
    const c = h("section", { class: "card checks-card" }, h("h3", { text: "확인할 이유" }));
    const checks = d.checks || [];
    if (!checks.length) c.append(h("p", { class: "muted-text", text: "표시할 규칙 검사 결과가 없습니다." }));
    for (const k of checks) {
      c.append(h("div", { class: "check-item" },
        h("div", { class: "head-row" }, h("strong", { text: L.rules[k.rule_id] || k.rule_id }),
          badge(L.outcome[k.outcome] || k.outcome, k.outcome === "matched" ? "bad" : "muted", k.outcome === "matched" ? "▲ " : "")),
        k.outcome === "matched" ? h("p", { class: "next-question", text: k.next_question }) : null,
        h("details", { class: "check-explanation" }, h("summary", { text: "판정 근거와 한계" }),
          h("p", { text: val(k.reason) }),
          h("p", { text: "대상: " + ((k.fields || []).map(fieldName).join(" · ") || "—") }),
          h("p", { text: "단정하지 않는 것: " + val(k.not_concluded) }),
          h("p", { class: "mono", text: k.rule_id + " · v" + val(k.version) }))));
    }
    return c;
  }

  function mainCard(d) {
    const img = d.source?.image || {}, t = d.time || {};
    const photo = h("div", { class: "photo" });
    if (img.available === false) photo.append(h("p", { text: "이 모드에서는 원본 사진을 표시하지 않습니다." }));
    else {
      const im = h("img", { src: BASE + "/api/records/" + enc(d.id) + "/image", width: "380", height: "380", alt: recordName(d) + "의 원본 사진. 좌표나 크기 측정에 사용하지 않습니다." });
      im.addEventListener("error", () => photo.replaceChildren(h("p", { text: "사진을 불러오지 못했습니다. 원문 기록은 계속 확인할 수 있습니다." })));
      photo.append(im);
    }
    const rows = (d.values || []).map((v) => h("tr", { class: v.corrected ? "value-corrected" : "" },
      h("th", { scope: "row", text: v.label || fieldName(v.field_path), title: v.field_path }),
      h("td", {}, h("span", { class: "cell-label mobile-only", text: "원문·해석" }),
        h("code", { class: "raw-value", text: rawText(v.raw, v.presence) }), h("span", { class: "cell-note", text: "해석값 " + val(v.parsed) })),
      h("td", {}, h("span", { class: "cell-label mobile-only", text: "적용값" }),
        h("strong", { class: "numeric", text: v.effective == null ? rawText(v.effective_raw) : String(v.effective) }),
        h("span", { class: "cell-note" }, L.status[v.status] || v.status, v.corrected ? badge("보정 적용", "warn") : null)),
      h("td", { class: "unit-cell" }, h("strong", { text: val(v.raw_unit) }),
        h("span", { class: "cell-note", text: L.unit[v.unit_status] || val(v.unit_status) }),
        v.unit_source_ref ? h("span", { class: "cell-note mono", text: v.unit_source_ref }) : null,
        v.assumed_unit ? h("span", { class: "cell-note", text: "규칙 가정 단위 " + v.assumed_unit }) : null)));
    const values = h("table", { class: "values-table" },
      h("thead", {}, h("tr", {}, ...["항목", "원문·해석", "적용값", "단위·출처"].map(text => h("th", { scope: "col", text })))),
      h("tbody", {}, rows));
    const time = h("details", { class: "time-details" }, h("summary", { text: "사진·환경 시각 확인" }),
      h("dl", { class: "kv" },
        h("dt", { text: "사진 원문" }), h("dd", { text: val(t.photo_raw) }),
        h("dt", { text: "사진 해석" }), h("dd", { text: val(t.photo_parsed) }),
        h("dt", { text: "환경 원문" }), h("dd", { text: val(t.env_raw) }),
        h("dt", { text: "환경 해석" }), h("dd", { text: val(t.env_parsed) }),
        h("dt", { text: "시간대" }), h("dd", { text: t.timezone_status === "unknown" ? "확인되지 않음" : val(t.timezone_status) }),
        h("dt", { text: "시각 차이" }), h("dd", { text: t.delta_seconds == null ? "계산 불가" : String(t.delta_seconds) + "초" })),
      h("p", { class: "help", text: val(t.delta_label) + " · 환경 시각이 사진보다 늦어도 자동으로 오류를 뜻하지 않습니다." }));
    return h("section", { class: "card" }, h("h3", { text: "사진과 기록 값" }),
      h("div", { class: "two" }, h("div", {}, photo,
        h("p", { class: "photo-note", text: "실제 " + val(img.actual_width) + "×" + val(img.actual_height) + " px · 선언 " + val(img.declared_width) + "×" + val(img.declared_height) + " px" }),
        h("p", { class: "help", text: "좌표 표시나 크기 측정 없이 원본 사진만 보여줍니다." }), time),
        h("div", { class: "values-region" }, values, h("p", { class: "help", text: "원문은 유지됩니다. 적용값은 보정 후 현재 검사에 사용하는 값입니다." }))));
  }

  function sourceCard(d) {
    const s = d.source || {};
    return h("details", { class: "card source-details", id: "sourceEvidence" },
      h("summary", {}, h("span", { class: "section-title", text: "원문과 식별정보" }), h("span", { class: "help", text: "원본 JSON · 해시 · 자료 출처" })),
      h("dl", { class: "kv" },
        h("dt", { text: "기록 ID" }), h("dd", {}, identifier(d.id)),
        h("dt", { text: "자료 종류" }), h("dd", {}, originBadge(d.origin)),
        h("dt", { text: "데이터셋" }), h("dd", { text: val(s.dataset_id) }),
        h("dt", { text: "원본 경로" }), h("dd", { class: "mono", text: val(s.member_path) }),
        h("dt", { text: "원본 해시" }), h("dd", {}, identifier(s.raw_hash, "전체 SHA256")),
        h("dt", { text: "자료 출처 키" }), h("dd", { class: "mono", text: val(s.source_group_id) })),
      h("p", { class: "help", text: "같은 JSON을 캡션·SQL·설명으로 다시 표현해도 독립된 새 근거가 아닙니다." }),
      h("details", {}, h("summary", { text: "원본 JSON 펼치기" }), h("pre", { class: "json", tabIndex: 0, text: s.raw_json || "원문 정보가 없습니다." })));
  }

  function groupCard(d) {
    const g = d.group;
    const c = h("section", { class: "card group-card" }, h("h3", { text: "같은 측정값의 사진 기록" }));
    if (!g) { c.append(h("p", { class: "help", text: "다른 기록과 묶이지 않은 사진 기록입니다." })); return c; }
    c.append(h("div", { class: "head-row" }, h("strong", { text: "원본 필드가 같은 사진 " + val(g.size) + "개" }), identifier(g.id, "후보 묶음 ID")),
      h("details", { class: "group-key" }, h("summary", { text: "묶음 기준과 단위 보기" }),
        h("dl", { class: "kv" }, ...Object.entries(g.key || {}).flatMap(([key, value]) => key === "units"
          ? Object.entries(value).flatMap(([field, unit]) => [h("dt", { text: fieldName(field) + " 단위" }), h("dd", { text: val(unit.raw_unit) + " · " + (L.unit[unit.status] || val(unit.status)) + " · " + val(unit.source_ref) })])
          : [h("dt", { text: fieldName(key) }), h("dd", { text: key === "origin" ? (L.origin[value] || val(value)) : val(value) })]))),
      h("p", { class: "help", text: g.note || "보기용 묶음이며 독립적인 측정 횟수가 아닙니다." }),
      h("p", { class: "help", text: "한 기록의 보정이나 해소는 다른 구성원에 적용되지 않습니다." }));
    const list = h("div", { class: "group-members" });
    for (const member of g.members || []) list.append(h("button", { type: "button", class: "row", onclick: () => selectRecord(member.id), "aria-current": String(member.id === d.id) },
      h("strong", { text: recordName(member) }), h("span", { class: "row-foot", text: "ID " + shortId(member.id) }),
      h("div", { class: "row-meta" }, badge(L.review[member.review_state] || member.review_state, "muted"), badge(member.resolution_state === "resolved" ? "해소 처리" : L.resolution[member.resolution_state], "muted"))));
    c.append(list); return c;
  }

  function peersCard(d) {
    const c = h("section", { class: "card peers-card" }, h("h3", { text: "다른 기록과 비교" }),
      h("p", { class: "help", text: "같은 작물·주차·단위 표기 기준, 최대 10개입니다. 필요한 열은 가로로 이동해 확인하세요." }));
    const peers = d.peers || [];
    if (!peers.length) c.append(h("p", { class: "help", text: "같은 기준으로 비교할 기록이 없습니다." }));
    else {
      const keys = [...new Set(peers.flatMap(peer => Object.keys(peer.values || {})))];
      c.append(h("div", { class: "tbl-wrap", tabIndex: 0, role: "region", "aria-label": "기록 비교 표, 가로 방향키로 이동" },
        h("table", { class: "comparison-table" },
          h("thead", {}, h("tr", {}, ...["기록 ID", "주차", "시각", ...keys.map(fieldName), "단위 표기", "자료 종류"].map(text => h("th", { scope: "col", text })))),
          h("tbody", {}, peers.map(peer => h("tr", {},
            h("td", { class: "peer-id" }, identifier(peer.id), h("button", { type: "button", class: "text-button", text: "기록 보기", onclick: () => selectRecord(peer.id) })),
            h("td", { text: val(peer.growth_week) }), h("td", { title: val(peer.timestamp), text: shortTime(peer.timestamp) }),
            ...keys.map(key => h("td", { class: "num", text: val(peer.values?.[key]) })),
            h("td", { text: L.unit[peer.unit_status] || val(peer.unit_status) }), h("td", {}, originBadge(peer.origin))))))));
    }
    c.append(h("p", { class: "help", text: d.peers_note || "비교는 정상 범위나 생육 추세를 뜻하지 않습니다." }));
    return c;
  }

  function modelCard(d) {
    const m = d.model || { status: "none" };
    const c = h("section", { class: "card" }, h("h3", { text: "이미지 모델 분류 결과" }),
      h("div", { class: "head-row" }, badge(L.model[m.status] || m.status, m.status === "failed" || m.status === "stale" ? "bad" : "muted")));
    if (m.status === "ok" || m.status === "not_adopted") {
      c.append(h("dl", { class: "kv" }, h("dt", { text: "모델 출력 작물" }), h("dd", { text: val(m.crop) }), h("dt", { text: "모델 출력 주차" }), h("dd", { text: val(m.growth_week) }),
        h("dt", { text: "모델 신뢰도 출력" }), h("dd", { text: val(m.confidence) }), h("dt", { text: "기록과의 차이" }), h("dd", { text: m.difference === null || m.difference === undefined ? "비교 불가" : (m.difference ? "차이 있음 → 재확인 요청일 뿐 기록 오류 확정이 아닙니다" : "차이 없음") })));
    }
    if (m.message) c.append(h("p", { text: m.message }));
    c.append(h("p", { class: "muted-text", text: "모델 값은 모델의 출력이며 정답 근거나 보정된 확률이 아닙니다. 모델이 실패해도 수동 검토는 계속할 수 있습니다." }));
    return c;
  }

  function historyCard(d) {
    const c = h("section", { class: "card" }, h("h3", { text: "변경 이력" }));
    const hs = d.history || [];
    if (!hs.length) { c.append(h("p", { class: "muted-text", text: "아직 기록된 변경이 없습니다." })); return c; }
    c.append(h("ul", { class: "history" }, hs.map((e) => h("li", {}, h("strong", { text: (L.history[e.type] || e.type) + " · " + val(e.actor) }), " ", h("span", { class: "muted-text", text: val(e.at) }),
      h("div", { text: val(e.summary) }), e.before != null || e.after != null ? h("div", { class: "mono", text: "전: " + val(e.before) + " → 후: " + val(e.after) }) : null))));
    return c;
  }

  /* ---------- actions ---------- */
  function draft(key) { return S.drafts[key] || (S.drafts[key] = {}); }
  function textField(label, key, name, opts) {
    const dr = draft(key);
    const el = h(opts && opts.area ? "textarea" : "input", { ...(opts && opts.area ? {} : { type: "text" }), required: true, "aria-required": "true", value: dr[name] || "", placeholder: (opts && opts.ph) || "" });
    el.addEventListener("input", () => { dr[name] = el.value; });
    return { el, wrap: h("label", { class: "field" }, h("span", { text: label }), el) };
  }
  async function send(btn, fn) {
    btn.disabled = true; hideError();
    try { await fn(); } catch (e) {
      if (e.status === 409) S.conflict = true;
      showError(e);
      if (e.status === 409) renderDetail();
    } finally { btn.disabled = false; }
  }
  function mutate(path, body) {
    return api("POST", path, body).then(async (d) => {
      if (d && d.id) applyDetail(d); else await loadDetail();
      try { S.boot = await api("GET", "/api/bootstrap"); renderTop(); renderSummary(); } catch (e) { /* summary refresh is best-effort display */ }
      loadList();
    });
  }
  const rev = () => S.detail.revision;
  const review = (a) => Object.assign({ expected_revision: rev(), action: "reviewed", note: "", next_action: "", evidence_note: "", evidence_refs: [] }, a);

  function actionsCard(d) {
    const rid = enc(d.id), rv = d.review || {};
    const notEmpty = (v) => typeof v === "string" && v.trim().length > 0;

    const reviewed = (() => {
      const dr = draft("reviewed");
      const note = h("textarea", { value: dr.note || "", placeholder: "선택: 살펴본 내용" });
      note.addEventListener("input", () => { dr.note = note.value; });
      const b = h("button", { type: "button", class: "btn primary", text: "검토함으로 기록", onclick: () => send(b, () => mutate("/api/records/" + rid + "/review", review({ action: "reviewed", note: notEmpty(dr.note) ? dr.note.trim() : "" }))) });
      return h("div", { class: "action" }, h("h4", { text: "1. 검토 기록" }), h("p", { class: "muted-text", text: "사람이 확인했음을 남깁니다. 문제 해결은 아닙니다." }), h("label", { class: "field" }, h("span", { text: "메모" }), note), h("div", { class: "spacer" }), b);
    })();

    const hold = (() => {
      const r = textField("보류 사유 (필수)", "hold", "reason", { area: true }), n = textField("다음 담당자의 조치 (필수)", "hold", "next", { area: true });
      const b = h("button", { type: "button", class: "btn", text: "보류하고 질문 남기기", onclick: () => {
        const dr = draft("hold");
        if (!notEmpty(dr.reason) || !notEmpty(dr.next)) return showError(new ApiError(0, { code: "validation", message: "보류 사유와 다음 조치를 모두 입력하세요." }), {});
        send(b, () => mutate("/api/records/" + rid + "/review", review({ action: "hold", note: dr.reason.trim(), next_action: dr.next.trim() })));
      } });
      return h("div", { class: "action" }, h("h4", { text: "2. 보류" }), h("p", { class: "muted-text", text: "미해결 질문으로 인수인계에 포함됩니다." }), r.wrap, n.wrap, h("div", { class: "spacer" }), b);
    })();

    const correct = (() => {
      const dr = draft("corr");
      const fields = d.correctable_fields || [];
      const sel = h("select", { "aria-required": "true" }, fields.map((f) => h("option", { value: f.field, text: f.label || f.field })));
      if (dr.field) sel.value = dr.field; else dr.field = sel.value;
      sel.addEventListener("change", () => { dr.field = sel.value; });
      const v = textField("새 원문 값 (필수)", "corr", "value"), r = textField("수정 이유 (필수)", "corr", "reason", { area: true });
      const b = h("button", { type: "button", class: "btn", text: "수정 저장 (원본 보존)", disabled: !fields.length, onclick: () => {
        if (!notEmpty(dr.value) || !notEmpty(dr.reason) || !dr.field) return showError(new ApiError(0, { code: "validation", message: "필드, 새 값, 수정 이유를 모두 입력하세요." }), {});
        send(b, () => mutate("/api/records/" + rid + "/corrections", { expected_revision: rev(), field: dr.field, value: dr.value, reason: dr.reason.trim() }));
      } });
      return h("div", { class: "action" }, h("h4", { text: "3. 값 수정" }), h("p", { class: "muted-text", text: "원본은 바뀌지 않고 덮어쓰기 기록이 남습니다. 수정하면 이전 검토·해소가 무효가 됩니다." }),
        h("label", { class: "field" }, h("span", { text: "수정할 필드" }), sel), v.wrap, r.wrap, h("div", { class: "spacer" }), b);
    })();

    const recheck = (() => {
      const b = h("button", { type: "button", class: "btn", text: "현재 데이터로 재검사", onclick: () => send(b, () => mutate("/api/records/" + rid + "/recheck", { expected_revision: rev() })) });
      const cls = h("button", { type: "button", class: "btn", text: "이미지 모델로 분류 요청", onclick: () => send(cls, () => mutate("/api/records/" + rid + "/classify", { expected_revision: rev() })) });
      return h("div", { class: "action" }, h("h4", { text: "4. 재검사·모델 분류" }), h("p", { class: "muted-text", text: "재검사는 자동으로 해소하지 않습니다. 모델 분류는 실패할 수 있으며 그래도 수동 검토는 가능합니다." }),
        h("p", { text: rv.recheck_needed ? "상태: 현재 버전의 재검사가 필요합니다." : "상태: 현재 데이터 기준 재검사 완료" }), h("div", { class: "spacer" }), b, cls);
    })();

    const resolve = (() => {
      const dr = draft("res"); dr.refs = dr.refs || [];
      const ev = textField("해소 근거 설명 (필수)", "res", "note", { area: true });
      const refs = h("fieldset", { class: "field" }, h("legend", { text: "근거 참조 (1개 이상 필수)" }),
        (d.evidence_candidates || []).map((c) => h("label", { class: "check" }, h("input", { type: "checkbox", ...(dr.refs.includes(c.ref) ? { checked: "" } : {}),
          onchange: (e) => { dr.refs = e.target.checked ? [...dr.refs, c.ref] : dr.refs.filter((x) => x !== c.ref); } }), h("span", { text: c.label || c.ref }))));
      const blocked = rv.recheck_needed;
      const b = h("button", { type: "button", class: "btn", text: "사람이 근거를 기록해 해소 처리", disabled: blocked, onclick: () => {
        if (!notEmpty(dr.note) || !dr.refs.length) return showError(new ApiError(0, { code: "validation", message: "해소 근거 설명과 출처를 1개 이상 선택하세요." }), {});
        send(b, () => mutate("/api/records/" + rid + "/review", review({ action: "resolve", evidence_note: dr.note.trim(), evidence_refs: dr.refs })));
      } });
      return h("div", { class: "action" }, h("h4", { text: "5. 해소 처리" }), h("p", { class: "muted-text", text: blocked ? "현재 데이터 기준 재검사를 먼저 실행해야 합니다." : "같은 출처를 다시 표현한 내용은 새 근거가 아닙니다." }), ev.wrap, refs, h("div", { class: "spacer" }), b);
    })();

    return h("section", { class: "card" }, h("h3", { text: "사람이 할 일" }), h("div", { class: "actions" }, reviewed, hold, correct, recheck, resolve));
  }

  /* ---------- handover ---------- */
  function renderHandover() {
    const v = $("viewHandover");
    v.replaceChildren();
    const mode = h("select", { id: "hoScope" }, h("option", { value: "auto", text: "현재 후보 + 처리·수정한 모든 기록" }),
      h("option", { value: "picked", text: "목록에서 체크한 기록 (" + S.picked.size + "건)" }));
    const b = h("button", { type: "button", class: "btn primary", text: "인수인계 스냅샷 만들기", onclick: () => send(b, async () => {
      const ids = mode.value === "picked" ? [...S.picked] : null;
      if (mode.value === "picked" && !ids.length) throw new ApiError(0, { code: "validation", message: "체크한 기록이 없습니다. 기록 화면에서 선택하세요." });
      S.handover = await api("POST", "/api/handovers", { expected_workspace_revision: S.boot.workspace_revision, record_ids: ids });
      renderHandover();
    }) });
    v.append(h("div", { class: "stack" }, h("section", { class: "card" }, h("h2", { text: "인수인계" }),
      h("p", { class: "muted-text", text: "만들어진 스냅샷은 변경되지 않습니다. 이후 수정은 새 스냅샷으로 남습니다. 보류·열림 기록도 미해결로 그대로 전달됩니다." }),
      h("div", { class: "toolbar" }, h("label", { class: "field" }, h("span", { text: "포함 범위" }), mode), b))));
    const hd = S.handover;
    if (!hd) { v.firstChild.append(h("div", { class: "empty" }, h("p", { text: "아직 만든 스냅샷이 없습니다." }))); return; }
    const sc = hd.scope || {}, ct = hd.counts || {};
    const dl = (f) => h("a", { class: "btn", href: BASE + "/api/handovers/" + enc(hd.id) + "/download?format=" + f, text: f.toUpperCase() + " 내려받기", download: "" });
    v.firstChild.append(h("section", { class: "card" }, h("h3", { text: "스냅샷 " + hd.id }),
      h("dl", { class: "kv" }, h("dt", { text: "생성" }), h("dd", { text: val(hd.created_at) }), h("dt", { text: "작업공간 버전" }), h("dd", { text: val(hd.workspace_revision) }),
        h("dt", { text: "범위" }), h("dd", { text: val(sc.description) + " · " + val(sc.count) + "건 (전체 식물을 점검했다는 뜻이 아닙니다)" }),
        h("dt", { text: "열림/보류/재검토/해소" }), h("dd", { text: [ct.open, ct.held, ct.needs_review, ct.resolved].map(val).join(" / ") }),
        h("dt", { text: "규칙 버전" }), h("dd", { text: val(hd.rule_version) }), h("dt", { text: "SHA-256" }), h("dd", { class: "mono", text: val(hd.sha256) })),
      h("div", { class: "toolbar" }, dl("json"), dl("csv"), dl("html"))),
      h("section", { class: "card" }, h("h3", { text: "미해결 질문" }),
        (hd.questions || []).length ? h("ul", { class: "history" }, hd.questions.map((q) => h("li", {}, h("strong", { text: q.record_id + " · " + val(q.state) }), h("div", { text: val(q.question) }), h("div", { class: "muted-text", text: "다음 조치: " + val(q.next_action) }))))
          : h("p", { class: "muted-text", text: "이 스냅샷에 남은 질문이 없습니다." })));
  }

  /* ---------- rules & agent ---------- */
  function renderRules() {
    const v = $("viewRules"); v.replaceChildren();
    const reg = (S.boot && S.boot.rule_registry) || [];
    const ag = (S.boot && S.boot.agent_status) || {};
    const ctxOut = h("pre", { class: "json", hidden: true });
    const cb = h("button", { type: "button", class: "btn", text: "읽기 전용 컨텍스트 내보내기", onclick: () => send(cb, async () => {
      const r = await api("POST", "/api/agent/context", { record_ids: S.picked.size ? [...S.picked] : null });
      ctxOut.textContent = JSON.stringify(r, null, 2); ctxOut.hidden = false;
    }) });
    v.append(h("div", { class: "stack" },
      h("section", { class: "card" }, h("h2", { text: "AgentStream" }),
        h("div", { class: "agent-box" },
          h("p", {}, badge("비활성 · 소유자 지시로 내일 연동", "muted", "○ ")),
          h("p", { text: val(ag.message) === "—" ? "에이전트 질의는 아직 연결되지 않았습니다. 수동 검토 흐름은 모두 사용할 수 있습니다." : ag.message }),
          h("label", { class: "field" }, h("span", { text: "에이전트에게 질문 (사용 불가)" }), h("textarea", { disabled: true, placeholder: "내일 연동 예정" })),
          h("button", { type: "button", class: "btn", disabled: true, text: "질문 보내기 (비활성)" }),
          h("p", { class: "muted-text", text: "아래 내보내기는 에이전트가 읽을 자료를 미리 확인하는 읽기 전용 기능이며 에이전트 응답이 아닙니다." }), cb, ctxOut)),
      h("section", { class: "card" }, h("h2", { text: "규칙 안내" }),
        reg.length ? reg.map((r) => h("div", { class: "check-item" }, h("strong", { text: r.rule_id + " v" + val(r.version) }),
          h("p", { text: "대상: " + ((r.fields || []).join(", ") || "—") + " · 가정 단위: " + val(r.assumed_unit) }), h("p", { text: "근거: " + val(r.rationale) }),
          h("p", { text: "단정하지 않는 것: " + val(r.not_concluded) }), h("p", { text: "다음 질문: " + val(r.next_question) })))
          : h("p", { class: "muted-text", text: "규칙 목록을 불러오지 못했습니다." }))));
  }

  /* ---------- nav & init ---------- */
  function setView(name) {
    S.view = name;
    for (const t of document.querySelectorAll(".tab")) t.setAttribute("aria-current", t.dataset.view === name ? "page" : "false");
    $("viewRecords").hidden = name !== "records"; $("viewHandover").hidden = name !== "handover"; $("viewRules").hidden = name !== "rules";
    if (name === "handover") renderHandover();
    if (name === "rules") renderRules();
  }
  async function init() {
    for (const t of document.querySelectorAll(".tab")) t.addEventListener("click", () => setView(t.dataset.view));
    $("filterForm").addEventListener("submit", (e) => { e.preventDefault(); S.filter = $("filterSelect").value; S.q = $("searchInput").value.trim(); S.page = 1; renderSummary(); loadList(); });
    $("detail").tabIndex = -1;
    renderDetail();
    try {
      S.boot = await api("GET", "/api/bootstrap");
      renderTop(); renderSummary();
    } catch (e) { showError(e); $("topBadges").replaceChildren(badge("서버 연결 실패", "bad", "▲ ")); }
    await loadList();
  }
  init();
})();
