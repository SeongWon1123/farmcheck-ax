# Copyright (c) 2026 FarmCheck contributors.
"""Server-selected model identities. Never accept a browser-supplied destination."""

import json
import os
from time import perf_counter
from typing import TYPE_CHECKING, cast

import httpx2

from farmcheck.store import timestamp

if TYPE_CHECKING:
    from pydantic import JsonValue

MAX_HEALTH_BYTES = 65536
HEALTH_TIMEOUT_SECONDS = 5
HTTP_OK = 200

_PROFILES = {
    "pilot": {
        "profile": "pilot",
        "name": "12팀 녹차 실험 모델",
        "version": "1",
        "uri": "models:/model-1c4cc046/1",
        "serving_id": "SV-9e5ca608",
    },
    "provided": {
        "profile": "provided",
        "name": "제공 작물·주차 분류 모델",
        "version": "provided",
        "uri": "serving:SV-c562094f",
        "serving_id": "SV-c562094f",
    },
}


def model_identity() -> dict[str, str]:
    """Fail closed on unknown profiles rather than turning configuration into SSRF."""
    profile = os.environ.get("FARM_MODEL_PROFILE", "provided")
    if profile not in _PROFILES:
        message = "FARM_MODEL_PROFILE must be provided or pilot"
        raise ValueError(message)
    return dict(_PROFILES[profile])


def model_base_url(identity: dict[str, str]) -> str:
    """Only allowlisted IDs can become a request destination."""
    if identity not in _PROFILES.values():
        message = "Unknown model identity"
        raise ValueError(message)
    return "https://aiopen-serving.jntp-ax.or.kr/jntp-cpu/" + identity["serving_id"]


def connection_status() -> dict[str, object]:
    """Perform a small health read, never sending user data or following redirects."""
    identity = model_identity()
    start = perf_counter()
    status = "unavailable"
    error = "health_unavailable"
    try:
        with (
            httpx2.Client(
                timeout=httpx2.Timeout(HEALTH_TIMEOUT_SECONDS),
                follow_redirects=False,
                trust_env=False,
            ) as client,
            client.stream("GET", model_base_url(identity) + "/health") as response,
        ):
            content = bytearray()
            for chunk in response.iter_bytes():
                if (
                    len(content) + len(chunk) > MAX_HEALTH_BYTES
                    or perf_counter() - start > HEALTH_TIMEOUT_SECONDS
                ):
                    error = "invalid_health"
                    break
                content.extend(chunk)
            else:
                if response.status_code == HTTP_OK:
                    try:
                        health = cast("JsonValue", json.loads(content))
                        if (
                            isinstance(health, dict)
                            and isinstance(health.get("status"), str)
                            and health.get("status") in {"ready", "ok"}
                            and perf_counter() - start <= HEALTH_TIMEOUT_SECONDS
                        ):
                            status = "ready"
                        else:
                            error = "invalid_health"
                    except (ValueError, RecursionError, UnicodeError):
                        error = "invalid_health"
    except httpx2.TimeoutException:
        error = "timeout"
    except httpx2.RequestError:
        error = "transport_error"
    result: dict[str, object] = {
        "enabled": True,
        "status": status,
        "model": identity,
        "latency_ms": round((perf_counter() - start) * 1000, 2),
        "checked_at": timestamp(),
    }
    if status != "ready":
        result["error"] = error
    return result
