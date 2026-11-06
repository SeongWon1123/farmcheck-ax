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
    history: { correction: "보정", reviewed: "검토", review: "검토", hold: "보류", resolve: "해소", recheck: "재검사", classify: "모델 분류", classify_failed: "모델 호출 실패" },
    mode: { synthetic: "합성 시연 데이터", green_tea: "녹차 로컬 검토" },
    fields: { "environment.ph": "pH", "environment.ec": "EC", "environment.co2": "CO2", "environment.temp": "온도", "environment.humidity": "습도", "growth.growth_cycle": "주차", "images.class_name": "작물", env_date: "환경 시각", origin: "자료 종류" },
    rules: { PH_SCREEN: "pH 원문 확인", REQUIRED_NUMERIC: "필수 값 확인", MODEL_RECORD_DIFFERENCE: "사진·기록 대조" },
    scenarios: { shared_candidate_a: "공유 측정 기록 A", shared_candidate_b: "공유 측정 기록 B", boundary_exact_14: "pH 경계값 기록", boundary_above_14: "pH 정밀 소수 기록", injected_week_difference: "주차 불일치 시연", model_failure: "모델 실패 시연", missing_required: "필수 값 누락 기록", zero_is_numeric: "숫자 0 기록" },
  };
  const tone = (o) => ({ actual: "actual", injected: "injected", recorded: "recorded", synthetic: "synthetic" }[o] || "muted");

  const S = {
    boot: null, filter: "candidates", q: "", page: 1, pageSize: 25, list: null,
    id: null, detail: null, picked: new Set(), drafts: new Map(), handover: null, view: "records",
    pendingWrites: new Set(), conflicts: new Set(), feedback: new Map(), activeActions: new Map(),
    listRequest: 0, detailRequest: 0, bootRequest: 0, listScrollY: 0, handoverScope: "auto",
    handoverPending: false, handoverMessage: "",
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
        method, credentials: "same-origin", cache: "no-store",
        headers: Object.assign({ Accept: "application/json" }, body !== undefined ? { "Content-Type": "application/json" } : {}),
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
    } catch (e) {
      throw new ApiError(0, { code: "network", message: "서버에 연결하지 못했습니다. 연결을 확인한 뒤 다시 시도하세요." });
    }
    let data = null;
    let text;
    try { text = await res.text(); }
    catch (error) { throw new ApiError(0, { code: "network", message: "응답을 받는 중 연결이 끊겼습니다. 저장 여부를 확인하려면 최신 내용을 다시 불러오세요." }); }
    if (text) { try { data = JSON.parse(text); } catch (e) { data = null; } }
    if (!res.ok) throw new ApiError(res.status, data || { message: "서버가 JSON이 아닌 응답을 보냈습니다 (HTTP " + res.status + ")." });
    if (!data || typeof data !== "object" || Array.isArray(data)) throw new ApiError(res.status, { code: "invalid_response", message: "서버 응답을 읽을 수 없습니다. 최신 내용을 다시 불러와 확인하세요." });
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
    box.append(h("button", { type: "button", class: "btn", text: conflict ? "최신 내용 불러오기" : "화면 다시 불러오기", onclick: async () => { hideError(); await refreshAll(); } }));
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
  async function loadList({ quiet = false } = {}) {
    const request = ++S.listRequest, body = $("listBody");
    body.setAttribute("aria-busy", "true");
    $("pager").querySelectorAll("button").forEach(button => { button.disabled = true; });
    body.replaceChildren(h("div", { class: "skeleton" }), h("div", { class: "skeleton" }));
    try {
      const qs = "filter=" + enc(S.filter) + "&page=" + S.page + "&page_size=" + S.pageSize + (S.q ? "&q=" + enc(S.q) : "");
      const list = await api("GET", "/api/records?" + qs);
      if (request !== S.listRequest) return;
      if (!Array.isArray(list.items)) throw new ApiError(200, { code: "invalid_response", message: "기록 목록 응답이 올바르지 않습니다." });
      S.list = list; renderList(); if (!S.detail) renderDetail();
      if (!quiet) announce("목록에 " + list.items.length + "개 기록을 표시했습니다.");
    } catch (error) {
      if (request !== S.listRequest) return;
      body.replaceChildren(h("div", { class: "empty" }, h("p", { text: "목록을 불러오지 못했습니다." }),
        h("p", { class: "help", text: error.message }), h("button", { class: "btn", type: "button", text: "목록 다시 불러오기", onclick: loadList })));
      body.setAttribute("aria-busy", "false"); announce("목록을 불러오지 못했습니다. 다시 시도할 수 있습니다.");
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
      const open = h("button", { type: "button", class: "row", "data-record-id": r.id, "aria-current": String(r.id === S.id), onclick: () => selectRecord(r.id) },
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
  function announce(message) { $("statusLine").textContent = message; }
  async function selectRecord(id) {
    if (document.body.dataset.pane === "list") S.listScrollY = window.scrollY;
    S.id = id; S.detail = null;
    document.body.dataset.pane = "detail"; renderList(); hideError();
    if (await loadDetail()) $("detail").focus({ preventScroll: true });
  }
  async function loadDetail({ recovering = false } = {}) {
    const id = S.id, request = ++S.detailRequest, box = $("detail");
    if (!id) return false;
    box.setAttribute("aria-busy", "true");
    box.replaceChildren(backBtn(), h("div", { class: "skeleton" }), h("div", { class: "skeleton" }));
    try {
      const detail = await api("GET", "/api/records/" + enc(id));
      if (request !== S.detailRequest || id !== S.id) return false;
      if (detail.id !== id || !Number.isInteger(detail.revision)) throw new ApiError(200, { code: "invalid_response", message: "요청한 기록과 다른 응답입니다. 다시 불러오세요." });
      const wasConflicted = S.conflicts.has(id); S.conflicts.delete(id);
      const listed = S.list?.items.find(item => item.id === id);
      const listIsStale = listed && listed.revision !== detail.revision;
      if (recovering || wasConflicted) S.feedback.set(id, { kind: "success", text: "최신 기록을 불러왔습니다. 작성 중인 내용은 유지했습니다. 확인 후 다시 저장하세요." });
      applyDetail(detail); box.setAttribute("aria-busy", "false");
      announce(recordName(detail) + " 기록을 불러왔습니다.");
      if (recovering || wasConflicted || listIsStale) {
        try { await refreshBootstrap(); await loadList({ quiet: true }); }
        catch (error) { showError(new ApiError(error.status || 0, { code: "summary_refresh", message: "기록은 갱신했지만 목록 요약을 불러오지 못했습니다. 화면을 다시 불러오세요." }), { focus: false }); }
      }
      return request === S.detailRequest && id === S.id;
    } catch (error) {
      if (request !== S.detailRequest || id !== S.id) return false;
      box.setAttribute("aria-busy", "false");
      box.replaceChildren(backBtn(), h("div", { class: "empty" }, h("p", { text: "기록을 불러오지 못했습니다." }), h("p", { class: "help", text: error.message }),
        h("button", { class: "btn", type: "button", text: "기록 다시 불러오기", onclick: () => loadDetail({ recovering }) })));
      announce("기록을 불러오지 못했습니다. 입력 초안은 보관되어 있습니다.");
      return false;
    }
  }
  function backBtn() {
    return h("button", { type: "button", class: "btn back", text: "기록 목록으로", onclick: () => {
      document.body.dataset.pane = "list";
      const row = document.querySelector('#listBody [data-record-id="' + CSS.escape(S.id || "") + '"]');
      (row || $("filterSelect")).focus({ preventScroll: true }); window.scrollTo(0, S.listScrollY);
    } });
  }
  function applyDetail(detail) {
    if (!detail || detail.id !== S.id || (S.detail?.id === detail.id && detail.revision < S.detail.revision)) return false;
    S.detail = detail;
    if (S.boot && detail.workspace_revision != null) S.boot.workspace_revision = Math.max(S.boot.workspace_revision, detail.workspace_revision);
    renderDetail(); return true;
  }
  async function refreshBootstrap() {
    const request = ++S.bootRequest, boot = await api("GET", "/api/bootstrap");
    if (request !== S.bootRequest) return;
    if (S.boot && boot.workspace_revision < S.boot.workspace_revision) return;
    S.boot = boot; renderTop(); renderSummary();
  }
  async function refreshAll() {
    try { await refreshBootstrap(); await loadList(); if (S.id) await loadDetail({ recovering: true }); }
    catch (error) { showError(error); }
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
    box.setAttribute("aria-busy", "false");
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
    return h("section", { class: "card", id: "recordValues" }, h("h3", { text: "사진과 기록 값" }),
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
    const c = h("section", { class: "card group-card" }, h("h3", { text: "원본 값이 같은 사진 기록" }));
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
    c.append(h("ul", { class: "history" }, hs.map((e) => {
      const info = e.details || {};
      return h("li", {}, h("strong", { text: (L.history[e.type] || e.type) + " · " + (e.actor === "workspace_user" ? "작업공간 사용자" : val(e.actor)) }),
        h("div", { class: "muted-text", text: val(e.at) + " · 기록 버전 " + val(e.revision) }),
        info.field ? h("p", { text: fieldName(info.field) + " 적용값: " + val(info.value) }) : null,
        ...[["note", "메모"], ["reason", "보정 이유"], ["next_action", "다음 조치"], ["evidence_note", "판단 근거"], ["rule_version", "규칙 버전"]]
          .filter(([key]) => info[key]).map(([key, label]) => h("p", { text: label + ": " + info[key] })),
        h("details", {}, h("summary", { text: "변경 상세·근거 ID" }), h("pre", { class: "json", text: JSON.stringify(info, null, 2) })));
    })));
    return c;
  }

  /* ---------- record-owned drafts and writes ---------- */
  function draft(id, action, initial = {}) {
    if (!S.drafts.has(id)) S.drafts.set(id, new Map());
    const entries = S.drafts.get(id);
    if (!entries.has(action)) entries.set(action, { ...initial, owner: id, dirty: false });
    return entries.get(action);
  }
  function markDraft(dr, name, value) {
    dr[name] = value; dr.dirty = true; S.feedback.delete(dr.owner);
    if (S.id === dr.owner) $("recordFeedback")?.remove();
    const hint = $("draftStatus"); if (hint) hint.textContent = "작성 중인 내용은 이 탭에서 기록별로 보관됩니다. 새로고침하기 전에는 저장하세요.";
  }
  function inputField(id, label, dr, name, options = {}) {
    const input = h(options.area ? "textarea" : "input", {
      id, name, ...(options.area ? {} : { type: "text" }), value: dr[name] ?? "",
      maxLength: options.maxLength ?? 2000, required: !!options.required,
      "aria-required": options.required ? "true" : null, placeholder: options.placeholder || "",
      "aria-describedby": options.help ? id + "-help" : null,
    });
    input.addEventListener("input", () => {
      markDraft(dr, name, input.value); input.removeAttribute("aria-invalid");
      const error = $(id + "-error"); if (error) error.remove();
      input.setAttribute("aria-describedby", options.help ? id + "-help" : "");
    });
    return { input, wrap: h("label", { class: "field" }, h("span", { text: label }), input,
      options.help ? h("span", { id: id + "-help", class: "field-help", text: options.help }) : null) };
  }
  function validate(form, entries) {
    form.querySelectorAll(".field-error").forEach(node => node.remove());
    form.querySelectorAll('[aria-invalid="true"]').forEach(node => node.removeAttribute("aria-invalid"));
    const failures = entries.filter(entry => !entry.valid);
    for (const entry of failures) {
      const target = entry.target, errorId = target.id + "-error";
      target.setAttribute("aria-invalid", "true");
      const described = (target.getAttribute("aria-describedby") || "").split(" ").filter(value => value && !value.endsWith("-error"));
      target.setAttribute("aria-describedby", [...described, errorId].join(" "));
      target.after(h("span", { id: errorId, class: "field-error", text: entry.message }));
    }
    if (failures.length) {
      const first = failures[0].target; (first.querySelector('input') || first).focus();
      first.scrollIntoView({ block: "center" }); announce(failures[0].message); return false;
    }
    return true;
  }
  function changeAction(id, action, focusTab = true) {
    S.activeActions.set(id, action);
    if (S.id !== id || !S.detail) return;
    $("actionsCard").replaceWith(actionsCard(S.detail));
    if (focusTab) $("action-tab-" + action).focus({ preventScroll: true });
  }
  function renderBusy(id) {
    if (S.id !== id) return;
    const busy = S.pendingWrites.has(id), card = $("actionsCard"); if (!card) return;
    card.querySelectorAll(".workflow-fields").forEach(fieldset => { fieldset.disabled = busy || S.conflicts.has(id); });
    card.querySelectorAll('[role="tab"]').forEach(tab => { tab.disabled = busy; });
    const progress = $("writeProgress"); if (progress) progress.hidden = !busy;
  }
  async function submitRecord(id, action, suffix, body, message, nextAction) {
    if (S.pendingWrites.has(id) || S.conflicts.has(id)) return;
    S.pendingWrites.add(id); S.feedback.delete(id); renderBusy(id); announce("저장 중입니다. 잠시 기다려 주세요.");
    if (S.id === id) ++S.detailRequest;
    try {
      const detail = await api("POST", "/api/records/" + enc(id) + suffix, body);
      if (detail.id !== id || !Number.isInteger(detail.revision)) throw new ApiError(200, { code: "invalid_response", message: "저장 응답을 확인할 수 없습니다. 최신 기록을 먼저 불러오세요." });
      S.drafts.get(id)?.delete(action); S.conflicts.delete(id);
      const modelFailed = action === "classify" && detail.model?.status === "failed";
      const resultMessage = modelFailed ? (detail.model.message || "모델 대조에 실패했습니다. 수동 검토는 계속할 수 있습니다.") : message;
      S.feedback.set(id, { kind: modelFailed ? "error" : "success", text: resultMessage });
      if (nextAction) S.activeActions.set(id, nextAction);
      applyDetail(detail);
      announce((S.id === id ? "" : "다른 기록의 ") + resultMessage);
      try { await refreshBootstrap(); await loadList({ quiet: true }); }
      catch (error) { showError(new ApiError(error.status || 0, { code: "summary_refresh", message: "저장은 완료됐지만 목록 요약을 갱신하지 못했습니다. 목록을 다시 불러오세요." }), { focus: false }); }
    } catch (error) {
      const uncertain = error.status === 409 || error.status === 0 || error.status >= 500 || error.code === "invalid_response";
      if (uncertain) S.conflicts.add(id);
      S.feedback.set(id, { kind: "error", text: error.status === 409 ? "기록이 업데이트되었습니다. 초안은 보관했으니 최신 내용을 확인한 뒤 다시 저장하세요." : error.message, recover: uncertain });
      if (S.id === id) renderDetail(); announce("저장하지 못했거나 결과를 확인할 수 없습니다. 입력 내용은 보관했습니다.");
    } finally {
      S.pendingWrites.delete(id);
      if (S.id === id && S.detail) {
        $("actionsCard")?.replaceWith(actionsCard(S.detail));
        if (S.view === "records") { const feedback = $("recordFeedback"); if (feedback) { feedback.focus(); feedback.scrollIntoView({ block: "center" }); } }
      }
      if (S.view === "handover") renderHandover();
    }
  }
  async function send(button, fn) {
    button.disabled = true; hideError();
    try { await fn(); } catch (error) { showError(error); } finally { button.disabled = false; }
  }
  function actionsCard(d) {
    const id = d.id, rv = d.review || {}, active = S.activeActions.get(id) || "reviewed";
    const actions = [["reviewed", "검토"], ["hold", "보류"], ["correction", "보정"], ["recheck", "재검사"], ["resolve", "해소"]];
    const card = h("section", { class: "card workflow", id: "actionsCard" }, h("h3", { text: "검토 결과 남기기" }),
      h("p", { class: "help", text: "필요한 행동을 선택하세요. 검토했다는 표시와 문제를 해소했다는 판단은 다릅니다." }));
    const feedback = S.feedback.get(id);
    if (feedback) card.append(h("div", { id: "recordFeedback", class: "feedback " + feedback.kind, tabIndex: -1, role: "group", "aria-label": "저장 결과" },
      h("p", { text: feedback.text }), feedback.recover ? h("button", { type: "button", class: "btn", text: "최신 기록 불러오기 · 초안 유지", onclick: () => loadDetail({ recovering: true }) }) : null));
    card.append(h("div", { class: "action-tabs", role: "tablist", "aria-label": "기록에 남길 행동" }, actions.map(([key, label], index) => h("button", {
      type: "button", id: "action-tab-" + key, role: "tab", "data-action": key, "aria-controls": "actionPanel",
      "aria-selected": String(active === key), tabIndex: active === key ? 0 : -1, disabled: S.pendingWrites.has(id), text: label,
      onclick: () => changeAction(id, key), onkeydown: event => {
        const offsets = { ArrowRight: 1, ArrowLeft: -1 };
        let target = null;
        if (event.key in offsets) target = (index + offsets[event.key] + actions.length) % actions.length;
        if (event.key === "Home") target = 0; if (event.key === "End") target = actions.length - 1;
        if (target !== null) { event.preventDefault(); changeAction(id, actions[target][0]); }
      },
    }))));
    card.append(h("p", { id: "writeProgress", class: "write-progress", hidden: !S.pendingWrites.has(id), text: "요청을 처리하고 있습니다. 중복 저장을 막기 위해 이 기록의 입력을 잠시 잠급니다. 다른 기록은 계속 살펴볼 수 있습니다." }));
    const panel = h("div", { id: "actionPanel", role: "tabpanel", "aria-labelledby": "action-tab-" + active });
    const busy = S.pendingWrites.has(id) || S.conflicts.has(id), nonempty = value => typeof value === "string" && value.trim().length > 0;
    const base = { expected_revision: d.revision };
    const form = (formId, submit) => h("form", { id: formId, class: "action-form", novalidate: true, onsubmit: event => { event.preventDefault(); if (!S.pendingWrites.has(id) && !S.conflicts.has(id)) submit(event.currentTarget); } });
    if (active === "reviewed") {
      const dr = draft(id, active, { note: "" }), note = inputField("reviewNote", "살펴본 내용 (선택)", dr, "note", { area: true, placeholder: "확인한 사진이나 원문 내용을 남겨주세요." });
      const alreadyReviewed = rv.review_state === "reviewed";
      const submit = h("button", { type: "submit", class: "btn primary", disabled: alreadyReviewed && !nonempty(dr.note), text: alreadyReviewed ? "추가 검토 메모 저장" : "검토함으로 저장" });
      note.input.addEventListener("input", () => { submit.disabled = alreadyReviewed && !nonempty(dr.note); });
      const f = form("reviewForm", f => { if (validate(f, [{ target: note.input, valid: !alreadyReviewed || nonempty(dr.note), message: "이미 검토한 기록입니다. 추가로 확인한 내용이 있다면 입력하세요." }])) submitRecord(id, active, "/review", { ...base, action: "reviewed", note: (dr.note || "").trim() }, "검토함으로 저장했습니다. 해소 상태는 바뀌지 않았습니다."); });
      f.append(h("fieldset", { class: "workflow-fields", disabled: busy }, h("legend", { text: "검토 기록" }), h("p", { class: "help", text: "사람이 살펴봤다는 표시만 남깁니다. 미해결 기록은 그대로 전달됩니다." }), note.wrap, alreadyReviewed ? h("p", { class: "help", text: "이미 검토한 기록입니다. 추가 메모가 있을 때만 다시 저장하세요." }) : null, submit)); panel.append(f);
    } else if (active === "hold") {
      const dr = draft(id, active, { reason: rv.hold?.reason || "", next: rv.hold?.next_action || "" });
      const reason = inputField("holdReason", "보류 이유 (필수)", dr, "reason", { area: true, required: true, placeholder: "무엇이 불확실한가요?" });
      const next = inputField("holdNext", "다음 담당자가 확인할 일 (필수)", dr, "next", { area: true, required: true, placeholder: "누가 어떤 원문이나 측정 로그를 확인해야 하나요?" });
      const unchanged = () => rv.resolution_state === "held" && (dr.reason || "").trim() === (rv.hold?.reason || "") && (dr.next || "").trim() === (rv.hold?.next_action || "");
      const submit = h("button", { type: "submit", class: "btn primary", disabled: unchanged(), text: rv.resolution_state === "held" ? "보류 내용 변경 저장" : "보류하고 질문 저장" });
      for (const input of [reason.input, next.input]) input.addEventListener("input", () => { submit.disabled = unchanged(); });
      const f = form("holdForm", f => { if (validate(f, [{ target: reason.input, valid: nonempty(dr.reason), message: "보류 이유를 입력하세요." }, { target: next.input, valid: nonempty(dr.next), message: "다음 담당자가 확인할 일을 입력하세요." }])) submitRecord(id, active, "/review", { ...base, action: "hold", note: dr.reason.trim(), next_action: dr.next.trim() }, "보류 이유와 다음 확인 항목을 저장했습니다."); });
      f.append(h("fieldset", { class: "workflow-fields", disabled: busy }, h("legend", { text: "질문을 남기고 보류" }), h("p", { class: "help", text: "답을 확신할 수 없다면 미해결 질문으로 남깁니다. 인수인계에도 포함됩니다." }), reason.wrap, next.wrap, submit)); panel.append(f);
    } else if (active === "correction") {
      const options = d.correctable_fields || [], dr = draft(id, active, { field: options[0]?.field || "", value: "", reason: "" });
      const select = h("select", { id: "correctionField", name: "field", value: dr.field, onchange: event => markDraft(dr, "field", event.target.value) }, options.map(option => h("option", { value: option.field, selected: dr.field === option.field, text: option.label || fieldName(option.field) })));
      const value = inputField("correctionValue", "기록할 보정값 (필수)", dr, "value", { required: true, maxLength: 200, help: "0도 입력할 수 있습니다. 원문은 바뀌지 않고 보정값을 따로 기록합니다." });
      const reason = inputField("correctionReason", "보정 이유 (필수)", dr, "reason", { area: true, required: true, placeholder: "어떤 근거로 값을 보정하나요?" });
      const f = form("correctionForm", f => { if (validate(f, [{ target: value.input, valid: nonempty(dr.value), message: "기록할 보정값을 입력하세요. 0도 가능합니다." }, { target: reason.input, valid: nonempty(dr.reason), message: "보정 이유를 입력하세요." }])) submitRecord(id, active, "/corrections", { ...base, field: dr.field, value: dr.value, reason: dr.reason.trim() }, "원문을 보존하고 보정값을 저장했습니다. 현재 버전으로 재검사하세요.", "recheck"); });
      f.append(h("fieldset", { class: "workflow-fields", disabled: busy }, h("legend", { text: "원문을 보존하고 값 보정" }), h("p", { class: "help", text: "보정하면 이전 검토·해소 판단이 무효화됩니다. 저장 후 다시 검사해야 합니다." }), h("label", { class: "field" }, h("span", { text: "보정할 항목" }), select), value.wrap, reason.wrap, h("button", { type: "submit", class: "btn primary", disabled: !options.length, text: "보정값 저장 · 원문 보존" }))); panel.append(f);
    } else if (active === "recheck") {
      const f = form("recheckForm", () => submitRecord(id, active, "/recheck", base, "현재 버전의 검사를 마쳤습니다. 결과를 확인한 뒤 사람이 보류 또는 해소를 판단하세요."));
      f.append(h("fieldset", { class: "workflow-fields", disabled: busy }, h("legend", { text: "현재 값으로 다시 검사" }), h("p", { class: "help", text: rv.recheck_needed ? "현재 버전의 재검사가 필요합니다. 재검사만으로 해소되지는 않습니다." : "현재 버전의 재검사를 마쳤습니다. 결과가 맞는지 사람이 확인하세요." }),
        h("button", { type: "submit", class: "btn primary", disabled: !rv.recheck_needed, text: rv.recheck_needed ? "현재 버전 재검사" : "현재 버전 재검사 완료" }), h("button", { type: "button", class: "btn", disabled: rv.recheck_needed, text: "해소 조건 확인", onclick: () => changeAction(id, "resolve") }),
        h("details", { class: "optional-model" }, h("summary", { text: "사진 모델로 대조하기" }), h("p", { class: "help", text: S.boot?.mode === "synthetic" ? "합성 시연 사진에는 실제 모델을 호출하지 않습니다. 위 수동 검토와 재검사는 사용할 수 있습니다." : "모델은 다시 볼 이유를 제공합니다. 신뢰도가 정답 확률이나 진단 결과를 뜻하지 않습니다." }),
          h("button", { type: "button", class: "btn", disabled: S.boot?.mode === "synthetic", text: "사진 모델 대조 요청", onclick: () => submitRecord(id, "classify", "/classify", base, "모델 요청을 마쳤습니다. 결과 상태와 원문을 확인하세요.") })))); panel.append(f);
    } else if (active === "resolve") {
      const dr = draft(id, active, { note: rv.resolution?.evidence_note || "", refs: [...(rv.resolution?.evidence_refs || [])] }), candidates = d.evidence_candidates || [], allowed = new Set(candidates.map(entry => entry.ref));
      dr.refs = (dr.refs || []).filter(ref => allowed.has(ref));
      const note = inputField("resolveNote", "해소 근거 설명 (필수)", dr, "note", { area: true, required: true, placeholder: "어떤 근거를 확인해 더 이상 미해결로 남기지 않나요?" });
      const refs = h("fieldset", { id: "resolveEvidence", class: "evidence-options", tabIndex: -1 }, h("legend", { text: "관련 근거 (1개 이상 필수)" }), h("p", { class: "help", text: "현재 확인 사유와 관련된 출처를 골라주세요. 모델 근거는 원본 JSON과 구분합니다." }));
      const evidenceLabel = entry => entry.kind === "model" ? "모델 실행 · " + (L.origin[entry.origin] || entry.origin) : fieldName(entry.ref.split(":").at(-1)) + " 원문";
      const unchanged = () => rv.resolution_state === "resolved" && (dr.note || "").trim() === (rv.resolution?.evidence_note || "") && [...dr.refs].sort().join("|") === [...(rv.resolution?.evidence_refs || [])].sort().join("|");
      const submit = h("button", { type: "submit", class: "btn primary", disabled: rv.recheck_needed || unchanged(), text: rv.resolution_state === "resolved" ? "해소 근거 변경 저장" : "근거를 남기고 해소 처리" });
      note.input.addEventListener("input", () => { submit.disabled = rv.recheck_needed || unchanged(); });
      for (const entry of candidates) refs.append(h("label", { class: "check" }, h("input", { type: "checkbox", value: entry.ref, checked: dr.refs.includes(entry.ref), onchange: event => { const selected = new Set(dr.refs); if (event.target.checked) selected.add(entry.ref); else selected.delete(entry.ref); markDraft(dr, "refs", [...selected]); submit.disabled = rv.recheck_needed || unchanged(); refs.removeAttribute("aria-invalid"); $("resolveEvidence-error")?.remove(); } }), h("span", { text: evidenceLabel(entry) })));
      refs.append(h("details", { class: "evidence-identifiers" }, h("summary", { text: "근거 식별자 확인" }), ...candidates.map(entry => h("p", {}, h("strong", { text: evidenceLabel(entry) }), h("code", { class: "full-reference", text: entry.ref })))));
      const f = form("resolveForm", f => {
        const matched = (d.checks || []).filter(check => check.outcome === "matched");
        const related = matched.every(check => (check.evidence_refs || []).some(ref => dr.refs.includes(ref)));
        const modelRefs = matched.flatMap(check => check.evidence_refs || []).filter(ref => ref.startsWith("model:"));
        const modelRelated = !modelRefs.length || modelRefs.some(ref => dr.refs.includes(ref));
        if (validate(f, [{ target: note.input, valid: nonempty(dr.note), message: "해소 근거 설명을 입력하세요." }, { target: refs, valid: dr.refs.length > 0 && related && modelRelated, message: "현재 확인 사유와 관련된 근거를 선택하세요. 모델 불일치에는 모델 실행 근거도 필요합니다." }])) submitRecord(id, active, "/review", { ...base, action: "resolve", evidence_note: dr.note.trim(), evidence_refs: dr.refs }, "사람이 근거를 기록해 해소 처리했습니다. 검사 결과와 변경 이력은 그대로 남습니다.");
      });
      f.append(h("fieldset", { class: "workflow-fields", disabled: busy }, h("legend", { text: "근거를 남기고 해소 판단" }),
        rv.recheck_needed ? h("div", { class: "inline-warning" }, h("p", { text: "현재 버전의 재검사를 먼저 실행하세요. 작성한 설명은 보관됩니다." }), h("button", { type: "button", class: "btn", text: "재검사 화면으로", onclick: () => changeAction(id, "recheck") })) : null,
        note.wrap, refs, submit)); panel.append(f);
    }
    card.append(panel, h("p", { id: "draftStatus", class: "help draft-status", text: draft(id, active).dirty ? "작성 중인 내용은 이 탭에서 기록별로 보관됩니다. 새로고침하기 전에는 저장하세요." : "작성한 내용은 다른 기록으로 이동해도 이 탭에서 유지됩니다." }),
      h("button", { type: "button", class: "btn link", disabled: S.pendingWrites.has(id), text: "인수인계로 이동", onclick: () => setView("handover") }));
    return card;
  }

  function renderHandover() {
    const v = $("viewHandover");
    v.replaceChildren();
    const status = h("p", { id: "handoverStatus", role: "status", tabindex: "-1", text: S.handoverMessage });
    const mode = h("fieldset", { class: "handover-scope", disabled: S.handoverPending }, h("legend", { text: "포함할 기록" }));
    const b = h("button", { type: "button", class: "btn primary", text: "현재 내용으로 고정본 만들기", onclick: async () => {
      if (S.handoverPending) return;
      S.handoverPending = true; b.disabled = true; mode.disabled = true;
      S.handoverMessage = "인수인계 고정본을 저장하고 있습니다."; status.textContent = S.handoverMessage;
      let saved = false;
      try {
        const ids = S.handoverScope === "picked" ? [...S.picked] : null;
        if (ids && !ids.length) throw new ApiError(0, { code: "validation", message: "체크한 기록이 없습니다. 기록 화면에서 선택하세요." });
        const handover = await api("POST", "/api/handovers", { expected_workspace_revision: S.boot.workspace_revision, record_ids: ids });
        if (typeof handover.id !== "string" || !Number.isInteger(handover.workspace_revision) || !Array.isArray(handover.questions)) throw new ApiError(200, { code: "invalid_response", message: "저장 응답을 확인할 수 없습니다. 아래에 이전 고정본이 있다면 그대로 유지됩니다." });
        S.handover = handover; saved = true; S.handoverMessage = "인수인계 고정본을 저장했습니다.";
      } catch (error) {
        S.handoverMessage = error.status === 409 ? "작업 내용이 변경되었습니다. 최신 버전을 불러왔습니다. 포함 범위를 확인하고 다시 만드세요." : error.message;
        if (error.status === 409) {
          try { await refreshBootstrap(); } catch (refreshError) { S.handoverMessage = refreshError.message; }
        }
      } finally {
        S.handoverPending = false; announce(S.handoverMessage);
        if (S.view === "handover") { renderHandover(); $(saved ? "handoverResult" : "handoverStatus")?.focus(); }
      }
    } });
    for (const [value, label, help] of [
      ["auto", "후보와 작업한 기록 모두", "현재 확인 후보와 검토·보류·보정·해소한 기록을 함께 전달합니다."],
      ["picked", "목록에서 체크한 기록 " + S.picked.size + "건", "검색이나 페이지를 바꿔도 체크한 기록을 포함합니다."]]) {
      mode.append(h("label", { class: "scope-option" },
        h("input", { type: "radio", name: "handoverScope", value, checked: S.handoverScope === value, onchange: () => {
          S.handoverScope = value; b.disabled = value === "picked" && !S.picked.size;
          S.handoverMessage = b.disabled ? "기록 화면의 체크박스로 포함할 기록을 먼저 선택하세요." : ""; status.textContent = S.handoverMessage;
        } }), h("span", {}, h("strong", { text: label }), h("span", { class: "muted-text", text: help }))));
    }
    b.disabled = S.handoverPending || (S.handoverScope === "picked" && !S.picked.size);
    v.append(h("div", { class: "stack" }, h("section", { class: "card" }, h("h2", { text: "인수인계" }),
      h("p", { class: "muted-text", text: "만들어진 스냅샷은 변경되지 않습니다. 이후 수정은 새 스냅샷으로 남습니다. 보류·열림 기록도 미해결로 그대로 전달됩니다." }),
      mode, h("div", { class: "toolbar" }, b, h("button", { type: "button", class: "btn", text: "기록 선택으로 돌아가기", onclick: () => setView("records") })), status)));
    const hd = S.handover;
    if (!hd) { v.firstChild.append(h("div", { class: "empty" }, h("p", { text: "아직 만든 스냅샷이 없습니다." }))); return; }
    const sc = hd.scope || {}, ct = hd.counts || {};
    const dl = (f) => h("a", { class: "btn", href: BASE + "/api/handovers/" + enc(hd.id) + "/download?format=" + f, text: f.toUpperCase() + " 내려받기", download: "" });
    v.firstChild.append(h("section", { class: "card", id: "handoverResult", tabindex: "-1" }, h("h3", { text: "저장된 인수인계 고정본" }),
      identifier(hd.id, "고정본 ID"),
      h("p", { class: "muted-text", text: hd.workspace_revision < S.boot.workspace_revision ? "저장 후 작업 내용이 변경되었습니다. 아래 다운로드는 저장 당시 내용입니다. 최신 내용은 새 고정본으로 만드세요." : "아래 파일은 모두 같은 저장 시점의 내용입니다. JSON은 전체 근거, CSV는 표, HTML은 읽기용 보고서입니다." }),
      h("dl", { class: "kv" }, h("dt", { text: "생성" }), h("dd", { text: val(hd.created_at) }), h("dt", { text: "작업공간 버전" }), h("dd", { text: val(hd.workspace_revision) }),
        h("dt", { text: "범위" }), h("dd", { text: val(sc.description) + " · " + val(sc.count) + "건 (전체 식물을 점검했다는 뜻이 아닙니다)" }),
        h("dt", { text: "열림/보류/재검토/해소" }), h("dd", { text: [ct.open, ct.held, ct.needs_review, ct.resolved].map(val).join(" / ") }),
        h("dt", { text: "규칙 버전" }), h("dd", { text: val(hd.rule_version) }), h("dt", { text: "SHA-256" }), h("dd", { class: "mono", text: val(hd.sha256) })),
      h("div", { class: "toolbar" }, dl("json"), dl("csv"), dl("html"))),
      h("section", { class: "card" }, h("h3", { text: "미해결 질문" }),
        (hd.questions || []).length ? h("ul", { class: "history" }, hd.questions.map((q) => h("li", {}, identifier(q.record_id, "기록 ID"), h("strong", { text: L.resolution[q.state] || val(q.state) }), h("div", { text: val(q.question) }), q.next_action && q.next_action !== q.question ? h("div", { class: "muted-text", text: "다음 조치: " + q.next_action }) : null)))
          : h("p", { class: "muted-text", text: "이 스냅샷에 남은 질문이 없습니다." })));
  }

  /* ---------- rules & agent ---------- */
  function renderRules() {
    const v = $("viewRules"); v.replaceChildren();
    const reg = (S.boot && S.boot.rule_registry) || [];
    const ctxOut = h("pre", { class: "json", hidden: true });
    const cb = h("button", { type: "button", class: "btn", text: "읽기 전용 컨텍스트 내보내기", onclick: () => send(cb, async () => {
      const r = await api("POST", "/api/agent/context", { record_ids: S.picked.size ? [...S.picked] : null });
      ctxOut.textContent = JSON.stringify(r, null, 2); ctxOut.hidden = false;
    }) });
    v.append(h("div", { class: "stack" },
      h("section", { class: "card" }, h("h2", { text: "AgentStream" }),
        h("div", { class: "agent-box" },
          h("p", {}, badge("AI 연동 예정", "muted", "○ ")),
          h("p", { text: "에이전트 질의는 아직 연결되지 않았습니다. 수동 검토와 인수인계는 사용할 수 있습니다." }),
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
    $("detail").tabIndex = -1; $("listBody").tabIndex = -1;
    renderDetail();
    try {
      await refreshBootstrap();
    } catch (e) { showError(e); $("topBadges").replaceChildren(badge("서버 연결 실패", "bad", "▲ ")); }
    await loadList();
  }
  init();
})();
