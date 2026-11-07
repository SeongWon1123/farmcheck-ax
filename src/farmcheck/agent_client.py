# Copyright (c) 2026 FarmCheck contributors.
"""Server-only AgentStream configuration and bounded NDJSON transport."""

import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from uuid import UUID

import httpx2
from pydantic import JsonValue

from farmcheck.service import JSON_OBJECT
from farmcheck.store import DomainError

BASE_URL = "https://api-gateway.jntp-ax.or.kr/agentstream"
MAX_STREAM_BYTES = 4 * 1024 * 1024
MAX_SESSION_BYTES = 16384
MAX_SESSION_ID = 200


@dataclass(frozen=True)
class AgentConfig:
    """Never include the secret in representations, responses, or logs."""

    api_key: str = field(repr=False)
    chatbot_id: str
    base_url: str = BASE_URL


def agent_config() -> AgentConfig | None:
    """Read environment or the explicitly configured server-only secret file."""
    path = Path(
        os.environ.get(
            "FARM_AGENT_CONFIG",
            str(Path(__file__).resolve().parents[3] / "run" / "agentstream.json"),
        )
    )
    values: dict[str, str] = {}
    try:
        if path.exists():
            if path.stat().st_mode & 0o077:
                return None
            loaded = JSON_OBJECT.validate_json(path.read_bytes())
            values = {key: value for key, value in loaded.items() if isinstance(value, str)}
        key = os.environ.get("AGENTSTREAM_API_KEY") or values.get("AGENTSTREAM_API_KEY", "")
        chatbot = (
            os.environ.get("AGENTSTREAM_CHATBOT_ID")
            or os.environ.get("CHATBOT_ID")
            or values.get("CHATBOT_ID", "")
        )
        base = (
            os.environ.get("AGENTSTREAM_BASE_URL")
            or os.environ.get("BASE_URL")
            or values.get("BASE_URL", BASE_URL)
        ).rstrip("/")
        if not key or not chatbot or base != BASE_URL:
            return None
        return AgentConfig(key, str(UUID(chatbot)), base)
    except (OSError, ValueError):
        return None


def provider_error(status: int) -> DomainError:
    """Do not forward provider bodies, credentials, or session identifiers."""
    if status in {401, 403}:
        return DomainError("agent_auth", "챗봇 API 키와 호출 권한을 서버에서 확인해 주세요.", 503)
    if status == HTTPStatus.TOO_MANY_REQUESTS:
        return DomainError("agent_busy", "챗봇 사용량이 많습니다. 잠시 후 다시 질문해 주세요.", 429)
    return DomainError(
        "agent_unavailable", "챗봇에 연결하지 못했습니다. 잠시 후 다시 시도하세요.", 502
    )


class AgentClient:
    """Use only X-API-Key; redirects, proxy environment and cookies are excluded."""

    def __init__(self, config: AgentConfig) -> None:
        """Create an isolated HTTP client with no browser authentication."""
        self.config: AgentConfig = config
        self.http: httpx2.AsyncClient = httpx2.AsyncClient(
            headers={"X-API-Key": config.api_key},
            timeout=httpx2.Timeout(connect=5, read=40, write=10, pool=5),
            follow_redirects=False,
            trust_env=False,
        )

    async def close(self) -> None:
        """Release sockets even after cancellation or a malformed response."""
        await self.http.aclose()

    async def health(self) -> None:
        """Check the configured chatbot without creating a conversation."""
        self.http.cookies.clear()
        async with self.http.stream(
            "GET",
            f"{self.config.base_url}/open/v1/chatbots/{self.config.chatbot_id}/health",
        ) as response:
            if response.status_code != HTTPStatus.OK:
                raise provider_error(response.status_code)

    async def create_session(self) -> str:
        """Create an opaque server-held session; this does not generate an answer."""
        self.http.cookies.clear()
        async with self.http.stream(
            "POST",
            f"{self.config.base_url}/open/v1/chatbots/{self.config.chatbot_id}/sessions",
            json={"query": "선택한 농업 기록의 근거를 확인하는 읽기 전용 검수 대화입니다."},
        ) as response:
            if response.status_code not in {200, 201}:
                raise provider_error(response.status_code)
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_SESSION_BYTES:
                    raise provider_error(502)
            value = JSON_OBJECT.validate_json(bytes(body)).get("session_id")
            if not isinstance(value, str) or not value or len(value) > MAX_SESSION_ID:
                raise provider_error(502)
            # IDs are opaque, but must not be able to change the request path.
            if not all(c.isalnum() or c in "_-" for c in value):
                raise provider_error(502)
            return value

    async def deltas(self, session_id: str, message: str) -> AsyncIterator[str]:
        """Forward assistant deltas only; require an explicit completed turn."""
        self.http.cookies.clear()
        async with self.http.stream(
            "POST",
            f"{self.config.base_url}/open/v1/chat-sessions/{session_id}/messages",
            json={"message": message},
        ) as response:
            if response.status_code != HTTPStatus.OK:
                raise provider_error(response.status_code)
            async for event in stream_events(response):
                kind = event.get("event") or event.get("type")
                if kind == "assistant.delta":
                    content = event.get("content")
                    if not isinstance(content, str):
                        raise provider_error(502)
                    yield content
                elif kind == "turn.completed":
                    return
                elif kind in {"error", "turn.failed", "turn.error", "turn.cancelled"}:
                    raise provider_error(502)
            code = "agent_incomplete"
            raise DomainError(code, "답변이 중단되었습니다. 새 대화로 다시 질문해 주세요.", 502)


async def stream_events(response: httpx2.Response) -> AsyncIterator[dict[str, JsonValue]]:
    """Bound raw bytes and parse complete UTF-8 lines, including a final EOF line."""
    pending = bytearray()
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > MAX_STREAM_BYTES:
            raise provider_error(502)
        pending.extend(chunk)
        while b"\n" in pending:
            raw, _, rest = pending.partition(b"\n")
            pending = bytearray(rest)
            if raw.strip():
                yield JSON_OBJECT.validate_json(bytes(raw))
    if pending.strip():
        yield JSON_OBJECT.validate_json(bytes(pending))


def event_line(kind: str, **data: object) -> str:
    """Emit the application's small, explicit stream contract."""
    return json.dumps({"event": kind, **data}, ensure_ascii=False) + "\n"
