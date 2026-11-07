"use strict";
(() => {
  const metaBase = document.querySelector('meta[name="farmcheck-base-path"]');
  const rawBase = metaBase ? metaBase.content : "";
  const BASE = rawBase.includes("__BASE_PATH__") ? "" : rawBase.replace(/\/+$/, "");

  const L = {
    origin: { uploaded: "내 자료", serverzip: "서버 연결 자료", actual: "실제 기록", injected: "주입 시험", recorded: "재생 기록", synthetic: "합성 예시" },
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
    boot: null, scope: "actual", filter: "all", q: "", page: 1, pageSize: 25, list: null,
    id: null, detail: null, picked: new Set(), drafts: new Map(), handover: null, view: "records",
    pendingWrites: new Set(), conflicts: new Set(), feedback: new Map(), activeActions: new Map(),
    quality: new Map(), listRequest: 0, detailRequest: 0, bootRequest: 0, listScrollY: 0, handoverScope: "current",
    handoverPending: false, handoverMessage: "",
    classifier: null, classifierPending: false, classifierError: "", importPending: false, importReceipt: null, importUncertain: false, importedRevisions: new Map(),
    reports: null, reportsPending: false, reportsError: "", reportOpening: false,
    counts: { actual: null, demo: null }, diffCount: null, diffReq: 0, classifying: new Set(), reportDetails: new Map(), lbOpener: null, advOpen: new Map(), imp: newImport(), completionReport: null, completionPending: false, completionMessage: "", flash: "",
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
  const val = (v) => (v === null || v === undefined || v === "" ? "미확인" : String(v));
  const enc = encodeURIComponent;
  const fieldName = (key) => L.fields[key] || key;
  const workflowLabel = (record) => record.resolution_state === "held" ? "보류" : record.resolution_state === "resolved" ? "근거 확인·해소" : record.review_state === "reviewed" ? "검토 기록됨" : "검수 필요";
  const shortId = (id) => String(id || "").slice(0, 10);
  const recordName = (r) => L.scenarios[r.scenario] || ((r.label && !/[\\/]|\.(jpg|jpeg|json)$/i.test(r.label)) ? r.label : (r.crop || r.class_name || "작물 미확인") + (r.growth_week != null ? " · " + r.growth_week + "주" : " · 사진 기록"));
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
  async function api(method, path, body, allowArray = false) {
    const controller = new AbortController();
    const timeoutMs = S.importPending ? 30000 : 120000;
    let timer;
    const deadline = new Promise((resolve, reject) => {
      timer = setTimeout(() => {
        reject(new ApiError(0, { code: "timeout", message: "응답 확인 시간이 초과되었습니다. 저장 요청은 완료되었을 수 있습니다. 자동 재시도하지 않았습니다. 저장 결과를 다시 확인하세요." }));
        controller.abort();
      }, timeoutMs);
    });
    try {
      let res;
      try {
        res = await Promise.race([fetch(BASE + path, {
          method, credentials: "same-origin", cache: "no-store", signal: controller.signal,
          headers: Object.assign({ Accept: "application/json" }, body !== undefined ? { "Content-Type": "application/json" } : {}),
          body: body !== undefined ? JSON.stringify(body) : undefined,
        }), deadline]);
      } catch (error) {
        if (error instanceof ApiError) throw error;
        throw new ApiError(0, { code: "network", message: "서버에 연결하지 못했습니다. 연결을 확인한 뒤 다시 시도하세요." });
      }
      let data = null, text;
      try { text = await Promise.race([res.text(), deadline]); }
      catch (error) {
        if (error instanceof ApiError) throw error;
        throw new ApiError(0, { code: "network", message: "응답을 받는 중 연결이 끊겼습니다. 저장 여부를 확인하려면 최신 내용을 다시 불러오세요." });
      }
      if (text) { try { data = JSON.parse(text); } catch (error) { data = null; } }
      if (!res.ok) throw new ApiError(res.status, data || { message: "서버가 JSON이 아닌 응답을 보냈습니다 (HTTP " + res.status + ")." });
      if (!data || typeof data !== "object" || (Array.isArray(data) && !allowArray)) throw new ApiError(res.status, { code: "invalid_response", message: "서버 응답을 읽을 수 없습니다. 최신 내용을 다시 불러와 확인하세요." });
      return data;
    } finally { clearTimeout(timer); }
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


  /* ---------- status vocabulary, nav, progress ---------- */
  const STATE_TEXT = { wait: "대기", diff: "차이 있음", hold: "보류", done: "판단 완료" };
  const num = (v) => (v === null || v === undefined ? "-" : String(v));
  const scopeName = () => (S.scope === "demo" ? "시연 예제" : "내 실제 자료");
  function stateOf(r, comparison) {
    if (r.resolution_state === "resolved") return "done";
    if (r.resolution_state === "held") return "hold";
    if (comparison?.status === "different" || r.candidate || (r.matched_rules || []).length) return "diff";
    return "wait";
  }
  const isPending = (r) => { const s = stateOf(r); return s === "wait" || s === "diff"; };
  function detailState(d) {
    const matched = (d.checks || []).filter((check) => check.outcome === "matched");
    return stateOf({ resolution_state: d.review?.resolution_state, candidate: matched.length > 0, matched_rules: matched.map((check) => check.rule_id) }, d.comparison);
  }
  function pendingCount() {
    const s = S.list?.summary;
    return s ? Math.max(0, (s.total || 0) - (s.held || 0) - (s.resolved || 0)) : 0;
  }
  function progressText() {
    const s = S.list?.summary;
    return s ? s.total + "건 중 " + ((s.resolved || 0) + (s.held || 0)) + "건 판단" : "";
  }
  function flash(text) { S.flash = text; renderSummary(); announce(text); }

  function renderTop() {
    $("topBadges").replaceChildren(S.boot ? badge("비공개 작업공간", "muted") : "");
    $("listTitle").textContent = scopeName();
    document.querySelectorAll("[data-scope]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.scope === S.scope)));
  }

  function renderSummary() {
    const s = S.list?.summary || {}, box = $("summary");
    document.querySelectorAll("[data-fcount]").forEach((el) => { el.textContent = num(el.dataset.fcount === "all" ? s.total : el.dataset.fcount === "candidates" ? S.diffCount : s[el.dataset.fcount]); });
    document.querySelectorAll("[data-count]").forEach((el) => { el.textContent = num(S.counts[el.dataset.count]); });
    document.querySelectorAll("[data-filter]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.filter === S.filter)));
    $("progress").textContent = progressText();
    const detailProgress = $("detailProgress"); if (detailProgress) detailProgress.textContent = progressText();
    box.replaceChildren();
    if (S.flash) box.append(h("p", { id: "flash", class: "flash", text: S.flash }));
    if (S.boot?.source_problems?.length) box.append(h("details", { class: "stat-note" },
      h("summary", { text: "원본 연결·해석 문제 " + S.boot.source_problems.length + "개 (검토 목록과 별도 보존)" }),
      ...S.boot.source_problems.map((p) => h("p", { text: p.member_path + " · " + p.kind + " · " + p.message + " · SHA256 " + p.raw_hash }))));
  }
  async function loadScopeCounts() {
    try {
      const [actual, demo] = await Promise.all(["actual", "demo"].map((scope) => api("GET", "/api/records?scope=" + scope + "&page_size=1")));
      S.counts = { actual: actual.summary?.total ?? actual.total ?? null, demo: demo.summary?.total ?? demo.total ?? null };
      renderSummary();
    } catch (error) { /* counts are decoration; the list itself reports its own load errors */ }
  }

  /* ---------- list ---------- */
  /* "차이 있음" counts only open candidates: held and resolved ones are excluded client-side. */
  async function fetchOpenCandidates(q) {
    const items = []; let summary = null;
    for (let page = 1; page <= 60; page++) {
      const res = await api("GET", "/api/records?scope=" + enc(S.scope) + "&filter=candidates&page=" + page + "&page_size=100" + (q ? "&q=" + enc(q) : ""));
      summary = summary || res.summary;
      items.push(...res.items.filter((item) => item.resolution_state === "open"));
      if (!res.items.length || page * 100 >= (res.total || 0)) break;
    }
    return { items, summary };
  }
  async function openCandidateList() {
    const scope = S.scope, all = await fetchOpenCandidates(S.q), size = S.pageSize, start = (S.page - 1) * size;
    if (!S.q && scope === S.scope) S.diffCount = all.items.length;
    return { items: all.items.slice(start, start + size), page: S.page, page_size: size, total: all.items.length, summary: all.summary };
  }
  async function loadDiffCount() {
    const request = ++S.diffReq, scope = S.scope;
    try {
      const all = await fetchOpenCandidates("");
      if (request !== S.diffReq || scope !== S.scope) return;
      S.diffCount = all.items.length; renderSummary();
    } catch (error) { /* the count is secondary; list errors are reported by the list itself */ }
  }
  const EMPTY_TEXT = { all: "이 보기에 해당하는 기록이 없습니다.", candidates: "차이가 남은 기록이 없습니다.", held: "보류한 기록이 없습니다.", resolved: "판단 완료한 기록이 없습니다." };
  function renderListTools() {
    const box = $("listTools"), items = S.list?.items || [];
    box.replaceChildren();
    if (!items.length) return;
    const n = items.filter((item) => S.picked.has(item.id)).length;
    const all = h("input", { type: "checkbox", "aria-label": "이 페이지 기록 전체 선택", ...(n === items.length ? { checked: "" } : {}),
      onchange: (event) => { for (const item of items) event.target.checked ? S.picked.add(item.id) : S.picked.delete(item.id); renderList(); updatePicked(); $("listTools").querySelector("input")?.focus(); } });
    all.indeterminate = n > 0 && n < items.length;
    box.append(h("label", { class: "check pick select-all" }, all, h("span", { text: "전체 선택" })), h("span", { class: "k", text: n + "개 선택" }));
  }
  async function loadList({ quiet = false } = {}) {
    const request = ++S.listRequest, body = $("listBody");
    body.setAttribute("aria-busy", "true");
    $("pager").querySelectorAll("button").forEach((button) => { button.disabled = true; });
    body.replaceChildren(h("div", { class: "skeleton" }), h("div", { class: "skeleton" }));
    try {
      const qs = "scope=" + enc(S.scope) + "&filter=" + enc(S.filter) + "&page=" + S.page + "&page_size=" + S.pageSize + (S.q ? "&q=" + enc(S.q) : "");
      const list = S.filter === "candidates" ? await openCandidateList() : await api("GET", "/api/records?" + qs);
      if (request !== S.listRequest) return;
      if (!Array.isArray(list.items)) throw new ApiError(200, { code: "invalid_response", message: "기록 목록 응답이 올바르지 않습니다." });
      S.list = list;
      if (list.summary && Number.isInteger(list.summary.total)) S.counts[S.scope] = list.summary.total;
      renderList(); renderSummary(); if (!S.id) renderDetail();
      if (!(S.filter === "candidates" && !S.q)) loadDiffCount();
      if (!quiet) announce("목록에 " + list.items.length + "개 기록을 표시했습니다.");
      return true;
    } catch (error) {
      if (request !== S.listRequest) return;
      body.replaceChildren(h("div", { class: "empty" }, h("p", { text: "목록을 불러오지 못했습니다." }),
        h("p", { class: "help", text: error.message }), h("button", { class: "btn", type: "button", text: "목록 다시 불러오기", onclick: loadList })));
      body.setAttribute("aria-busy", "false"); announce("목록을 불러오지 못했습니다. 다시 시도할 수 있습니다.");
      return false;
    }
  }

  function renderList() {
    const body = $("listBody"), pager = $("pager"), data = S.list || { items: [] };
    body.setAttribute("aria-busy", "false");
    body.replaceChildren(); pager.replaceChildren(); renderListTools();
    if (!data.items.length) {
      const emptyActual = S.scope === "actual" && S.list?.summary?.total === 0;
      body.append(h("div", { class: "empty" }, h("p", { text: S.q ? "검색 결과가 없습니다." : emptyActual ? "아직 가져온 자료가 없습니다." : (EMPTY_TEXT[S.filter] || EMPTY_TEXT.all) }),
        emptyActual || (S.filter === "all" && !S.q) ? null : h("button", { type: "button", class: "btn", text: "전체 기록 보기", onclick: () => { S.filter = "all"; S.q = ""; S.page = 1; $("searchInput").value = ""; renderSummary(); loadList(); } })));
      return;
    }
    for (const r of data.items) {
      const st = stateOf(r);
      const open = h("button", { type: "button", class: "row", "data-record-id": r.id, "aria-current": String(r.id === S.id), onclick: () => selectRecord(r.id) },
        h("span", { class: "st " + st, "aria-hidden": "true" }),
        h("span", { class: "row-main" }, h("span", { class: "rt", text: recordName(r), title: r.label || r.id }), h("span", { class: "rs", text: r.label || shortTime(r.env_date_raw) })),
        h("span", { class: "tag " + st, text: STATE_TEXT[st] }));
      const cb = h("label", { class: "check pick" }, h("input", { type: "checkbox", "aria-label": (r.label || r.id) + " 보고서 범위에 포함", ...(S.picked.has(r.id) ? { checked: "" } : {}),
        onchange: (ev) => { ev.target.checked ? S.picked.add(r.id) : S.picked.delete(r.id); updatePicked(); } }));
      body.append(h("div", { class: "row-wrap" }, cb, open));
    }
    const pages = Math.max(1, Math.ceil((data.total || 0) / (data.page_size || S.pageSize)));
    pager.append(
      h("button", { type: "button", class: "btn", text: "이전", disabled: S.page <= 1, onclick: () => { S.page--; loadList(); } }),
      h("span", { text: S.page + " / " + pages + "쪽 · 총 " + val(data.total) + "건" }),
      h("button", { type: "button", class: "btn", text: "다음", disabled: S.page >= pages, onclick: () => { S.page++; loadList(); } }));
    body.querySelector('[aria-current="true"]')?.scrollIntoView({ block: "nearest" });
  }
  function updatePicked() {
    $("selectedReport").textContent = "체크한 " + S.picked.size + "건 보고서"; renderListTools();
    $("selectedReport").disabled = !S.picked.size;
  }
  function openFirstPending() {
    if (S.id || !S.list || !window.matchMedia("(min-width: 761px)").matches) return;
    const hit = S.list.items.find(isPending);
    if (hit) selectRecord(hit.id);
  }
  async function selectScope(scope) {
    if (scope === S.scope) return;
    S.scope = scope; S.filter = "all"; S.q = ""; S.page = 1; S.id = null; S.detail = null; S.list = null; S.flash = ""; S.diffCount = null;
    S.picked.clear(); S.handoverScope = "current"; ++S.detailRequest;
    $("searchInput").value = ""; document.body.dataset.pane = "list";
    updatePicked(); renderTop(); renderSummary(); renderDetail(); await loadList(); openFirstPending();
  }

  function detailSkeleton() {
    const bone = (cls) => h("div", { class: "skeleton " + cls });
    return h("div", { class: "doc", "aria-hidden": "true" }, bone("sk-title"), bone("sk-sub"), bone("sk-photo"), h("div", { class: "cmp" }, bone("sk-cell"), bone("sk-cell")), bone("sk-line"));
  }
  const propsSkeleton = () => [h("div", { class: "skeleton sk-card" }), h("div", { class: "skeleton sk-card tall" })];
  function updateTitle() {
    const part = S.view === "handover" ? "보고서" : S.view === "rules" ? "사용 안내" : S.detail ? recordName(S.detail) : scopeName();
    document.title = part + " — 팜체크";
  }
  function announce(message) { $("statusLine").textContent = message; }

  /* ---------- selection ---------- */
  function setMode() {
    const total = S.list?.summary?.total;
    $("ws").dataset.mode = S.id ? "work" : (S.scope === "actual" && total === 0 && !S.q ? "empty" : "browse");
  }
  async function selectRecord(id) {
    if (document.body.dataset.pane === "list") S.listScrollY = window.scrollY;
    S.id = id; S.detail = null;
    window.scrollTo(0, 0);
    document.body.dataset.pane = "detail"; $("props").replaceChildren(...propsSkeleton()); renderMbar(); setMode(); renderList(); hideError();
    const loaded = await loadDetail();
    if (loaded) $("detail").focus({ preventScroll: true });
    return loaded;
  }
  async function scopeRecordIds() {
    const ids = [];
    for (let page = 1; page <= 200; page++) {
      const res = await api("GET", "/api/records?scope=" + enc(S.scope) + "&filter=all&page=" + page + "&page_size=100");
      ids.push(...res.items.map((item) => item.id));
      if (!res.items.length || ids.length >= (res.total || 0)) break;
    }
    return ids;
  }
  async function findPending(skipId) {
    for (let page = 1; page <= 60; page++) {
      const res = await api("GET", "/api/records?scope=" + enc(S.scope) + "&filter=all&page=" + page + "&page_size=100");
      const hit = res.items.find((item) => item.id !== skipId && isPending(item));
      if (hit) return hit.id;
      if (page * 100 >= (res.total || 0)) break;
    }
    return null;
  }
  async function openNextPending() {
    try {
      const id = await findPending(S.id);
      if (id) await selectRecord(id); else announce("남은 대기 기록이 없습니다.");
    } catch (error) { showError(error); }
  }


  async function loadDetail({ recovering = false } = {}) {
    const id = S.id, request = ++S.detailRequest, box = $("detail");
    if (!id) return false;
    box.setAttribute("aria-busy", "true");
    box.replaceChildren(backBtn(), detailSkeleton());
    try {
      const detail = await api("GET", "/api/records/" + enc(id));
      if (request !== S.detailRequest || id !== S.id) return false;
      if (detail.id !== id || !Number.isInteger(detail.revision)) throw new ApiError(200, { code: "invalid_response", message: "요청한 기록과 다른 응답입니다. 다시 불러오세요." });
      if (detail.revision < (S.importedRevisions.get(id) ?? 0)) throw new ApiError(200, { code: "stale_import", message: "가져오기 응답보다 이전 버전입니다. 다시 불러오세요." });
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
      if (recovering || wasConflicted) {
        const feedback = $("recordFeedback");
        if (feedback) { feedback.focus({ preventScroll: true }); feedback.scrollIntoView({ block: "center" }); }
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
      (row || $("searchInput")).focus({ preventScroll: true }); window.scrollTo(0, S.listScrollY);
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
    S.boot = boot; renderTop(); renderSummary(); renderClassifier(); updateImportState(); loadScopeCounts();
  }

  async function refreshAll() {
    try { await refreshBootstrap(); await loadList(); if (S.id) await loadDetail({ recovering: true }); }
    catch (error) { showError(error); }
  }

  /* ---------- center column ---------- */
  function dropZone(onFiles, { title, hint, big }) {
    const input = h("input", { type: "file", class: "file-input", multiple: true, accept: ".jpg,.jpeg,.json,image/jpeg,application/json", "aria-label": "사진과 JSON 파일 선택" });
    input.addEventListener("change", () => { onFiles([...input.files]); input.value = ""; });
    const zone = h("div", { class: "drop" + (big ? " big" : "") },
      h("p", { class: "drop-title", text: title }), h("p", { class: "help", text: hint }),
      h("label", { class: "btn black file-btn" }, input, h("span", { text: "파일 선택" })));
    zone.addEventListener("dragover", (event) => { event.preventDefault(); zone.classList.add("over"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("over"));
    zone.addEventListener("drop", (event) => { event.preventDefault(); event.stopPropagation(); zone.classList.remove("over"); onFiles([...event.dataTransfer.files]); });
    return zone;
  }
  function emptyPanel() {
    return h("section", { class: "idle empty-actual" },
      h("p", { class: "k", text: "01 · Import" }), h("h2", { text: "검수할 사진과 기록을 가져오세요" }),
      dropZone((files) => openImport(files), { big: true, title: "사진(.jpg)과 JSON(.json)을 여기에 놓으세요", hint: "같은 이름의 JPEG와 JSON을 한 쌍으로 묶습니다. 예: A.jpg + A.json. 여러 쌍을 한꺼번에 선택할 수 있습니다." }),
      h("button", { type: "button", class: "btn", text: "파일 없이 시연 예제 보기", onclick: () => selectScope("demo") }));
  }
  function completionPanel() {
    const s = S.list.summary, saved = S.completionReport?.scope === S.scope ? S.completionReport.handover : null;
    return h("section", { class: "idle complete" },
      h("p", { class: "k", text: "검수 완료" }), h("h2", { text: "검수를 마쳤습니다" }),
      h("dl", { class: "complete-counts" },
        h("div", {}, h("dt", { text: "전체" }), h("dd", { text: s.total + "건" })),
        h("div", {}, h("dt", { text: "판단 완료" }), h("dd", { text: (s.resolved || 0) + "건" })),
        h("div", {}, h("dt", { text: "보류" }), h("dd", { text: (s.held || 0) + "건" }))),
      S.scope === "demo" ? h("p", { class: "inline-warning", text: "시연 예제의 결과입니다. 실제 자료의 검수가 아닙니다." }) : null,
      h("button", { type: "button", id: "completeReport", class: "btn tea", disabled: S.completionPending, text: S.completionPending ? "보고서 만드는 중" : "이번 검수 보고서 받기", onclick: createCompletionReport }),
      h("p", { id: "completeStatus", class: "help", role: "status", tabindex: "-1", text: S.completionMessage }),
      saved ? h("div", { class: "report-links" }, h("p", { text: readableTime(saved.created_at) + " 저장 · " + val(saved.scope?.count) + "건" }), downloadLinks(saved)) : null);
  }
  function downloadLinks(hd) {
    const link = (format, label, cls) => h("a", { class: "btn " + cls, href: BASE + "/api/handovers/" + enc(hd.id) + "/download?format=" + format, download: "", text: label });
    return h("div", { class: "toolbar" }, link("html", "HTML 보고서", "black"), link("csv", "CSV", ""), link("json", "JSON", ""));
  }
  async function createCompletionReport() {
    if (S.completionPending) return;
    S.completionPending = true; S.completionMessage = "보고서를 만들고 있습니다."; renderDetail();
    try {
      const ids = await scopeRecordIds();
      if (!ids.length) throw new ApiError(0, { code: "validation", message: "보고서에 포함할 기록이 없습니다." });
      const handover = await api("POST", "/api/handovers", { expected_workspace_revision: S.boot.workspace_revision, record_ids: ids });
      if (typeof handover.id !== "string" || !Array.isArray(handover.questions)) throw new ApiError(200, { code: "invalid_response", message: "저장 응답을 확인할 수 없습니다. 보고서 탭에서 저장 여부를 확인하세요." });
      S.handover = handover; S.completionReport = { scope: S.scope, handover }; S.completionMessage = "보고서를 저장했습니다. 아래에서 내려받으세요."; loadReports();
    } catch (error) {
      S.completionMessage = error.status === 409 ? "작업 내용이 변경되었습니다. 최신 상태를 불러왔으니 다시 시도하세요." : error.message;
      if (error.status === 409) { try { await refreshBootstrap(); } catch (refreshError) { S.completionMessage = refreshError.message; } }
    } finally { S.completionPending = false; renderDetail(); $("completeStatus")?.focus(); announce(S.completionMessage); }
  }
  function idlePanel() {
    const s = S.list?.summary;
    if (S.scope === "actual" && s?.total === 0) return emptyPanel();
    if (s?.total > 0 && pendingCount() === 0) return completionPanel();
    return h("section", { class: "idle" },
      h("p", { class: "k", text: scopeName() }), h("h2", { text: S.list ? "검수할 기록을 선택하세요" : "기록을 불러오는 중" }),
      S.scope === "demo" ? h("p", { class: "help", text: "시연 예제는 가상 자료입니다. 실제 AI 결과가 아니며, 판단을 저장해도 내 실제 자료에는 반영되지 않습니다." }) : null,
      pendingCount() > 0 ? h("button", { type: "button", class: "btn tea", text: "다음 판단 기록 열기 · " + pendingCount() + "건 남음", onclick: openNextPending }) : null,
      h("p", { class: "help", text: "J / K 키로 이전·다음 기록으로 이동합니다." }));
  }

  const weekField = (d) => (d.values || []).find((v) => v.field_path === "growth.growth_cycle");
  const currentWeek = (d) => d.comparison?.current_week ?? weekField(d)?.effective ?? d.growth_week;
  const modelWeek = (d) => d.comparison?.model_week ?? d.model?.growth_week;
  const clock = (value) => {
    const date = new Date(value);
    return Number.isNaN(date.valueOf()) ? val(value) : new Intl.DateTimeFormat("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
  };
  function eventSummary(e) {
    const info = e.details || {};
    if (e.type === "classify" || e.type === "recheck") return "";
    const text = info.note || info.reason || info.evidence_note || info.next_action || (info.field ? fieldName(info.field) + " 적용값 " + val(info.value) : "");
    return text.length > 90 ? text.slice(0, 90) + "..." : text;
  }
  const classifierReady = () => S.classifier?.enabled === true && S.classifier?.status === "ready";

  function openLightbox(opener, src, alt, caption) {
    const dialog = $("lightbox"), img = $("lbImg");
    S.lbOpener = opener;
    img.src = src; img.alt = alt; img.className = "fit";
    $("lbZoom").textContent = "확대"; $("lbZoom").setAttribute("aria-pressed", "false"); $("lbCaption").textContent = caption;
    dialog.showModal(); $("lbStage").scrollTo(0, 0); $("lbClose").focus();
  }
  function toggleLightboxZoom() {
    const img = $("lbImg"), zoomed = img.classList.toggle("zoom");
    img.classList.toggle("fit", !zoomed);
    $("lbZoom").textContent = zoomed ? "축소" : "확대"; $("lbZoom").setAttribute("aria-pressed", String(zoomed));
  }
  function photoNode(d) {
    const img = d.source?.image || {}, photo = h("div", { class: "photo" });
    const caption = d.source?.synthetic ? "01: 시연용 그림" : "01: SOURCE IMAGE · " + val(img.actual_width) + "×" + val(img.actual_height);
    if (img.available === false) photo.append(h("p", { class: "photo-empty", text: "이 모드에서는 원본 사진을 표시하지 않습니다." }));
    else {
      const src = BASE + "/api/records/" + enc(d.id) + "/image", alt = recordName(d) + "의 원본 사진. 좌표나 크기 측정에 사용하지 않습니다.";
      const open = () => openLightbox(photo, src, alt, caption);
      const im = h("img", { src, width: "380", height: "380", alt });
      photo.classList.add("loading", "zoomable");
      photo.setAttribute("role", "button"); photo.tabIndex = 0; photo.setAttribute("aria-haspopup", "dialog"); photo.setAttribute("aria-label", "원본 사진 크게 보기");
      photo.addEventListener("click", open);
      photo.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); } });
      im.addEventListener("load", () => { photo.classList.remove("loading"); measurePhoto(d, im); });
      im.addEventListener("error", () => {
        photo.classList.remove("loading", "zoomable"); photo.removeAttribute("role"); photo.removeAttribute("tabindex"); photo.removeAttribute("aria-label"); photo.removeAttribute("aria-haspopup");
        photo.replaceChildren(h("p", { class: "photo-empty", text: "사진을 불러오지 못했습니다. 원문 기록은 계속 확인할 수 있습니다." }));
      });
      photo.append(im, h("span", { class: "zoom-chip", "aria-hidden": "true", text: "크게 보기" }));
    }
    photo.append(h("span", { class: "tagline", text: caption }));
    return photo;
  }
  function runClassify(d) {
    if (S.id !== d.id || S.detail?.revision !== d.revision) return;
    submitRecord(d.id, "classify", "/classify", { expected_revision: d.revision }, "AI 비교를 마쳤습니다. 원본과 비교해 판단하세요.");
  }
  const modelFailed = (d) => (d.model_attempt?.status === "failed" || d.model?.status === "failed") && d.model?.status !== "ok";
  function failureCard(d) {
    if (!modelFailed(d) || S.classifying.has(d.id)) return null;
    const att = d.model_attempt || {}, raw = att.message || att.reason || att.error?.message || att.error || d.model?.message || "원인을 알 수 없습니다.";
    const busy = S.pendingWrites.has(d.id) || S.conflicts.has(d.id), canRun = realImage(d) && classifierReady();
    return h("div", { class: "fail-card", role: "status" },
      h("div", { class: "fail-text" }, h("strong", { text: "AI 비교에 실패했습니다" }), h("p", { text: typeof raw === "string" ? raw : JSON.stringify(raw) }),
        h("p", { class: "help", text: "원본 기록은 그대로입니다. 다시 시도하거나 아래에서 직접 판단할 수 있습니다." })),
      h("button", { type: "button", class: "btn", "data-classify": "", disabled: !canRun || busy, text: "다시 시도",
        title: !realImage(d) ? "시연 예제는 실제 모델을 호출하지 않습니다" : !canRun ? "실험 모델에 연결되어 있어야 실행할 수 있습니다" : null, onclick: () => runClassify(d) }));
  }
  function compareNode(d) {
    const m = d.model || { status: "none" }, aiOk = m.status === "ok", w = weekField(d);
    const busy = S.pendingWrites.has(d.id) || S.conflicts.has(d.id), canRun = realImage(d) && classifierReady();
    const pending = S.classifying.has(d.id), failed = modelFailed(d);
    const ai = h("div", { class: "cell ai", "aria-busy": String(pending) }, h("div", { class: "k", text: realImage(d) ? "03: AI 예측" : "03: 시연용 예측" }));
    if (aiOk) ai.append(h("div", { class: "v", text: val(modelWeek(d)) + "주" }), h("div", { class: "m", text: val(m.crop) + " · " + (realImage(d) ? "실험 모델" : "시연용 모델 결과") }));
    else if (pending) ai.append(h("div", { class: "v idle-v", text: "AI 비교 중…" }), h("div", { class: "skeleton sk-line" }), h("div", { class: "m", text: "사진을 실험 모델로 보내 예측을 받고 있습니다." }));
    else {
      ai.append(...[h("div", { class: "v idle-v", text: failed ? "결과 없음" : "비교 전" }), h("div", { class: "m", text: L.model[m.status] || "분류 전" }),
        realImage(d) && !failed ? h("button", { type: "button", class: "btn", "data-classify": "", disabled: !canRun || busy, text: busy ? "요청 처리 중" : "AI로 사진 비교", onclick: () => runClassify(d) }) : null,
        realImage(d) && !failed ? h("p", { class: "help", text: canRun ? "클릭하면 사진을 실험 모델로 전송합니다. 1회 예측, 자동 재시도 없음." : "실험 모델에 연결되어 있어야 실행할 수 있습니다." }) : null].filter(Boolean));
    }
    return h("div", { class: "cmp", id: "modelComparison", tabindex: "-1" },
      h("div", { class: "cell" }, h("div", { class: "k", text: "02: 원본 기록" }), h("div", { class: "v", text: val(currentWeek(d)) + "주" }), h("div", { class: "m", text: val(d.crop) + (w?.corrected ? " · 보정 적용" : " · JSON 라벨") })), ai);
  }
  function noteNode(d) {
    const c = d.comparison || {}, notes = [];
    if (d.model?.status === "ok" && c.status === "different") notes.push(h("p", { class: "note", text: c.crop_matches === false ? "기록의 작물과 AI가 분류한 작물이 다릅니다. 원본 JSON은 바뀌지 않습니다." : "원본 " + val(c.current_week) + "주와 AI " + val(c.model_week) + "주가 다릅니다. 어느 쪽이 맞는지 판단이 필요하며, 원본 JSON은 바뀌지 않습니다." }));
    else if (d.model?.status === "ok" && c.status === "equal") notes.push(h("p", { class: "note calm", text: "원본과 AI 주차가 같습니다. 정답을 보장하지는 않습니다." }));
    else if (["stale", "not_adopted"].includes(d.model?.status)) notes.push(h("p", { class: "note", text: "AI 결과가 오래되었거나 채택되지 않아 비교하지 않았습니다." }));
    if (d.model_attempt?.status === "failed" && d.model?.status === "ok") notes.push(h("p", { class: "note", role: "status", text: "최근 재시도는 실패했습니다. 위 비교는 이전 성공 결과를 사용합니다." }));
    return notes;
  }
  function photoTime(value) {
    const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value || "");
    return m ? m[1] + "." + m[2] + "." + m[3] + " " + m[4] + ":" + m[5] : val(value);
  }
  function timelineNode(d) {
    const events = (d.history || []).slice(-6);
    return h("section", { class: "act-wrap" }, h("p", { class: "k", text: "활동 기록" }),
      events.length ? h("ol", { class: "act" }, events.map((e) => h("li", { class: "ev" }, h("i", { "aria-hidden": "true" }),
        h("span", { text: (L.history[e.type] || e.type) + (eventSummary(e) ? " · " + eventSummary(e) : "") }), h("time", { text: clock(e.at) }))))
        : h("p", { class: "help", text: "아직 기록된 변경이 없습니다." }));
  }
  function valuesCard(d) {
    const t = d.time || {}, img = d.source?.image || {};
    const rows = (d.values || []).map((v) => h("tr", { class: v.corrected ? "value-corrected" : "" },
      h("th", { scope: "row", text: v.label || fieldName(v.field_path), title: v.field_path }),
      h("td", {}, h("code", { class: "raw-value", text: rawText(v.raw, v.presence) }), h("span", { class: "cell-note", text: "해석값 " + val(v.parsed) })),
      h("td", {}, h("strong", { class: "numeric", text: v.effective == null ? rawText(v.effective_raw) : String(v.effective) }), h("span", { class: "cell-note" }, L.status[v.status] || v.status, v.corrected ? badge("보정 적용", "warn") : null)),
      h("td", {}, h("strong", { text: val(v.raw_unit) }), h("span", { class: "cell-note", text: L.unit[v.unit_status] || val(v.unit_status) }),
        v.unit_source_ref ? h("span", { class: "cell-note mono", text: v.unit_source_ref }) : null,
        v.assumed_unit ? h("span", { class: "cell-note", text: "규칙 가정 단위 " + v.assumed_unit }) : null)));
    return h("div", { class: "values-block" },
      h("p", { class: "help", text: "환경값은 JSON에 적힌 기록입니다. AI가 사진에서 측정한 결과가 아닙니다." }),
      h("div", { class: "tbl-wrap", tabIndex: 0, role: "region", "aria-label": "원문과 적용값" }, h("table", { class: "values-table" },
        h("thead", {}, h("tr", {}, ...["항목", "원문·해석", "적용값", "단위·출처"].map((text) => h("th", { scope: "col", text })))), h("tbody", {}, rows))),
      h("p", { class: "help", text: "사진 실제 " + val(img.actual_width) + "×" + val(img.actual_height) + " px · 선언 " + val(img.declared_width) + "×" + val(img.declared_height) + " px" }),
      h("dl", { class: "kv" },
        h("dt", { text: "사진 시각" }), h("dd", { text: val(t.photo_parsed) + " (원문 " + val(t.photo_raw) + ")" }),
        h("dt", { text: "환경 시각" }), h("dd", { text: val(t.env_parsed) + " (원문 " + val(t.env_raw) + ")" }),
        h("dt", { text: "시간대" }), h("dd", { text: t.timezone_status === "unknown" ? "확인되지 않음" : val(t.timezone_status) }),
        h("dt", { text: "시각 차이" }), h("dd", { text: t.delta_seconds == null ? "계산 불가" : t.delta_seconds + "초" })),
      h("p", { class: "help", text: val(t.delta_label) + " · 환경 시각이 사진보다 늦어도 자동으로 오류를 뜻하지 않습니다." }));
  }
  function modelEvidence(d) {
    const m = d.model || {}, s = d.source || {}, aiOk = m.status === "ok", real = realImage(d) && m.origin !== "injected";
    const raw = m.model_identity, ident = raw && typeof raw === "object" ? raw : raw ? { name: String(raw) } : {};
    const conn = real && aiOk ? S.classifier?.model || {} : {};
    const name = ident.name || ident.model_name || conn.name, version = ident.version || ident.model_version || conn.version;
    const rows = [
      ["상태", L.model[m.status] || val(m.status)], ["실행 ID", m.run_id], ["작물", m.crop], ["주차", m.growth_week != null ? m.growth_week + "주" : null],
      ["모델 이름·버전", name ? name + (version ? " v" + version : "") : null], ["서빙 ID", ident.serving_id || conn.serving_id],
      ["이미지 SHA-256", m.image_sha256 || (aiOk ? s.image_sha256 : null)], ["JSON SHA-256", m.json_sha256 || (aiOk ? s.json_sha256 : null)],
      ["실행 유형", !aiOk && m.status !== "failed" ? null : real ? "실제 추론" : "시연 결과 (실제 모델 호출 아님)"],
      ["소요 시간", m.elapsed_ms != null ? m.elapsed_ms + " ms" : null], ["요청 ID", m.request_id],
      ["기준 버전", m.based_on_revision != null ? "기록 " + m.based_on_revision + (m.based_on_data_revision != null ? " · 데이터 " + m.based_on_data_revision : "") : null]]
      .filter(([, value]) => value !== null && value !== undefined && value !== "");
    return h("div", { class: "values-block" },
      h("p", { class: "help", text: "분류 점수는 보정된 정답 확률이 아닙니다. 자료 출처와 실행 유형은 별개입니다." }),
      h("dl", { class: "kv" }, rows.flatMap(([key, value]) => [h("dt", { text: key }), h("dd", { class: /ID|SHA/.test(key) ? "mono" : "", text: String(value) })])),
      m.message ? h("p", { class: "help", text: m.message }) : null,
      S.classifier ? h("details", {}, h("summary", { text: "기술 정보" }), h("p", { class: "help", text: "모델 연결 점검 응답입니다." }), h("pre", { class: "json", text: JSON.stringify(S.classifier, null, 2) })) : null);
  }

  function recordPosition() {
    const items = S.list?.items || [], index = items.findIndex((item) => item.id === S.id);
    if (index < 0 || !S.list) return null;
    const total = S.list.total || items.length, n = (S.page - 1) * (S.list.page_size || S.pageSize) + index + 1;
    return { n, total, first: n <= 1, last: n >= total };
  }
  function renderDetail() {
    const d = S.detail, box = $("detail"); box.replaceChildren(); setMode(); updateTitle();
    box.setAttribute("aria-busy", "false");
    if (!d) { box.append(backBtn(), idlePanel()); $("props").replaceChildren(); renderMbar(); return; }
    const pos = recordPosition();
    box.append(backBtn(),
      h("div", { class: "bar" }, h("span", { class: "crumb" }, scopeName() + " / ", h("b", { text: recordName(d) })),
        h("span", { class: "bar-end" },
          h("span", { class: "stepper", role: "group", "aria-label": "기록 이동" },
            h("button", { type: "button", class: "btn mini", id: "prevRecord", disabled: !pos || pos.first, title: "이전 기록 (K)", text: "‹ 이전", onclick: () => stepRecord(-1) }),
            h("span", { class: "k pos", text: pos ? pos.n + " / " + pos.total : "" }),
            h("button", { type: "button", class: "btn mini", id: "nextRecord", disabled: !pos || pos.last, title: "다음 기록 (J)", text: "다음 ›", onclick: () => stepRecord(1) })),
          h("button", { type: "button", class: "btn mini", text: "이 기록 보고서", onclick: () => { S.handoverScope = "current"; setView("handover"); } }))),
      h("div", { class: "doc" },
        h("h2", { text: recordName(d) }),
        h("p", { class: "sub", text: (d.source?.synthetic ? "합성 시연 자료" : photoTime(d.time?.photo_parsed) + " 촬영 · " + (L.origin[d.origin] || val(d.origin))) + " · 원본 JSON 보존" }),
        photoNode(d), qualityNode(d), compareNode(d), failureCard(d), ...noteNode(d), timelineNode(d),
        h("div", { class: "folds" },
          h("p", { class: "k", text: "04 · Evidence" }), h("p", { class: "help", text: "판단의 근거가 되는 원본 값, 규칙 검사, 비교 기록입니다. 필요할 때 펼쳐 보세요." }),
          foldSection(valuesCard(d), "원본 환경 기록과 단위"), foldSection(checksCard(d), "규칙 검사와 확인할 이유"),
          foldSection(groupCard(d), "같은 원본 값의 사진 묶음 · 독립 측정 아님"), foldSection(peersCard(d), "다른 기록과 비교"),
          sourceCard(d), foldSection(historyCard(d), "변경 이력 전체"), foldSection(modelEvidence(d), "AI 실행 근거"))));
    renderProps();
  }

  /* ---------- photo check: advisory hints, never a verdict (docs/quality-checks-eval.md) ---------- */
  const PHOTO_CODES = new Set(["BLURRY", "TOO_DARK", "TOO_BRIGHT", "LOW_CONTRAST"]);
  const PHOTO_CHIP = "사진 품질 확인 필요";
  const QUALITY_TEXT = {
    BLURRY: { title: "사진이 흐림", label: "선명도", op: "<" },
    TOO_DARK: { title: "사진이 어두움", label: "밝기", op: "<" },
    TOO_BRIGHT: { title: "사진이 너무 밝음", label: "밝기", op: ">" },
    LOW_CONTRAST: { title: "사진의 대비가 낮음", label: "대비", op: "<" },
    CROP_NOT_TEA: { title: "녹차가 아닌 것 같음", label: "AI 분류", op: "≠" },
    CROP_LOW_CONFIDENCE: { title: "녹차 분류 신뢰도가 낮음", label: "신뢰도", op: "<" },
  };
  function qualityTitle(w) { return w.code === "CROP_NOT_TEA" ? "녹차가 아닌 것 같음 · AI가 " + w.value + "(으)로 분류" : QUALITY_TEXT[w.code].title; }
  function qualityEvidence(w) {
    const t = QUALITY_TEXT[w.code];
    if (w.code === "CROP_NOT_TEA") return t.label + " " + w.value + " " + t.op + " " + w.threshold;
    const shown = w.code === "CROP_LOW_CONFIDENCE" ? w.value.toFixed(2) : String(t.op === "<" ? Math.floor(w.value) : Math.ceil(w.value));
    return t.label + " " + shown + " " + t.op + " " + w.threshold;
  }
  function photoWarnings(d) {
    const q = S.quality.get(d.id);
    if (!q?.metrics) return [];
    const m = d.model;
    // A missing score cannot trigger CROP_LOW_CONFIDENCE; CROP_NOT_TEA only needs the crop name.
    const modelResult = m?.status === "ok" && m.crop ? { crop: { name_ko: m.crop, confidence: Number(m.crop_confidence ?? m.confidence ?? 1) } } : undefined;
    return window.FarmQuality.assess(q.metrics, modelResult);
  }
  function qualityNode(d) {
    const warnings = photoWarnings(d), card = h("section", { id: "qualityCard", class: "q-card", hidden: !warnings.length, "aria-label": "사진 확인 권장" });
    if (warnings.length) card.append(h("p", { class: "k", text: "사진 확인 권장" }),
      h("ul", { class: "q-list" }, warnings.map((w) => h("li", { class: "q-row" }, h("span", { class: "q-title", text: qualityTitle(w) }), h("code", { class: "q-evidence", text: qualityEvidence(w) })))),
      h("p", { class: "help", text: "확인 권장 표시이며 라벨 오류 판정이 아닙니다" }));
    return card;
  }
  function measurePhoto(d, img) {
    if (S.quality.has(d.id)) return;
    let entry;
    try { entry = { metrics: window.FarmQuality.measure(img) }; }
    catch (error) { entry = { failed: true }; } // advisory only: a failed measurement shows nothing
    S.quality.set(d.id, entry);
    if (S.detail?.id !== d.id) return;
    const warnings = photoWarnings(S.detail);
    if (!warnings.length) return;
    $("qualityCard")?.replaceWith(qualityNode(S.detail));
    if (decisionDraft(d.id).mode === "hold") renderProps();
    announce("사진 확인 권장 " + warnings.length + "건");
  }
  const suggestsPhotoChip = (d, dr, chip) => chip === PHOTO_CHIP && dr.chip !== chip && photoWarnings(d).some((w) => PHOTO_CODES.has(w.code));
  async function measurePair(pair, token) {
    let warnings = [];
    try {
      const bitmap = await createImageBitmap(pair.jpg);
      try { warnings = window.FarmQuality.assess(window.FarmQuality.measure(bitmap)); } finally { bitmap.close(); }
    } catch (error) { warnings = []; } // advisory only: a failed measurement shows nothing
    if (token !== pair.token) return;
    pair.quality = warnings; updateImportState();
  }

  /* ---------- right panel: properties, scores, decision ---------- */
  const prop = (key, ...value) => h("div", { class: "prop" }, h("dt", { text: key }), h("dd", {}, value));
  function scoreRow(label, raw) {
    const n = raw === null || raw === undefined || raw === "" ? null : Number.parseFloat(raw);
    const fill = h("span");
    fill.style.width = (n === null || Number.isNaN(n) ? 0 : Math.min(100, Math.max(0, n * 100))) + "%";
    return prop(label, h("span", { class: "score" }, h("span", { class: "meter", role: "meter", "aria-label": label + " 분류 점수", "aria-valuemin": "0", "aria-valuemax": "1", "aria-valuenow": n ?? 0 }, fill), n === null ? "없음" : String(raw)));
  }
  function feedbackNode(id) {
    const feedback = S.feedback.get(id);
    if (!feedback) return null;
    return h("div", { id: "recordFeedback", class: "feedback " + feedback.kind, tabIndex: -1, role: "group", "aria-label": "저장 결과" },
      h("p", { text: feedback.text }), feedback.recover ? h("button", { type: "button", class: "btn", text: "최신 기록 불러오기 · 초안 유지", onclick: () => loadDetail({ recovering: true }) }) : null);
  }
  function renderProps() {
    const box = $("props"), d = S.detail; box.replaceChildren(); renderMbar();
    if (!d) return;
    const st = detailState(d), c = d.comparison || {}, m = d.model || {}, aiOk = m.status === "ok";
    const advanced = h("details", { class: "advanced" }, h("summary", { text: "상세 옵션 · 직접 기록" }), h("p", { class: "help", text: "대부분은 위 질문으로 충분합니다. 검토 메모, 값 보정, 재검사, 해소 근거를 직접 기록할 때만 사용하세요." }), actionsCard(d));
    advanced.open = !!S.advOpen.get(d.id);
    advanced.addEventListener("toggle", () => S.advOpen.set(d.id, advanced.open));
    box.append(...[feedbackNode(d.id),
      h("dl", { class: "card prop-list" },
        prop("상태", h("span", { class: "pill " + st }, h("span", { class: "st " + st, "aria-hidden": "true" }), STATE_TEXT[st])),
        prop("작물", val(d.crop), aiOk && c.crop_matches === true ? h("span", { class: "dim", text: " · 일치" }) : null, aiOk && c.crop_matches === false ? h("span", { class: "dim", text: " · 불일치" }) : null),
        prop("원본 주차", val(currentWeek(d)) + "주"),
        prop("AI 주차", aiOk ? h("strong", { class: "ai-week", text: val(modelWeek(d)) + "주" }) : "비교 전")),
      h("section", { class: "scores" }, h("p", { class: "k", text: "분류 점수 · 정답 확률 아님" }),
        h("dl", { class: "card prop-list" }, scoreRow("작물", aiOk ? (m.crop_confidence ?? m.confidence) : null), scoreRow("주차", aiOk ? m.growth_confidence : null))),
      decisionPanel(d), advanced,
      h("p", { class: "key-hint", text: "1·2·3 판단 · Enter 기록 · J/K 이동" })].filter(Boolean));
  }

  /* ---------- decision panel ---------- */
  const HOLD_CHIPS = [["촬영일 확인 필요", "촬영 기록 원본을 확인합니다."], ["재배 시작일 확인 필요", "재배 시작일 기록을 확인합니다."], ["사진 품질 확인 필요", "사진 원본 상태를 확인합니다."], ["다른 담당자 확인 필요", "다른 담당자에게 확인을 요청합니다."]];
  const HOLD_DEFAULT_NEXT = "원본 기록을 확인합니다.";
  const decisionDraft = (id) => draft(id, "decision", { mode: null, note: "", chip: "", holdReason: "", next: "", nextTouched: false });
  const holdReason = (dr) => [dr.chip, dr.holdReason.trim()].filter(Boolean).join(" · ");
  const holdNext = (dr) => (dr.nextTouched ? dr.next.trim() : (HOLD_CHIPS.find(([chip]) => chip === dr.chip)?.[1] || HOLD_DEFAULT_NEXT));
  const matchedChecks = (d) => (d.checks || []).filter((check) => check.outcome === "matched");
  const isModelCandidate = (entry) => entry.kind === "model" || entry.ref.startsWith("model:");
  function refLabel(entry) {
    if (isModelCandidate(entry)) return "모델 실행";
    if (entry.ref.startsWith("rule:")) { const id = entry.ref.slice(5).split("@")[0]; return "규칙 " + (L.rules[id] || id); }
    return fieldName(entry.ref.split(":").at(-1)) + " 원문";
  }
  function resolveEvidence(d) {
    const candidates = d.evidence_candidates || [], matched = matchedChecks(d), refs = new Set(matched.flatMap((check) => check.evidence_refs || []));
    if (d.model?.status === "ok") candidates.filter(isModelCandidate).forEach((entry) => refs.add(entry.ref));
    if (!matched.length) candidates.filter((entry) => entry.ref.includes("growth.growth_cycle")).forEach((entry) => refs.add(entry.ref));
    return candidates.filter((entry) => refs.has(entry.ref));
  }
  function decisionPlan(d, dr) {
    const cur = currentWeek(d), mw = modelWeek(d), memo = dr.note.trim() ? " · 메모: " + dr.note.trim() : "";
    if (dr.mode === "original") {
      const evidence = resolveEvidence(d), equal = d.comparison?.status === "equal";
      return { ok: evidence.length > 0, text: (equal ? "원본 " + cur + "주와 AI 예측 일치 확인" : "원본 " + cur + "주 유지") + " · 해소로 기록 · 근거: " + (evidence.map(refLabel).join(", ") || "없음") + memo,
        blocker: evidence.length ? "" : "연결할 근거가 없습니다. 상세 옵션에서 근거를 직접 선택하세요." };
    }
    if (dr.mode === "ai") return { ok: true, text: "원본 JSON은 그대로 두고 주차 " + cur + "주 → " + mw + "주 보정 · 해소로 기록 · 근거: 모델 실행, 주차 원문" + memo, blocker: "" };
    if (dr.mode === "hold") {
      const reason = holdReason(dr), next = holdNext(dr);
      return { ok: !!reason && !!next, text: "보류로 기록 · 사유: " + (reason || "미입력") + " · 다음 확인: " + (next || "미입력") + memo,
        blocker: !reason ? "보류 사유를 고르거나 입력하세요." : !next ? "다음에 확인할 일을 입력하세요." : "" };
    }
    return { ok: false, text: "", blocker: "" };
  }
  function updateConfirm(d, dr) {
    const plan = decisionPlan(d, dr), text = $("confirmText");
    if (!text) return;
    text.textContent = plan.text; $("confirmHint").textContent = plan.blocker;
    $("commitDecision").disabled = !plan.ok || S.pendingWrites.has(d.id) || S.conflicts.has(d.id);
  }
  function setDecisionMode(id, mode) {
    const dr = decisionDraft(id); dr.mode = mode; dr.dirty = true; renderProps();
    (mode === "hold" ? $("hold-chip-0") : $("commitDecision"))?.focus({ preventScroll: true });
  }
  function cancelDecision(id) {
    const dr = decisionDraft(id), mode = dr.mode; dr.mode = null; renderProps(); $("decide-" + mode)?.focus({ preventScroll: true });
  }
  function holdFields(d, dr) {
    const id = d.id;
    const free = h("input", { id: "holdFree", type: "text", maxLength: 500, value: dr.holdReason, placeholder: "직접 사유 입력 (선택)" });
    free.addEventListener("input", () => { markDraft(dr, "holdReason", free.value); updateConfirm(d, dr); });
    const next = h("input", { id: "holdNext", type: "text", maxLength: 500, value: holdNext(dr) });
    next.addEventListener("input", () => { dr.next = next.value; dr.nextTouched = true; dr.dirty = true; updateConfirm(d, dr); });
    return h("div", { class: "hold-fields" },
      h("div", { class: "chips", role: "group", "aria-label": "보류 사유" }, HOLD_CHIPS.map(([chip], index) => { const hint = suggestsPhotoChip(d, dr, chip); return h("button", { type: "button", id: "hold-chip-" + index, class: "chip" + (hint ? " suggested" : ""), "aria-pressed": String(dr.chip === chip), title: hint ? "사진 확인 권장 표시가 있어 추천합니다" : null,
        onclick: () => { dr.chip = dr.chip === chip ? "" : chip; dr.dirty = true; renderProps(); $("hold-chip-" + index)?.focus({ preventScroll: true }); } }, chip, hint ? h("span", { class: "chip-hint", text: " · 추천" }) : null); })),
      h("label", { class: "field" }, h("span", { text: "사유" }), free),
      h("label", { class: "field" }, h("span", { text: "다음 담당자가 확인할 일" }), next));
  }
  const GENERIC_NOTES = [/^원본 .*주 기록이 맞다고 판단$/, /^AI .*주가 맞다고 판단해 주차를 보정$/, /^원본 .*주와 AI 예측이 일치함을 확인$/];
  const isJudged = (d) => ["held", "resolved"].includes(d.review?.resolution_state);
  function judgedSummary(d) {
    const rv = d.review || {}, clip = (text) => (text.length > 120 ? text.slice(0, 120) + "…" : text);
    if (rv.resolution_state === "held") {
      const hold = rv.hold || {};
      return ["보류", hold.reason, hold.next_action ? "다음 확인: " + hold.next_action : ""].filter(Boolean).map(clip).join(" · ");
    }
    const week = weekField(d), memo = (rv.resolution?.evidence_note || "").trim();
    const parts = ["판단 완료", week?.corrected ? "AI 기준 " + val(week.effective) + "주로 보정" : "원본 " + val(currentWeek(d)) + "주 유지"];
    if (memo && !GENERIC_NOTES.some((re) => re.test(memo))) parts.push(clip(memo));
    return parts.join(" · ");
  }
  function decisionChoices(d) {
    const cmp = d.comparison?.status;
    if (cmp === "equal") return [{ mode: "original", label: "일치 확인", enabled: true }];
    const aiEnabled = d.model?.status === "ok" && cmp === "different", demoNoAi = !realImage(d) && d.model?.status !== "ok";
    return [{ mode: "original", label: "원본이 맞음", enabled: true },
      { mode: "ai", label: "AI가 맞음", enabled: aiEnabled, title: aiEnabled ? null : demoNoAi ? "시연 예제는 실제 모델을 호출하지 않습니다" : "AI 결과가 있고 주차가 다를 때만 선택할 수 있습니다" },
      { mode: "hold", label: "모르겠음 (보류)", enabled: true }];
  }
  function renderMbar() {
    const box = $("mbar"), d = S.detail;
    box.replaceChildren();
    if (!d || S.view !== "records") { box.hidden = true; return; }
    const dr = decisionDraft(d.id);
    if ((isJudged(d) && !dr.changing) || dr.mode) { box.hidden = true; return; }
    const locked = S.pendingWrites.has(d.id) || S.conflicts.has(d.id);
    for (const c of decisionChoices(d)) box.append(h("button", { type: "button", class: "btn", disabled: !c.enabled || locked, title: c.title || null, text: c.label.replace(" (보류)", ""),
      onclick: () => { setDecisionMode(d.id, c.mode); $("decisionPanel")?.scrollIntoView({ block: "start", behavior: "smooth" }); } }));
    box.hidden = false;
  }
  function decisionPanel(d) {
    const id = d.id, dr = decisionDraft(id), busy = S.pendingWrites.has(id) || S.conflicts.has(id);
    const judged = isJudged(d), changing = judged && dr.changing, equal = d.comparison?.status === "equal";
    const progress = h("p", { id: "decisionProgress", class: "write-progress", hidden: !S.pendingWrites.has(id), text: "기록하는 중입니다. 중복 저장을 막기 위해 이 기록의 입력을 잠시 잠급니다." });
    if (judged && !changing) {
      return h("section", { class: "decision", id: "decisionPanel", "aria-labelledby": "decisionTitle" },
        h("h3", { id: "decisionTitle", text: "현재 판단" }),
        h("p", { class: "judged " + (d.review.resolution_state === "held" ? "hold" : "done"), text: judgedSummary(d) }),
        h("button", { type: "button", id: "changeDecision", class: "btn", disabled: busy, text: "판단 바꾸기",
          onclick: () => { dr.changing = true; renderProps(); $("decide-original")?.focus({ preventScroll: true }); } }),
        progress);
    }
    const others = matchedChecks(d).filter((check) => check.rule_id !== "MODEL_RECORD_DIFFERENCE"), choices = decisionChoices(d);
    const demoNoAi = !realImage(d) && d.model?.status !== "ok", aiEnabled = choices.some((c) => c.mode === "ai" && c.enabled);
    const choice = (c) => h("button", { type: "button", id: "decide-" + c.mode, class: "btn choice", "aria-pressed": String(dr.mode === c.mode), disabled: !c.enabled, title: c.title || null, text: c.label, onclick: () => setDecisionMode(id, c.mode) });
    const note = h("textarea", { id: "decisionNote", maxLength: 2000, rows: 3, placeholder: "판단 이유를 남기면 기록에 함께 저장됩니다.", value: dr.note });
    note.addEventListener("input", () => { markDraft(dr, "note", note.value); updateConfirm(d, dr); });
    const plan = decisionPlan(d, dr);
    return h("section", { class: "decision", id: "decisionPanel", "aria-labelledby": "decisionTitle" },
      h("h3", { id: "decisionTitle", text: changing ? "판단 바꾸기" : equal ? "일치 확인" : "어느 쪽이 맞나요?" }),
      changing ? h("p", { class: "help", text: "현재: " + judgedSummary(d) }) : null,
      others.length ? h("div", { class: "also" }, h("p", { class: "k", text: "함께 확인할 항목" }),
        h("ul", {}, others.map((check) => h("li", {}, h("strong", { text: L.rules[check.rule_id] || check.rule_id }), check.next_question ? h("span", { class: "dim", text: " · " + check.next_question }) : null))),
        h("p", { class: "help", text: "판단을 기록하면 이 항목도 함께 확인한 것으로 남습니다." })) : null,
      h("fieldset", { class: "workflow-fields decision-fields", disabled: busy },
        h("legend", { class: "sr-only", text: "판단 선택" }),
        h("label", { class: "field" }, h("span", { text: "메모 (선택)" }), note),
        h("div", { class: "choices" }, choices.map(choice)),
        !equal && !aiEnabled ? h("p", { class: "help", text: demoNoAi ? "시연 예제는 실제 모델을 호출하지 않습니다" : "AI가 맞음은 AI 결과가 있고 주차가 다를 때만 선택할 수 있습니다." }) : null,
        dr.mode === "hold" ? holdFields(d, dr) : null,
        dr.mode ? h("div", { class: "confirm", id: "confirmBlock", role: "group", "aria-label": "기록 내용 확인" },
          h("p", { class: "k", text: "이렇게 기록합니다" }), h("p", { id: "confirmText", class: "confirm-text", text: plan.text }), h("p", { id: "confirmHint", class: "field-help", text: plan.blocker }),
          h("div", { class: "confirm-actions" },
            h("button", { type: "button", id: "commitDecision", class: "btn tea", disabled: !plan.ok, text: "기록하기", onclick: () => commitDecision(id) }),
            h("button", { type: "button", class: "btn", text: "취소", onclick: () => cancelDecision(id) }))) : null,
        changing && !dr.mode ? h("button", { type: "button", class: "btn link", text: "그대로 두기", onclick: () => { dr.changing = false; renderProps(); } }) : null),
      progress);
  }

  function foldSection(node, title) {
    const disclosure = h("details", { class: "evidence-section" }, h("summary", { text: title }));
    node.classList.remove("card"); disclosure.append(node); return disclosure;
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
          h("p", { text: "대상: " + ((k.fields || []).map(fieldName).join(" · ") || "미확인") }),
          h("p", { text: "단정하지 않는 것: " + val(k.not_concluded) }),
          h("p", { class: "mono", text: k.rule_id + " · v" + val(k.version) }))));
    }
    return c;
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
        ...(s.image_sha256 ? [h("dt", { text: "JPEG SHA-256" }), h("dd", { class: "mono", text: s.image_sha256 })] : []),
        ...(s.json_sha256 ? [h("dt", { text: "JSON SHA-256" }), h("dd", { class: "mono", text: s.json_sha256 })] : []),
        h("dt", { text: "자료 출처 키" }), h("dd", { class: "mono", text: val(s.source_group_id) })),
      ...(s.warnings || []).map(text => h("p", { class: "help", text: "가져오기 주의: " + text })),
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
          h("thead", {}, h("tr", {}, ...["기록 ID", "작물", "주차", "시각", ...keys.map(fieldName), "단위 표기", "자료 종류"].map(text => h("th", { scope: "col", text })))),
          h("tbody", {}, peers.map(peer => h("tr", {},
            h("td", { class: "peer-id" }, identifier(peer.id), h("button", { type: "button", class: "text-button", text: "기록 보기", onclick: () => selectRecord(peer.id) })),
            h("td", { text: val(peer.crop) }), h("td", { text: val(peer.growth_week) }), h("td", { title: val(peer.timestamp), text: shortTime(peer.timestamp) }),
            ...keys.map(key => h("td", { class: "num", text: val(peer.values?.[key]) })),
            h("td", { text: L.unit[peer.unit_status] || val(peer.unit_status) }), h("td", {}, originBadge(peer.origin))))))));
    }
    c.append(h("p", { class: "help", text: d.peers_note || "비교는 정상 범위나 생육 추세를 뜻하지 않습니다." }));
    return c;
  }

  function realImage(d) {
    const source = d.source || {};
    if (source.synthetic === true) return false;
    return ["uploaded", "serverzip"].includes(source.origin) || source.synthetic === false;
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

  /* ---------- record-owned drafts ---------- */

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
    if (focusTab) { $("action-tab-" + action).focus({ preventScroll: true }); $("actionsCard").scrollIntoView({ block: "nearest" }); }
  }

  /* ---------- writes ---------- */
  async function postRec(id, suffix, body) {
    const detail = await api("POST", "/api/records/" + enc(id) + suffix, body);
    if (detail.id !== id || !Number.isInteger(detail.revision)) throw new ApiError(200, { code: "invalid_response", message: "저장 응답을 확인할 수 없습니다. 최신 기록을 먼저 불러오세요." });
    return detail;
  }
  function renderBusy(id) {
    if (S.id !== id) return;
    const busy = S.pendingWrites.has(id), locked = busy || S.conflicts.has(id);
    document.querySelectorAll("#props fieldset.workflow-fields").forEach((fieldset) => { fieldset.disabled = locked; });
    document.querySelectorAll("#props [role='tab']").forEach((tab) => { tab.disabled = busy; });
    document.querySelectorAll("[data-classify]").forEach((button) => { button.disabled = locked || !realImage(S.detail) || !classifierReady(); });
    for (const progress of [$("writeProgress"), $("decisionProgress")]) if (progress) progress.hidden = !busy;
  }
  /* The one write funnel: serializes per record, keeps drafts on failure, reports the chained detail. */
  async function writeRecord(id, work, opts = {}) {
    if (S.pendingWrites.has(id) || S.conflicts.has(id)) return false;
    S.pendingWrites.add(id); S.feedback.delete(id); renderBusy(id); announce("저장 중입니다. 잠시 기다려 주세요.");
    if (S.id === id) ++S.detailRequest;
    let latest = null, advancing = false, ok = false;
    try {
      const result = await work((detail) => { latest = detail; });
      if (opts.clear) S.drafts.get(id)?.delete(opts.clear);
      S.conflicts.delete(id);
      S.feedback.set(id, { kind: result.kind || "success", text: result.message, recover: !!result.recover });
      if (opts.nextAction) S.activeActions.set(id, opts.nextAction);
      applyDetail(result.detail); announce(result.message);
      ok = result.kind !== "error"; advancing = ok && opts.advance !== undefined;
      try { await refreshBootstrap(); await loadList({ quiet: true }); }
      catch (error) { showError(new ApiError(error.status || 0, { code: "summary_refresh", message: "저장은 완료됐지만 목록 요약을 갱신하지 못했습니다. 목록을 다시 불러오세요." }), { focus: false }); }
    } catch (error) {
      const uncertain = error.status === 409 || error.status === 0 || error.status >= 500 || error.code === "invalid_response";
      if (uncertain) S.conflicts.add(id);
      S.feedback.set(id, { kind: "error", text: error.status === 409 ? "기록이 업데이트되었습니다. 초안은 보관했으니 최신 내용을 확인한 뒤 다시 저장하세요." : error.message, recover: uncertain });
      if (latest) applyDetail(latest);
      announce("저장하지 못했거나 결과를 확인할 수 없습니다. 입력 내용은 보관했습니다.");
    } finally {
      S.pendingWrites.delete(id);
      if (!advancing && S.id === id && S.detail) {
        renderDetail();
        const target = S.feedback.get(id)?.kind !== "error" && opts.focusAi ? $("modelComparison") : $("recordFeedback");
        if (S.view === "records" && target) { target.focus({ preventScroll: true }); target.scrollIntoView({ block: "nearest" }); }
      }
    }
    if (advancing) await advance(id, opts.advance);
    return ok;
  }
  async function submitRecord(id, action, suffix, body, message, nextAction) {
    if (action === "classify") {
      if (S.pendingWrites.has(id) || S.conflicts.has(id)) return false;
      S.classifying.add(id); if (S.id === id) renderDetail();
    }
    return writeRecord(id, async (track) => { try {
      let detail = await postRec(id, suffix, body); track(detail);
      const modelFailed = action === "classify" && (detail.model_attempt?.status === "failed" || detail.model?.status === "failed");
      let recheckFailed = false;
      if ((action === "classify" && !modelFailed) || action === "correction") {
        try { detail = await postRec(id, "/recheck", { expected_revision: detail.revision }); track(detail); }
        catch (error) { if (!(error instanceof ApiError)) throw error; recheckFailed = true; }
      }
      const text = recheckFailed ? "저장은 완료됐지만 검사 갱신에 실패했습니다. AI를 다시 실행하지 말고, 최신 기록을 불러온 뒤 검사만 갱신하세요." : modelFailed ? "최근 AI 요청이 실패했습니다. 이전 성공 결과가 있다면 그대로 유지합니다." : message;
      return { detail, message: text, kind: modelFailed || recheckFailed ? "error" : "success", recover: recheckFailed };
    } finally { S.classifying.delete(id); } }, { clear: action, nextAction, focusAi: ["classify", "correction"].includes(action) });
  }

  /* ---------- decision commit and auto-advance ---------- */
  function nextPendingId(id) {
    const items = S.list?.items || [], index = items.findIndex((item) => item.id === id);
    if (index < 0) return null;
    return [...items.slice(index + 1), ...items.slice(0, index)].find(isPending)?.id ?? null;
  }
  async function advance(fromId, nextId) {
    if (S.id !== fromId) return;
    let target = nextId;
    if (!target && pendingCount() > 0) { try { target = await findPending(fromId); } catch (error) { showError(error); } }
    if (target) { flash("판단을 기록했습니다. " + progressText() + ". 다음 기록으로 이동합니다."); await selectRecord(target); return; }
    S.id = null; S.detail = null; ++S.detailRequest; document.body.dataset.pane = "detail";
    renderList(); renderDetail(); flash("판단을 기록했습니다. " + progressText() + ".");
    $("detail").focus({ preventScroll: true });
  }
  async function commitDecision(id) {
    const d = S.detail, dr = decisionDraft(id);
    if (!d || d.id !== id || !dr.mode || !decisionPlan(d, dr).ok) return;
    const judged = isJudged(d), note = dr.note.trim(), cur = currentWeek(d), mw = modelWeek(d), equal = d.comparison?.status === "equal", mode = dr.mode;
    const resolve = (detail, evidenceNote) => {
      const refs = resolveEvidence(detail).map((entry) => entry.ref);
      if (!refs.length) throw new ApiError(422, { code: "evidence_missing", message: "근거로 연결할 항목이 없어 해소를 기록하지 못했습니다. 앞 단계는 저장되었습니다. 상세 옵션에서 근거를 직접 선택하세요." });
      return postRec(id, "/review", { expected_revision: detail.revision, action: "resolve", evidence_note: evidenceNote, evidence_refs: refs });
    };
    await writeRecord(id, async (track) => {
      let detail = d;
      if (mode === "hold") {
        detail = await postRec(id, "/review", { expected_revision: d.revision, action: "hold", note: [holdReason(dr), note].filter(Boolean).join(" · "), next_action: holdNext(dr) });
        return { detail, message: "보류로 기록했습니다. 사유와 다음 확인 항목이 보고서에 포함됩니다." };
      }
      if (mode === "ai") {
        if (!(d.model?.status === "ok" && d.comparison?.status === "different") || mw == null) throw new ApiError(422, { code: "ai_unavailable", message: "AI 결과가 없거나 주차가 같아 AI 기준으로 보정할 수 없습니다." });
        detail = await postRec(id, "/corrections", { expected_revision: d.revision, field: "growth.growth_cycle", value: String(mw), reason: note || "사진 기준 AI 주차가 맞다고 판단" }); track(detail);
        detail = await postRec(id, "/recheck", { expected_revision: detail.revision }); track(detail);
        detail = await resolve(detail, note || "AI " + mw + "주가 맞다고 판단해 주차를 보정");
        return { detail, message: "주차를 " + cur + "주에서 " + mw + "주로 보정하고 해소로 기록했습니다. 원본 JSON은 바뀌지 않았습니다." };
      }
      if (d.review?.recheck_needed) { announce("현재 값 기준으로 검사를 갱신하는 중입니다."); detail = await postRec(id, "/recheck", { expected_revision: d.revision }); track(detail); }
      detail = await resolve(detail, note || (equal ? "원본 " + cur + "주와 AI 예측이 일치함을 확인" : "원본 " + cur + "주 기록이 맞다고 판단"));
      return { detail, message: "원본 " + cur + "주 유지로 해소 기록했습니다." };
    }, judged ? { clear: "decision" } : { clear: "decision", advance: nextPendingId(id) });
  }
  async function send(button, fn) {
    button.disabled = true; hideError();
    try { await fn(); } catch (error) { showError(error); } finally { button.disabled = false; }
  }

  function actionsCard(d) {
    const id = d.id, rv = d.review || {}, active = S.activeActions.get(id) || "reviewed";
    const actions = [["reviewed", "검토"], ["hold", "보류"], ["correction", "보정"], ["recheck", "재검사"], ["resolve", "해소"]];
    const card = h("section", { class: "workflow", id: "actionsCard" });
    const actionButtons = actions.map(([key, label], index) => h("button", {
      type: "button", id: "action-tab-" + key, role: "tab", "data-action": key, "aria-controls": "actionPanel",
      "aria-selected": String(active === key), tabIndex: active === key ? 0 : -1, disabled: S.pendingWrites.has(id), text: label,
      onclick: () => changeAction(id, key), onkeydown: event => {
        const offsets = { ArrowRight: 1, ArrowLeft: -1 };
        let target = null;
        if (event.key in offsets) target = (index + offsets[event.key] + actions.length) % actions.length;
        if (event.key === "Home") target = 0; if (event.key === "End") target = actions.length - 1;
        if (target !== null) { event.preventDefault(); changeAction(id, actions[target][0]); }
      },
    }));
    card.append(h("div", { class: "action-tabs", role: "tablist", "aria-label": "상세 옵션 작업" }, actionButtons));
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
      const f = form("correctionForm", f => { if (validate(f, [{ target: value.input, valid: nonempty(dr.value), message: "기록할 보정값을 입력하세요. 0도 가능합니다." }, { target: reason.input, valid: nonempty(dr.reason), message: "보정 이유를 입력하세요." }])) submitRecord(id, active, "/corrections", { ...base, field: dr.field, value: dr.value, reason: dr.reason.trim() }, "보정값을 저장하고 검사를 갱신했습니다. 바뀐 결과를 확인하세요.", "reviewed"); });
      f.append(h("fieldset", { class: "workflow-fields", disabled: busy }, h("legend", { text: "원문을 보존하고 값 보정" }), h("p", { class: "help", text: "보정하면 이전 판단은 재확인이 필요합니다. 규칙 검사는 저장 후 자동으로 갱신합니다." }), h("label", { class: "field" }, h("span", { text: "보정할 항목" }), select), value.wrap, reason.wrap, h("button", { type: "submit", class: "btn primary", disabled: !options.length, text: "보정값 저장 · 원문 보존" }))); panel.append(f);
    } else if (active === "recheck") {
      const f = form("recheckForm", () => submitRecord(id, active, "/recheck", base, "현재 버전의 검사를 마쳤습니다. 결과를 확인한 뒤 사람이 보류 또는 해소를 판단하세요."));
      f.append(h("fieldset", { class: "workflow-fields", disabled: busy }, h("legend", { text: "현재 값으로 다시 검사" }), h("p", { class: "help", text: rv.recheck_needed ? "현재 버전의 재검사가 필요합니다. 재검사만으로 해소되지는 않습니다." : "현재 버전의 재검사를 마쳤습니다. 결과가 맞는지 사람이 확인하세요." }),
        h("button", { type: "submit", class: "btn primary", disabled: !rv.recheck_needed, text: rv.recheck_needed ? "현재 버전 재검사" : "현재 버전 재검사 완료" }), h("button", { type: "button", class: "btn", disabled: rv.recheck_needed, text: "해소 조건 확인", onclick: () => changeAction(id, "resolve") }))); panel.append(f);
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
    card.append(panel, h("p", { id: "draftStatus", class: "help draft-status", text: draft(id, active).dirty ? "작성 중인 내용은 이 탭에서 기록별로 보관됩니다. 새로고침하기 전에는 저장하세요." : "작성한 내용은 다른 기록으로 이동해도 이 탭에서 유지됩니다." }));
    return card;
  }

  function renderHandover() {
    const v = $("viewHandover");
    v.replaceChildren();
    const sum = S.list?.summary || {}, total = sum.total ?? 0, judgedCount = (sum.resolved || 0) + (sum.held || 0);
    const scopes = [
      ["current", "현재 기록 1건", S.detail ? recordName(S.detail) : "검수 화면에서 기록을 하나 열어 주세요.", !!S.id],
      ["picked", "체크한 " + S.picked.size + "건", "목록에서 체크한 기록만 포함합니다.", S.picked.size > 0],
      ["all", "이번 범위 전체 " + total + "건 (판단 " + judgedCount + "건)", scopeName() + "의 모든 기록을 포함합니다.", total > 0]];
    const scopeOk = (value) => !!scopes.find((entry) => entry[0] === value)?.[3];
    if (!scopeOk(S.handoverScope)) S.handoverScope = scopes.find((entry) => entry[3])?.[0] || S.handoverScope;
    const status = h("p", { id: "handoverStatus", class: "help", role: "status", tabindex: "-1", text: S.handoverMessage });
    const mode = h("fieldset", { class: "scope-cards", disabled: S.handoverPending || S.reportOpening }, h("legend", { class: "sr-only", text: "포함할 기록" }));
    const b = h("button", { type: "button", class: "btn tea", text: "보고서 만들기", onclick: async () => {
      if (S.handoverPending || S.reportOpening) return;
      S.handoverPending = true; b.disabled = true; mode.disabled = true;
      $("savedReportsSlot")?.querySelectorAll("[data-report-open]").forEach(button => { button.disabled = true; });
      S.handoverMessage = "검수 보고서 고정본을 저장하고 있습니다."; status.textContent = S.handoverMessage;
      let saved = false;
      try {
        const ids = S.handoverScope === "picked" ? [...S.picked] : S.handoverScope === "all" ? await scopeRecordIds() : S.id ? [S.id] : [];
        if (!ids.length) throw new ApiError(0, { code: "validation", message: "보고서에 포함할 기록을 먼저 선택하세요." });
        const handover = await api("POST", "/api/handovers", { expected_workspace_revision: S.boot.workspace_revision, record_ids: ids });
        if (typeof handover.id !== "string" || !Number.isInteger(handover.workspace_revision) || !Array.isArray(handover.questions)) throw new ApiError(200, { code: "invalid_response", message: "저장 응답을 확인할 수 없습니다. 아래에 이전 고정본이 있다면 그대로 유지됩니다." });
        S.handover = handover; S.reportDetails.set(handover.id, handover); loadReports(); saved = true; S.handoverMessage = "검수 보고서 고정본을 저장했습니다.";
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
    for (const [value, label, help, ok] of scopes) {
      mode.append(h("label", { class: "scope-card" },
        h("input", { type: "radio", name: "handoverScope", value, checked: S.handoverScope === value, disabled: !ok, onchange: () => {
          S.handoverScope = value; b.disabled = !scopeOk(value);
          S.handoverMessage = ""; status.textContent = "";
        } }), h("span", { class: "sc-body" }, h("strong", { text: label }), h("span", { class: "muted-text", text: help }))));
    }
    b.disabled = S.handoverPending || S.reportOpening || !scopeOk(S.handoverScope);
    v.append(h("div", { class: "stack" }, h("section", { class: "card report-make" },
      h("p", { class: "k", text: "01 · Scope" }), h("h2", { text: "보고서 만들기" }),
      h("p", { class: "muted-text", text: "고른 기록의 검수 상태를 고정해 저장합니다. 이후 수정은 새 보고서에만 반영됩니다." }),
      S.scope === "demo" ? h("p", { class: "inline-warning", text: "시연 예제의 기록만 포함됩니다. 실제 자료의 검수 결과가 아닙니다." }) : null,
      mode, h("div", { class: "toolbar" }, b), status)));

    const hd = S.handover;
    if (!hd) { v.firstChild.append(reportsCard()); return; }
    const sc = hd.scope || {}, ct = hd.counts || {};
    const dl = f => h("a", { class: "btn " + (f === "html" ? "black" : ""), href: BASE + "/api/handovers/" + enc(hd.id) + "/download?format=" + f, text: f === "html" ? "HTML 보고서" : f.toUpperCase(), download: "" });
    v.firstChild.append(h("section", { class: "card current-report", id: "handoverResult", tabindex: "-1" },
      h("p", { class: "k", text: "열린 고정 보고서" }), h("h3", { text: readableTime(hd.created_at) }),
      h("p", { class: "report-count", text: "총 " + val(sc.count) + "건 중 보류 " + val(ct.held) + "건 · 해소 " + val(ct.resolved) + "건" }),
      h("p", { class: "help", text: val(sc.description) }),
      hd.workspace_revision < S.boot.workspace_revision ? h("p", { class: "inline-warning", text: "저장 후 검수 내용이 변경되었습니다. 다운로드는 저장 당시 고정본입니다." }) : null,
      h("div", { class: "toolbar" }, dl("html"), dl("csv"), dl("json")),
      h("details", {}, h("summary", { text: "식별정보와 무결성" }), h("dl", { class: "kv" },
        h("dt", { text: "보고서 ID" }), h("dd", { class: "mono", text: hd.id }), h("dt", { text: "작업공간 버전" }), h("dd", { text: hd.workspace_revision }),
        h("dt", { text: "규칙 버전" }), h("dd", { text: val(hd.rule_version) }), h("dt", { text: "SHA-256" }), h("dd", { class: "mono", text: val(hd.sha256) })))),
      h("details", { class: "evidence-section" }, h("summary", { text: "미해결 질문 " + (hd.questions || []).length + "건" }),
        (hd.questions || []).length ? h("ul", { class: "history" }, hd.questions.map(q => h("li", {}, identifier(q.record_id, "기록 ID"), h("strong", { text: L.resolution[q.state] || val(q.state) }), h("p", { text: val(q.question) }), q.next_action && q.next_action !== q.question ? h("p", { class: "help", text: "다음 확인: " + q.next_action }) : null))) : h("p", { text: "남은 질문이 없습니다." })), reportsCard());
  }

  function readableTime(value) {
    const date = new Date(value);
    return Number.isNaN(date.valueOf()) ? val(value) : new Intl.DateTimeFormat("ko-KR", { dateStyle: "medium", timeStyle: "short" }).format(date);
  }

  /* ---------- multi-file import ---------- */
  function newImport() { return { pairs: new Map(), running: false, finished: false, autoAi: true, saved: 0, index: 0, total: 0, done: 0 }; }
  const PAIR_STATE = { checking: "확인 중", ready: "가져올 수 있음", missing: "짝 없음", invalid: "가져올 수 없음", jsonbad: "JSON 형식 오류", queued: "대기", running: "저장 중", ai: "AI 비교 중", saved: "완료", dup: "중복", failed: "실패" };
  async function inspectJson(file) {
    try { JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer()).replace(/^\uFEFF/, "")); return ""; }
    catch (error) { return error instanceof SyntaxError ? "JSON 문법을 확인하세요. 원문은 자동 수정하지 않습니다." : "JSON이 올바른 UTF-8 형식이 아닙니다."; }
  }
  const setImportStatus = (text) => { $("importStatus").textContent = text; };

  async function checkPairFiles(image, annotation) {
    if (!image.size || image.size > 10 * 1024 * 1024 || !annotation.size || annotation.size > 1024 * 1024) throw new Error("사진은 10 MiB 이하, JSON은 1 MiB 이하의 비어 있지 않은 파일이어야 합니다.");
    const signature = new Uint8Array(await image.slice(0, 3).arrayBuffer());
    if (signature[0] !== 255 || signature[1] !== 216 || signature[2] !== 255) throw new Error("JPEG 파일 내용이 아닙니다.");
    let annotationJson;
    try { annotationJson = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(await annotation.arrayBuffer()); }
    catch { throw new Error("JSON이 올바른 UTF-8 형식이 아닙니다. 자동 변환하거나 저장하지 않았습니다."); }
    if (annotationJson.charCodeAt(0) === 0xFEFF) throw new Error("JSON 파일 앞에 UTF-8 BOM이 있습니다. BOM 없는 UTF-8로 다시 내보내세요.");
    let parsed;
    try { parsed = JSON.parse(annotationJson); } catch { throw new Error("JSON 문법을 확인하세요. 원문은 자동 수정하지 않습니다."); }
    if (parsed?.images?.imagepath !== image.name) throw new Error("JSON의 images.imagepath가 JPEG 파일명 " + image.name + "과(와) 정확히 같아야 합니다.");
    const [width, height] = await new Promise((resolve, reject) => {
      const objectUrl = URL.createObjectURL(image), probe = new Image();
      probe.onload = () => { URL.revokeObjectURL(objectUrl); resolve([probe.naturalWidth, probe.naturalHeight]); };
      probe.onerror = () => { URL.revokeObjectURL(objectUrl); reject(new Error("JPEG 이미지를 해석하지 못했습니다.")); };
      probe.src = objectUrl;
    });
    if (width * height > 16000000) throw new Error("JPEG는 1,600만 화소 이하여야 합니다.");
  }
  async function validatePair(pair) {
    const token = ++pair.token;
    pair.state = "checking"; pair.message = ""; pair.quality = []; updateImportState();
    let result;
    const jsonProblem = pair.json ? await inspectJson(pair.json) : "";
    if (jsonProblem) result = { state: "jsonbad", message: jsonProblem };
    else if (!pair.jpg || !pair.json) result = { state: "missing", message: pair.jpg ? "같은 이름의 JSON이 없습니다." : "같은 이름의 JPEG가 없습니다." };
    else {
      try { await checkPairFiles(pair.jpg, pair.json); result = { state: "ready", message: "" }; }
      catch (error) { result = { state: "invalid", message: error.message }; }
    }
    if (token !== pair.token) return;
    Object.assign(pair, result); updateImportState();
    if (result.state === "ready") await measurePair(pair, token);
  }
  function addImportFiles(files) {
    const imp = S.imp;
    if (imp.running) return;
    if (imp.finished) { imp.pairs.clear(); imp.finished = false; imp.saved = 0; setImportStatus(""); }
    const touched = new Set();
    for (const file of files) {
      const match = /^(.+)\.(jpe?g|json)$/i.exec(file.name);
      if (!match) { imp.pairs.set("!" + file.name, { stem: file.name, token: 0, state: "invalid", message: ".jpg, .jpeg, .json 파일만 가져올 수 있습니다.", other: true }); continue; }
      const stem = match[1], pair = imp.pairs.get(stem) || { stem, token: 0, jpg: null, json: null, state: "checking", message: "" };
      pair[/json$/i.test(match[2]) ? "json" : "jpg"] = file; imp.pairs.set(stem, pair); touched.add(pair);
    }
    updateImportState();
    for (const pair of touched) validatePair(pair);
  }
  function pairRow(pair) {
    const imp = S.imp, locked = imp.running, removable = !locked && !["saved", "dup"].includes(pair.state);
    const pres = (label, ok) => h("span", { class: "pres", "data-ok": String(ok), text: label + " " + (ok ? "있음" : "없음") });
    return h("li", { class: "pair-row", "data-state": pair.state },
      h("span", { class: "stem", text: pair.stem }),
      pair.other ? h("span", { class: "pair-files", text: "지원하지 않는 형식" }) : h("span", { class: "pair-files" }, pres("사진", !!pair.jpg), pres("JSON", !!pair.json)),
      h("span", { class: "pair-state" }, h("b", { text: PAIR_STATE[pair.state] }), pair.message ? h("span", { class: "pair-msg", text: pair.message }) : null, pair.aiMessage ? h("span", { class: "pair-ai", text: pair.aiMessage }) : null,
        pair.quality?.length ? h("span", { class: "pair-q" }, pair.quality.map((w) => h("span", { class: "q-chip", text: QUALITY_TEXT[w.code].title + " · 확인 권장" }))) : null),
      h("span", { class: "pair-act" },
        pair.state === "dup" && !locked ? h("button", { type: "button", class: "btn mini", text: "기존 기록 열기", "aria-label": pair.stem + " 기존 기록 열기", onclick: () => openExisting(pair) }) : null,
        removable ? h("button", { type: "button", class: "btn mini", text: "빼기", "aria-label": pair.stem + " 목록에서 빼기", onclick: () => { imp.pairs.delete(pair.other ? "!" + pair.stem : pair.stem); updateImportState(); } }) : null));
  }
  async function openRecordById(id) {
    if ($("importDialog").open) $("importDialog").close("review");
    S.scope = "actual"; S.filter = "all"; S.q = ""; S.page = 1; S.picked.clear(); $("searchInput").value = "";
    updatePicked(); renderTop(); setView("records");
    await loadList(); await selectRecord(id);
  }
  /* Duplicate rows: find the stored record through the list search (GET /api/records?scope=actual&filter=all&q=<stem>). */
  async function openExisting(pair) {
    if (S.imp.running) return;
    setImportStatus("기존 기록을 찾는 중입니다.");
    try {
      const res = await api("GET", "/api/records?scope=actual&filter=all&q=" + enc(pair.stem));
      const hit = (res.items || []).find((item) => (item.label || "").replace(/\.(jpe?g|json)$/i, "") === pair.stem) || (res.items || [])[0];
      const id = hit?.id || pair.existingId;
      if (!id) throw new ApiError(0, { code: "not_found", message: "기존 기록을 찾지 못했습니다. 내 실제 자료 목록에서 검색해 보세요." });
      await openRecordById(id);
    } catch (error) { setImportStatus(error.message); }
  }
  function updateImportState() {
    const imp = S.imp, enabled = S.boot?.capabilities?.import_pair === true, pairs = [...imp.pairs.values()];
    $("pairList").replaceChildren(...pairs.map(pairRow));
    const count = (...states) => pairs.filter((pair) => states.includes(pair.state)).length;
    const ready = count("ready"), aiReady = classifierReady(), files = pairs.reduce((n, pair) => n + (pair.jpg ? 1 : 0) + (pair.json ? 1 : 0) + (pair.other ? 1 : 0), 0);
    const summary = $("pairSummary");
    summary.hidden = !pairs.length;
    const line = imp.running || imp.finished
      ? ["파일 " + files + "개", "완료 " + count("saved"), count("dup") ? "중복 " + count("dup") : "", count("failed") ? "실패 " + count("failed") : ""]
      : ["파일 " + files + "개", "가져올 쌍 " + ready, "제외 " + pairs.filter((pair) => !["ready", "checking"].includes(pair.state)).length];
    summary.replaceChildren(h("span", { class: "k", text: line.filter(Boolean).join(" · ") }),
      h("button", { type: "button", class: "link-btn", id: "pairClear", disabled: imp.running, text: "모두 지우기",
        onclick: () => { imp.pairs.clear(); imp.finished = false; imp.saved = 0; setImportStatus(""); updateImportState(); } }));
    $("importSubmitLabel").textContent = imp.running ? "가져오는 중… " + imp.index + "/" + imp.total : ready ? ready + "쌍 가져오기" : "가져오기";
    $("importBar").style.width = imp.running && imp.total ? Math.round(((imp.done + 0.5) / imp.total) * 100) + "%" : "0%";
    $("importSubmit").classList.toggle("busy", imp.running);
    $("importSubmit").disabled = !enabled || imp.running || ready === 0;
    $("importClose").disabled = imp.running;
    $("autoAi").disabled = !aiReady || imp.running; $("autoAi").checked = imp.autoAi && aiReady;
    $("autoAiHelp").textContent = aiReady ? "체크하면 저장 직후 사진을 실험 모델로 보내 예측을 받고 규칙을 갱신합니다. 체크하지 않으면 사진은 전송되지 않습니다." : "실험 모델에 연결되어 있지 않아 AI 비교는 건너뜁니다.";
    $("importReview").hidden = !(imp.finished && imp.saved > 0 && $("importDialog").open);
    $("importForm").setAttribute("aria-busy", String(imp.running));
    if (!enabled && !imp.running) setImportStatus("서버에서 파일 가져오기를 아직 사용할 수 없습니다. 연결 후 화면을 다시 불러오세요.");
    else if (!imp.running && !imp.finished && /^서버에서 파일 가져오기를/.test($("importStatus").textContent)) setImportStatus("");
  }
  const bumpWorkspace = (detail) => { if (Number.isInteger(detail.workspace_revision)) S.boot.workspace_revision = Math.max(S.boot.workspace_revision, detail.workspace_revision); };

  async function importOne(pair, wantAi) {
    const imageBase64 = await readImage(pair.jpg), annotationJson = new TextDecoder("utf-8").decode(await pair.json.arrayBuffer());
    const post = () => api("POST", "/api/imports", { expected_workspace_revision: S.boot.workspace_revision, image_name: pair.jpg.name, image_base64: imageBase64, annotation_name: pair.json.name, annotation_json: annotationJson });
    let receipt;
    try { receipt = await post(); }
    catch (error) {
      if (error.status !== 409 || error.code === "duplicate_import") throw error;
      await refreshBootstrap(); receipt = await post();
    }
    if (typeof receipt.record_id !== "string" || !Number.isInteger(receipt.record_revision) || !Number.isInteger(receipt.workspace_revision)) throw new ApiError(200, { code: "invalid_response", message: "저장 응답을 확인할 수 없습니다." });
    S.boot.workspace_revision = Math.max(S.boot.workspace_revision, receipt.workspace_revision);
    S.importedRevisions.set(receipt.record_id, receipt.record_revision);
    pair.recordId = receipt.record_id;
    if (!wantAi) return;
    pair.state = "ai"; updateImportState();
    try {
      let detail = await postRec(receipt.record_id, "/classify", { expected_revision: receipt.record_revision });
      bumpWorkspace(detail); S.importedRevisions.set(receipt.record_id, detail.revision);
      if (detail.model_attempt?.status === "failed" || detail.model?.status === "failed") { pair.aiMessage = "AI 비교 실패: 원본은 저장되었습니다. 상세 화면에서 다시 시도할 수 있습니다."; return; }
      try { detail = await postRec(receipt.record_id, "/recheck", { expected_revision: detail.revision }); bumpWorkspace(detail); S.importedRevisions.set(receipt.record_id, detail.revision); pair.aiMessage = "AI 비교 완료"; }
      catch (error) { if (!(error instanceof ApiError)) throw error; pair.aiMessage = "AI 결과는 저장되었지만 검사 갱신에 실패했습니다. 상세 화면에서 검사만 갱신하세요. AI는 다시 실행하지 않습니다."; }
    } catch (error) {
      if (!(error instanceof ApiError)) throw error;
      pair.aiMessage = "AI 비교 실패: " + error.message;
    }
  }
  async function runImport(event) {
    event.preventDefault();
    const imp = S.imp, ready = [...imp.pairs.values()].filter((pair) => pair.state === "ready");
    if (imp.running || !ready.length || !S.boot?.capabilities?.import_pair) return;
    const wantAi = imp.autoAi && classifierReady();
    imp.running = true; imp.saved = 0; imp.total = ready.length; imp.done = 0; imp.index = 1; let dup = 0, failed = 0, uncertain = false;
    for (const pair of ready) { pair.state = "queued"; pair.message = ""; pair.aiMessage = ""; }
    updateImportState();
    for (const [index, pair] of ready.entries()) {
      imp.index = index + 1; pair.state = "running"; updateImportState();
      setImportStatus((index + 1) + " / " + ready.length + "쌍 처리 중");
      try { await importOne(pair, wantAi); pair.state = "saved"; imp.saved++; }
      catch (error) {
        if (error.code === "duplicate_import") { pair.state = "dup"; pair.message = "이미 가져온 자료입니다."; pair.existingId = error.details?.record_id; dup++; }
        else {
          pair.state = "failed"; pair.message = error.message; failed++;
          if (error instanceof ApiError && (error.status === 0 || error.status >= 500)) { uncertain = true; break; }
        }
      }
      imp.done++;
    }
    for (const pair of ready) if (pair.state === "queued") pair.state = "ready";
    imp.running = false; imp.finished = true;
    try {
      await refreshBootstrap();
      S.scope = "actual"; S.filter = "all"; S.q = ""; S.page = 1; S.picked.clear(); S.id = null; S.detail = null; ++S.detailRequest;
      $("searchInput").value = ""; updatePicked(); renderTop();
      await loadList();
    } catch (error) { showError(error); }
    const summary = imp.saved + "쌍 저장" + (dup ? " · 중복 " + dup + "쌍" : "") + (failed ? " · 실패 " + failed + "쌍" : "") + (wantAi ? " · AI 비교 요청 포함" : "");
    setImportStatus(summary + (uncertain ? ". 저장 여부를 확인할 수 없는 쌍이 있습니다. 목록을 확인한 뒤 남은 쌍을 다시 가져오세요. 자동 재시도하지 않았습니다." : ""));
    announce(summary); updateImportState();
    if (imp.saved > 0 && !failed && !uncertain) await finishImport();
    else if (!imp.saved && dup > 0 && !failed && !uncertain) await openExisting(ready.find((pair) => pair.state === "dup"));
  }
  async function finishImport() {
    const target = S.list?.items.find(isPending)?.id ?? S.list?.items[0]?.id;
    if ($("importDialog").open) $("importDialog").close("review");
    flash(S.imp.saved + "쌍을 가져왔습니다.");
    setView("records");
    if (target) await selectRecord(target); else renderDetail();
  }
  function openImport(files) {
    const imp = S.imp;
    if (!imp.running && imp.finished) { imp.pairs.clear(); imp.finished = false; imp.saved = 0; setImportStatus(""); }
    if (!$("importDialog").open) $("importDialog").showModal();
    updateImportState();
    if (files?.length) addImportFiles(files);
  }
  function modelInfo() { return S.classifier?.model || S.boot?.model_connection?.model || null; }
  function modelLabel() { const m = modelInfo(); return m?.name ? m.name + (m.version ? " v" + m.version : "") : "실험 모델"; }
  function renderClassifier() {
    const node = $("modelStatus"), ready = classifierReady(), c = S.classifier;
    const state = S.classifierPending ? "checking" : ready ? "ready" : c && c.enabled === true ? "warn" : "down";
    node.dataset.state = state;
    $("modelStatusText").textContent = S.classifierPending ? "연결 확인 중" : S.classifierError || (ready ? "연결됨" : state === "warn" ? "응답이 불안정합니다" : "연결되지 않음");
    $("modelName").textContent = modelLabel();
    const latency = Number.parseFloat(c?.latency_ms);
    $("modelMeta").textContent = ready && !Number.isNaN(latency) ? "응답 " + Math.round(latency) + " ms" : state === "ready" || state === "checking" ? "" : "AI 비교를 사용할 수 없습니다";
    $("classifierCheck").disabled = S.classifierPending;
  }
  async function checkClassifier() {
    if (S.classifierPending) return;
    S.classifierPending = true; S.classifierError = ""; renderClassifier();
    try { S.classifier = await api("GET", "/api/model/status"); }
    catch (error) { S.classifier = null; S.classifierError = "연결 확인 실패"; }
    finally { S.classifierPending = false; renderClassifier(); updateImportState(); if (S.detail) renderDetail(); }
  }

  function readImage(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onerror = () => reject(new Error("사진 파일을 읽지 못했습니다. 다시 선택하세요."));
      reader.onabort = () => reject(new Error("사진 읽기가 취소되었습니다."));
      reader.onload = () => { const data = String(reader.result || ""), comma = data.indexOf(","); if (comma < 0) reject(new Error("사진 인코딩을 읽지 못했습니다.")); else resolve(data.slice(comma + 1)); };
      reader.readAsDataURL(file);
    });
  }

  async function loadReports() {
    if (S.reportsPending) return;
    S.reportsPending = true; S.reportsError = "";
    const refresh = $("reportsRefresh");
    if (refresh) { refresh.textContent = "목록 확인 중"; refresh.setAttribute("aria-busy", "true"); refresh.setAttribute("aria-disabled", "true"); }
    try { const result = await api("GET", "/api/handovers"); if (!Array.isArray(result.items)) throw new Error("저장된 보고서 목록 응답을 확인할 수 없습니다."); S.reports = result.items; }
    catch (error) { S.reportsError = error.message; }
    finally { S.reportsPending = false; refreshReportsSlot(); loadReportDetails(); }
  }
  /* Counts per saved report come from GET /api/handovers/{id}, fetched lazily and cached. */
  async function loadReportDetails() {
    const queue = (S.reports || []).slice(0, 30).map((item) => item.snapshot_id).filter((id) => !S.reportDetails.has(id));
    const worker = async () => {
      while (queue.length) {
        const id = queue.shift();
        if (S.reportDetails.has(id)) continue;
        try { S.reportDetails.set(id, await api("GET", "/api/handovers/" + enc(id))); } catch (error) { S.reportDetails.set(id, { error: true }); }
        refreshReportsSlot();
      }
    };
    await Promise.all([worker(), worker(), worker()]);
  }

  function refreshReportsSlot() {
    const slot = $("savedReportsSlot"); if (!slot || S.view !== "handover") return;
    const focusedId = slot.contains(document.activeElement) ? document.activeElement.id : null;
    slot.replaceWith(reportsCard());
    if (focusedId) $(focusedId)?.focus({ preventScroll: true });
  }

  function reportsCard() {
    const card = h("section", { class: "card report-saved", id: "savedReportsSlot" },
      h("div", { class: "head-row spread" }, h("p", { class: "k", text: "02 · Saved" }),
        h("button", { type: "button", class: "btn mini", id: "reportsRefresh", disabled: S.reportsPending, text: S.reportsPending ? "목록 확인 중" : "다시 확인", onclick: loadReports })),
      h("h3", { text: "저장한 보고서" }),
      h("p", { class: "help", text: "이 브라우저의 비공개 작업공간에 저장한 고정본입니다. 쿠키가 없거나 다른 작업공간이면 열 수 없습니다." }));
    if (S.reportsError) card.append(h("p", { role: "status", text: "보고서 목록 확인 실패: " + S.reportsError }));
    else if (S.reports && !S.reports.length) card.append(h("p", { class: "empty-line", text: "아직 저장한 보고서가 없습니다. 위에서 범위를 고르고 만들어 보세요." }));
    const list = h("ul", { class: "rep-list" });
    for (const item of S.reports || []) {
      const det = S.reportDetails.get(item.snapshot_id), ct = det?.counts || {};
      const countText = !det ? "집계 불러오는 중" : det.error ? "집계를 불러오지 못했습니다" : "기록 " + val(det.scope?.count) + "건 · 보류 " + val(ct.held) + " · 해소 " + val(ct.resolved);
      const link = (format, label) => h("a", { class: "btn mini", href: BASE + "/api/handovers/" + enc(item.snapshot_id) + "/download?format=" + format, download: "", text: label, "aria-label": readableTime(item.created_at) + " 보고서 " + label + " 내려받기" });
      list.append(h("li", { class: "rep-row" },
        h("div", { class: "rep-date" }, h("strong", { text: readableTime(item.created_at) }), h("span", { class: "muted-text mono", title: "SHA-256 " + val(item.sha256), text: "v" + val(item.workspace_revision) })),
        h("div", { class: "rep-counts", text: countText }),
        h("div", { class: "rep-actions" }, link("html", "HTML"), link("csv", "CSV"), link("json", "JSON"),
          h("button", { type: "button", class: "btn mini", id: "report-open-" + item.snapshot_id, "data-report-open": "", disabled: S.reportOpening || S.handoverPending, text: "열기", onclick: async () => {
            if (S.reportOpening || S.handoverPending) return; S.reportOpening = true; renderHandover();
            try { const hd = await api("GET", "/api/handovers/" + enc(item.snapshot_id)); if (hd.id !== item.snapshot_id) throw new Error("요청한 보고서와 다른 응답입니다."); S.handover = hd; S.handoverMessage = "저장 당시 범위와 내용을 열었습니다. 현재 선택 범위와는 별개입니다."; }
            catch (error) { S.handoverMessage = "보고서를 열지 못했습니다: " + error.message; }
            finally { S.reportOpening = false; if (S.view === "handover") { renderHandover(); $("handoverResult")?.focus(); } announce(S.handoverMessage); }
          } }))));
    }
    if (list.childElementCount) card.append(list);
    return card;
  }

  /* ---------- help ---------- */
  function renderRules() {
    const v = $("viewRules"); v.replaceChildren();
    const reg = (S.boot && S.boot.rule_registry) || [];
    const ctxOut = h("pre", { class: "json", hidden: true });
    const cb = h("button", { type: "button", class: "btn", text: "읽기 전용 컨텍스트 내보내기", onclick: () => send(cb, async () => {
      const r = await api("POST", "/api/agent/context", { record_ids: S.picked.size ? [...S.picked] : null });
      ctxOut.textContent = JSON.stringify(r, null, 2); ctxOut.hidden = false;
    }) });
    const chip = (text, tone) => h("span", { class: "mini-chip " + (tone || ""), text });
    const step = (n, title, text, ...visual) => h("li", { class: "step" }, h("span", { class: "step-n", text: n }),
      h("div", { class: "step-body" }, h("strong", { text: title }), h("p", { class: "help", text }), h("div", { class: "step-visual", "aria-hidden": "true" }, visual)));
    const keys = [["1", "원본이 맞음"], ["2", "AI가 맞음"], ["3", "모르겠음 (보류)"], ["Enter", "기록하기 (확인 문장이 보일 때)"], ["Esc", "취소"], ["J", "다음 기록"], ["K", "이전 기록"], ["Enter", "사진에서: 크게 보기"]];
    const faq = [
      ["점수는 무엇인가요?", "AI가 사진을 보고 낸 분류 점수입니다. 정답일 확률이 아니므로 높아도 틀릴 수 있습니다. 작물과 주차를 따로 보여 주며, 없는 점수는 추측해서 채우지 않습니다."],
      ["원본은 바뀌나요?", "바뀌지 않습니다. 'AI가 맞음'을 고르면 원본 JSON은 그대로 두고 보정값을 따로 기록하며, 변경 이력에 남습니다."],
      ["다른 팀원과 공유되나요?", "가져온 자료와 판단은 이 브라우저의 비공개 작업공간에만 저장됩니다. 팀원에게 전달하려면 보고서를 HTML·CSV·JSON으로 내려받아 공유하세요."],
      ["시연 예제는 무엇인가요?", "기능을 둘러보기 위한 가상 자료입니다. 실제 모델을 호출하지 않고, 판단을 저장해도 내 실제 자료에는 반영되지 않습니다."],
      ["개인정보는 어떻게 되나요?", "'자료 가져오기'를 누르기 전에는 아무것도 업로드하지 않습니다. 사진은 'AI 비교'를 체크했을 때만 실험 모델로 전송됩니다."],
      ["'사진 확인 권장'은 무엇인가요?", "사진이 흐리거나(선명도 90 미만), 어둡거나(밝기 110 미만), 너무 밝거나(220 초과), 대비가 낮을 때(17 미만) 표시합니다. 상세 화면에서는 AI가 녹차가 아닌 작물로 분류했거나 녹차 분류 신뢰도가 0.8 미만일 때도 표시합니다. 기준값은 제공된 녹차 샘플 사진에서 정한 것이라 다른 농장·카메라·조명에서는 맞지 않을 수 있습니다. 식물이 없는 사진, 일부만 과노출된 사진, 가림, 병해, 라벨 오류는 찾지 못합니다. 확인을 돕는 표시일 뿐 라벨 오류 판정이 아니며 가져오기와 판단을 막지 않습니다."]];
    const rule = (title, text) => h("div", { class: "rule-item" }, h("strong", { text: title }), h("p", { class: "help", text }));
    v.append(h("div", { class: "stack help-view" },
      h("section", { class: "card" }, h("p", { class: "k", text: "01 · Flow" }), h("h2", { text: "검수는 이렇게 진행합니다" }),
        h("ol", { class: "steps" },
          step("01", "자료 가져오기", "같은 이름의 JPEG와 JSON을 여러 쌍 한꺼번에 선택하거나 끌어다 놓습니다. 파일이 없으면 시연 예제를 열어 보세요.", chip("A.jpg"), chip("A.json"), chip("1쌍", "tea")),
          step("02", "AI 비교 선택", "'가져온 뒤 바로 AI 비교'를 체크하면 저장 직후 사진을 실험 모델로 보냅니다. 체크하지 않으면 사진은 전송되지 않고, 나중에 'AI로 사진 비교'로 실행할 수 있습니다.", chip("AI 비교", "tea")),
          step("03", "어느 쪽이 맞는지 판단", "원본 주차와 AI 예측이 다르면 원본이 맞음, AI가 맞음, 모르겠음 중 하나를 고릅니다. 기록하기 전에 저장될 내용이 문장으로 표시됩니다.", chip("원본 3주"), chip("AI 4주", "tea"), chip("보류", "amber")),
          step("04", "기록하고 다음으로", "기록하면 다음 대기 기록으로 자동 이동합니다. 이전·다음 버튼이나 J / K 키로 직접 이동할 수도 있습니다.", chip("기록하기", "tea"), chip("2 / 8")),
          step("05", "보고서 받기", "모두 판단하면 보고서를 만들어 HTML·CSV·JSON으로 내려받습니다. 저장한 보고서는 보고서 탭에서 다시 엽니다.", chip("HTML"), chip("CSV"), chip("JSON")))),
      h("section", { class: "card" }, h("p", { class: "k", text: "02 · Keys" }), h("h2", { text: "키보드 단축키" }),
        h("div", { class: "tbl-wrap", tabIndex: 0, role: "region", "aria-label": "키보드 단축키 표" }, h("table", {},
          h("thead", {}, h("tr", {}, h("th", { scope: "col", text: "키" }), h("th", { scope: "col", text: "동작" }))),
          h("tbody", {}, keys.map(([key, text]) => h("tr", {}, h("th", { scope: "row" }, h("kbd", { text: key })), h("td", { text })))))),
        h("p", { class: "help", text: "입력칸에 글자를 쓰는 중에는 단축키가 동작하지 않습니다." })),
      h("section", { class: "card" }, h("p", { class: "k", text: "03 · Rules" }), h("h2", { text: "어떤 기록을 확인 대상으로 표시하나요" }),
        rule("pH가 14를 넘을 때", "pH 값이 14를 넘으면 원문을 다시 확인하도록 표시합니다. 센서 고장이나 물리적 불가능으로 단정하지는 않습니다."),
        rule("필수 값이 빠졌을 때", "꼭 필요한 환경 값이 비었거나 숫자로 읽을 수 없으면 표시합니다. 숫자 0은 정상적인 값으로 읽습니다."),
        rule("사진과 기록의 주차가 다를 때", "AI가 사진에서 예측한 주차가 기록과 다르면 표시합니다. 어느 쪽이 맞는지는 사람이 판단합니다.")),
      h("section", { class: "card" }, h("p", { class: "k", text: "04 · FAQ" }), h("h2", { text: "자주 묻는 질문" }),
        faq.map(([q, a]) => h("details", { class: "faq" }, h("summary", { text: q }), h("p", { class: "help", text: a })))),
      h("details", { class: "evidence-section agent-technical" }, h("summary", { text: "기술 정보" }),
        h("div", { class: "agent-box" },
          h("p", { class: "k", text: "규칙 ID" }),
          reg.length ? reg.map((r) => h("div", { class: "check-item" }, h("strong", { class: "mono", text: r.rule_id + " v" + val(r.version) }),
            h("p", { text: "대상: " + ((r.fields || []).join(", ") || "미확인") + " · 가정 단위: " + val(r.assumed_unit) }), h("p", { text: "근거: " + val(r.rationale) }),
            h("p", { text: "단정하지 않는 것: " + val(r.not_concluded) }), h("p", { text: "다음 질문: " + val(r.next_question) })))
            : h("p", { class: "muted-text", text: "규칙 목록을 불러오지 못했습니다." }),
          h("p", { class: "k", text: "AgentStream" }),
          h("p", { text: "에이전트 질의는 아직 연결되지 않았습니다. 이미지 분류 모델 연결과는 별개이며 수동 검수와 보고서는 사용할 수 있습니다." }),
          h("p", { class: "muted-text", text: "아래 내보내기는 에이전트가 읽을 자료를 미리 확인하는 읽기 전용 기능이며 에이전트 응답이 아닙니다." }), cb, ctxOut))));
  }

  /* ---------- nav & init ---------- */
  function setView(name) {
    S.view = name; window.scrollTo(0, 0); updateTitle(); renderMbar();
    for (const t of document.querySelectorAll(".tab")) t.setAttribute("aria-current", t.dataset.view === name ? "page" : "false");
    $("viewRecords").hidden = name !== "records"; $("viewHandover").hidden = name !== "handover"; $("viewRules").hidden = name !== "rules";
    if (name === "handover") { renderHandover(); loadReports(); }
    if (name === "rules") renderRules();
  }
  async function stepRecord(delta) {
    const items = S.list?.items || [];
    if (!items.length) return;
    const index = items.findIndex((item) => item.id === S.id);
    if (index < 0) { selectRecord(items[0].id); return; }
    const to = index + delta;
    if (to >= 0 && to < items.length) { selectRecord(items[to].id); return; }
    const pages = Math.ceil((S.list.total || 0) / (S.list.page_size || S.pageSize)), step = delta > 0 ? 1 : -1;
    if (S.page + step < 1 || S.page + step > pages) return;
    S.page += step;
    if ((await loadList({ quiet: true })) === false) { S.page -= step; return; }
    const arr = S.list?.items || [], target = delta > 0 ? arr[0] : arr[arr.length - 1];
    if (target) selectRecord(target.id);
  }
  function hasFiles(event) { return [...(event.dataTransfer?.types || [])].includes("Files"); }
  async function init() {
    document.querySelectorAll("[data-scope]").forEach((button) => button.addEventListener("click", () => selectScope(button.dataset.scope)));
    document.querySelectorAll("[data-filter]").forEach((button) => button.addEventListener("click", () => { S.filter = button.dataset.filter; S.page = 1; renderSummary(); loadList(); }));
    $("selectedReport").addEventListener("click", () => { S.handoverScope = "picked"; setView("handover"); });
    $("importOpen").addEventListener("click", () => openImport());
    $("importClose").addEventListener("click", () => { if (!S.imp.running) $("importDialog").close(); });
    $("importDialog").addEventListener("cancel", (event) => { if (S.imp.running) { event.preventDefault(); announce("저장 중입니다. 완료 후 닫을 수 있습니다."); } });
    $("importDialog").addEventListener("close", () => { updateImportState(); if ($("importDialog").returnValue !== "review") $("importOpen").focus(); });
    $("importForm").addEventListener("submit", runImport);
    $("importReview").addEventListener("click", () => { finishImport(); });
    $("autoAi").addEventListener("change", (event) => { S.imp.autoAi = event.target.checked; });
    $("importDrop").append(dropZone((files) => addImportFiles(files), { title: "사진(.jpg)과 JSON(.json)을 여기에 놓으세요", hint: "같은 이름의 JPEG와 JSON을 한 쌍으로 묶습니다. 여러 쌍을 한꺼번에 선택할 수 있습니다." }));
    $("importLimits").textContent = "사진 10 MiB·1,600만 화소 이하, JSON 1 MiB 이하. 이름의 확장자 앞부분과 images.imagepath가 일치해야 합니다. EXIF 없는 JPEG만 허용합니다. 작업공간 최대 32쌍·128 MiB. 가져오기 버튼을 누르기 전에는 업로드하지 않으며, AI 전송은 위 체크박스를 켠 경우에만 일어납니다.";
    $("classifierCheck").addEventListener("click", checkClassifier);
    for (const t of document.querySelectorAll(".tab")) t.addEventListener("click", () => setView(t.dataset.view));
    $("filterForm").addEventListener("submit", (e) => { e.preventDefault(); S.q = $("searchInput").value.trim(); S.page = 1; renderSummary(); loadList(); });
    window.addEventListener("dragover", (event) => { if (hasFiles(event)) event.preventDefault(); });
    window.addEventListener("drop", (event) => { if (hasFiles(event)) { event.preventDefault(); openImport([...event.dataTransfer.files]); } });
    document.addEventListener("keydown", (event) => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
      if (S.view !== "records" || $("importDialog").open || $("lightbox").open) return;
      const target = event.target;
      if (target instanceof Element && target.matches("input:not([type=checkbox]):not([type=radio]):not([type=button]), textarea, select, [contenteditable]")) return;
      if (event.code === "KeyJ" || event.code === "KeyK") { event.preventDefault(); stepRecord(event.code === "KeyJ" ? 1 : -1); return; }
      const d = S.detail;
      if (!d || event.shiftKey || S.pendingWrites.has(d.id) || S.conflicts.has(d.id)) return;
      const dr = decisionDraft(d.id);
      if (/^[123]$/.test(event.key)) {
        if (isJudged(d) && !dr.changing) return;
        const choice = decisionChoices(d)[Number(event.key) - 1];
        if (choice?.enabled) { event.preventDefault(); setDecisionMode(d.id, choice.mode); }
      } else if (event.key === "Enter") {
        const commit = $("commitDecision");
        const selectedChip = target instanceof Element && target.matches(".hold-fields .chip[aria-pressed=\"true\"]");
        if (commit && !commit.disabled && (selectedChip || !(target instanceof Element && target.closest("button, a, summary, [role=button], [role=tab]")))) { event.preventDefault(); commitDecision(d.id); }
      } else if (event.key === "Escape" && dr.mode && $("confirmBlock")) { event.preventDefault(); cancelDecision(d.id); }
    });
    $("lbZoom").addEventListener("click", toggleLightboxZoom);
    $("lbImg").addEventListener("click", toggleLightboxZoom);
    $("lbClose").addEventListener("click", () => $("lightbox").close());
    $("lightbox").addEventListener("click", (event) => { if (event.target === $("lightbox") || event.target === $("lbStage")) $("lightbox").close(); });
    $("lightbox").addEventListener("close", () => { $("lbImg").removeAttribute("src"); S.lbOpener?.focus({ preventScroll: true }); });
    $("detail").tabIndex = -1; $("listBody").tabIndex = -1;
    renderClassifier(); updateImportState(); renderDetail();
    try { await refreshBootstrap(); }
    catch (e) { showError(e); $("topBadges").replaceChildren(badge("서버 연결 실패", "bad")); }
    await loadList();
    openFirstPending();
    await checkClassifier();
  }
  init();
})();
