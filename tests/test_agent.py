# Copyright (c) 2026 FarmCheck contributors.
"""Agent sessions, grounding, streaming failures, and secret isolation."""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import httpx2
import pytest
from fastapi.testclient import TestClient

from farmcheck.agent_client import AgentClient, AgentConfig, agent_config
from farmcheck.agent_sessions import AgentSessions
from farmcheck.app import create_app
from farmcheck.source import load_catalog
from farmcheck.store import DomainError, Store

type Vendor = tuple[list[httpx2.Request], dict[str, str]]


@pytest.fixture
def vendor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Vendor:
    """Inject deterministic NDJSON without making external calls."""
    monkeypatch.setenv("AGENTSTREAM_API_KEY", "test-secret-not-for-browser")
    monkeypatch.setenv("CHATBOT_ID", str(uuid4()))
    monkeypatch.setenv("BASE_URL", "https://api-gateway.jntp-ax.or.kr/agentstream")
    monkeypatch.setenv("FARM_AGENT_CONFIG", str(tmp_path / "absent.json"))
    requests: list[httpx2.Request] = []
    mode = {"value": "ok"}

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        assert request.headers["X-API-Key"] == "test-secret-not-for-browser"
        for forbidden in ("authorization", "cookie", "x-user-id", "x-org-id"):
            assert forbidden not in request.headers
        if request.url.path.endswith("/sessions"):
            return httpx2.Response(
                201, json={"session_id": str(uuid4())}, headers={"set-cookie": "vendor=private"}
            )
        if request.url.path.endswith("/health"):
            return httpx2.Response(200, json={"status": "ready"})
        if mode["value"] == "auth":
            return httpx2.Response(403, text="test-secret-not-for-browser vendor internal details")
        if mode["value"] == "malformed":
            return httpx2.Response(200, content=b"{broken}\n")
        if mode["value"] == "error":
            return httpx2.Response(
                200, content=b'{"event":"turn.failed","content":"private details"}\n'
            )
        stream = (
            json.dumps(
                {"event": "assistant.delta", "content": "pH 원문을 확인하세요. [E1]"},
                ensure_ascii=False,
            )
            + "\n"
        )
        if mode["value"] != "truncated":
            stream += '{"event":"turn.completed"}'
        return httpx2.Response(200, content=stream.encode())

    original = AgentClient.__init__

    def init(self: AgentClient, config: AgentConfig) -> None:
        original(self, config)
        self.http = httpx2.AsyncClient(
            transport=httpx2.MockTransport(handle), headers={"X-API-Key": config.api_key}
        )

    monkeypatch.setattr(AgentClient, "__init__", init)
    return requests, mode


def test_followups_isolation_history_reset_and_grounding(vendor: Vendor, tmp_path: Path) -> None:
    """Reuse only the owner's session, cite actual values, and preserve review state."""
    requests, _ = vendor
    catalog = load_catalog("synthetic")
    record = next(iter(catalog.records))
    app = create_app(catalog, tmp_path / "app.sqlite", "synthetic", "")
    payload = {"record_id": record, "question": "무엇을 확인하나요?", "expected_revision": 0}
    with TestClient(app) as owner, TestClient(app) as other:
        owner.get("/api/bootstrap")
        other.get("/api/bootstrap")
        assert owner.post("/api/agent/health").status_code == 200
        before = owner.get(f"/api/records/{record}").json()
        for _ in range(2):
            response = owner.post("/api/agent/query", json=payload)
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("application/x-ndjson")
            events = [json.loads(line) for line in response.text.splitlines()]
            assert events[-1]["event"] == "turn.completed"
            assert events[-1]["evidence"][0]["id"] == "E1"
            assert "test-secret" not in response.text
            assert "session_id" not in response.text
        messages = [r for r in requests if r.url.path.endswith("/messages")]
        assert messages[0].url == messages[1].url
        context = json.loads(messages[0].content)["message"]
        assert record in context
        assert "environment.ph" in context
        assert "evidence" in context
        assert "raw_json" not in context
        assert "member_path" not in context
        for other_id in catalog.records:
            if other_id != record:
                assert other_id not in context
        assert owner.get(f"/api/records/{record}").json() == before
        assert len(owner.get(f"/api/agent/conversations/{record}").json()["turns"]) == 2
        assert other.get(f"/api/agent/conversations/{record}").json()["turns"] == []
        other.post("/api/agent/query", json=payload)
        messages = [r for r in requests if r.url.path.endswith("/messages")]
        assert messages[-1].url != messages[0].url
        owner.delete(f"/api/agent/conversations/{record}")
        assert owner.get(f"/api/agent/conversations/{record}").json()["turns"] == []
        owner.post("/api/agent/query", json=payload)
        messages = [r for r in requests if r.url.path.endswith("/messages")]
        assert messages[-1].url != messages[0].url


@pytest.mark.parametrize("failure", ["auth", "malformed", "truncated", "error"])
def test_failed_turn_is_not_saved_and_retries_start_new_session(
    vendor: Vendor, tmp_path: Path, failure: str
) -> None:
    """A partial stream is not a completed answer and remote errors remain private."""
    requests, mode = vendor
    catalog = load_catalog("synthetic")
    record = next(iter(catalog.records))
    app = create_app(catalog, tmp_path / "app.sqlite", "synthetic", "")
    with TestClient(app) as client:
        client.get("/api/bootstrap")
        payload = {"record_id": record, "question": "설명해 주세요", "expected_revision": 0}
        mode["value"] = failure
        response = client.post("/api/agent/query", json=payload)
        assert json.loads(response.text.splitlines()[-1])["event"] == "error"
        assert "test-secret" not in response.text
        assert "private details" not in response.text
        assert client.get(f"/api/agent/conversations/{record}").json()["turns"] == []
        mode["value"] = "ok"
        result = client.post("/api/agent/query", json=payload)
        assert json.loads(result.text.splitlines()[-1])["event"] == "turn.completed"
        messages = [r for r in requests if r.url.path.endswith("/messages")]
        assert messages[0].url != messages[1].url


def test_reject_unknown_record_stale_and_unauthenticated(vendor: Vendor, tmp_path: Path) -> None:
    """Reject unauthorized scopes and stale requests before any external request."""
    requests, _ = vendor
    catalog = load_catalog("synthetic")
    record = next(iter(catalog.records))
    app = create_app(catalog, tmp_path / "app.sqlite", "synthetic", "/demo")
    with TestClient(app) as client:
        payload = {"record_id": record, "question": "설명", "expected_revision": 0}
        assert client.post("/demo/api/agent/query", json=payload).status_code == 401
        client.get("/demo/api/bootstrap")
        assert (
            client.post(
                "/demo/api/agent/query", json={**payload, "record_id": "foreign"}
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/demo/api/agent/query", json={**payload, "expected_revision": 1}
            ).status_code
            == 409
        )
        assert (
            client.post("/demo/api/agent/query", json={**payload, "question": " "}).status_code
            == 422
        )
        assert not requests


def test_session_lease_persistence_and_concurrent_turns(tmp_path: Path) -> None:
    """Independent server objects share one lease and persisted provider session."""
    store = Store(tmp_path / "sessions.sqlite")
    workspace = store.workspace(None)
    first, second = AgentSessions(store), AgentSessions(store)
    token, previous = first.acquire(workspace, "record", "bot")
    assert previous is None
    first.bind(token, "session")
    with pytest.raises(DomainError):
        second.acquire(workspace, "record", "bot")
    with pytest.raises(DomainError):
        second.reset(workspace, "record", "bot")
    first.finish(token, {"answer": "saved"})
    token, previous = second.acquire(workspace, "record", "bot")
    assert previous == "session"
    second.finish(token, None)
    token, previous = first.acquire(workspace, "record", "bot")
    assert previous is None
    first.finish(token, None)


def test_secret_config_permissions_and_redaction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Read a protected file, never display the key, reject unexpected API origins."""
    for key in (
        "AGENTSTREAM_API_KEY",
        "BASE_URL",
        "CHATBOT_ID",
        "AGENTSTREAM_CHATBOT_ID",
        "AGENTSTREAM_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / "agent.json"
    monkeypatch.setenv("FARM_AGENT_CONFIG", str(path))
    path.write_text(json.dumps({"AGENTSTREAM_API_KEY": "private-key", "CHATBOT_ID": str(uuid4())}))
    path.chmod(0o600)
    config = agent_config()
    assert config is not None
    assert "private-key" not in repr(config)
    path.chmod(0o644)
    assert agent_config() is None
    path.chmod(0o600)
    monkeypatch.setenv("BASE_URL", "https://untrusted.example")
    assert agent_config() is None


def test_fragmented_utf8_stream() -> None:
    """A UTF-8 character and NDJSON line may span multiple network chunks."""
    raw = ('{"event":"assistant.delta","content":"녹차"}\n{"event":"turn.completed"}').encode()

    class Chunks(httpx2.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            for byte in raw:
                yield bytes([byte])

    async def run() -> list[str]:
        client = AgentClient(AgentConfig("test", str(uuid4())))
        await client.close()
        client.http = httpx2.AsyncClient(
            transport=httpx2.MockTransport(
                lambda _: httpx2.Response(200, stream=Chunks()),
            )
        )
        try:
            return [delta async for delta in client.deltas("session", "question")]
        finally:
            await client.close()

    assert asyncio.run(run()) == ["녹차"]


def test_long_answer_with_verbose_provider_chunks() -> None:
    """AgentStream sends ~485 bytes per character chunk; a normal answer exceeds 256 KiB."""
    chunk = {
        "content": "가",
        "additional_kwargs": {"thinking_content": "", "session_id": str(uuid4())},
        "type": "AIMessageChunk",
        "name": None,
        "id": "run--" + str(uuid4()),
        "tool_calls": [],
        "invalid_tool_calls": [],
        "tool_call_chunks": [],
        "event": "assistant.delta",
        "padding": "x" * 250,
    }
    line = json.dumps(chunk, ensure_ascii=False) + "\n"
    count = 1200
    raw = (line * count + '{"event":"turn.completed"}\n').encode()
    assert len(raw) > 256 * 1024

    async def run() -> list[str]:
        client = AgentClient(AgentConfig("test", str(uuid4())))
        await client.close()
        client.http = httpx2.AsyncClient(
            transport=httpx2.MockTransport(lambda _: httpx2.Response(200, content=raw)),
        )
        try:
            return [delta async for delta in client.deltas("session", "question")]
        finally:
            await client.close()

    assert "".join(asyncio.run(run())) == "가" * count


def test_missing_configuration_keeps_review_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A missing key disables only chat and never invents a live answer."""
    for key in ("AGENTSTREAM_API_KEY", "CHATBOT_ID", "AGENTSTREAM_CHATBOT_ID"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("FARM_AGENT_CONFIG", str(tmp_path / "absent.json"))
    catalog = load_catalog("synthetic")
    record = next(iter(catalog.records))
    app = create_app(catalog, tmp_path / "app.sqlite", "synthetic", "")
    with TestClient(app) as client:
        assert client.get("/api/bootstrap").json()["agent_status"]["status"] == "not_configured"
        assert client.get(f"/api/records/{record}").status_code == 200
        response = client.post(
            "/api/agent/query",
            json={
                "record_id": record,
                "question": "설명",
                "expected_revision": 0,
            },
        )
        assert response.status_code == 503
        assert response.json()["code"] == "agent_not_configured"


def test_changed_record_during_answer_is_marked_stale(
    vendor: Vendor,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An answer based on an earlier record revision cannot silently appear current."""
    _ = vendor
    catalog = load_catalog("synthetic")
    record = next(iter(catalog.records))
    database = tmp_path / "app.sqlite"
    app = create_app(catalog, database, "synthetic", "")
    with TestClient(app) as client:
        client.get("/api/bootstrap")
        store = Store(database)
        ws = store.resolve_session(client.cookies["farmcheck_workspace"])
        original = AgentClient.deltas

        async def changed(self: AgentClient, session: str, message: str) -> AsyncIterator[str]:
            async for chunk in original(self, session, message):
                yield chunk
            store.mutate(ws, record, 0, lambda state: state.model_copy(update={"revision": 1}))

        monkeypatch.setattr(AgentClient, "deltas", changed)
        response = client.post(
            "/api/agent/query",
            json={
                "record_id": record,
                "question": "설명",
                "expected_revision": 0,
            },
        )
        last = json.loads(response.text.splitlines()[-1])
        assert last["event"] == "turn.completed"
        assert last["stale"] is True
        assert last["revision"] == 0
