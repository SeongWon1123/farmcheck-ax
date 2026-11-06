# Copyright (c) 2026 FarmCheck contributors.
"""Immutable handover and explicitly disabled agent HTTP boundaries."""

from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response

from farmcheck.agent_boundary import AgentStatus
from farmcheck.handover import CaptureInput, export_snapshot
from farmcheck.rules import RULE_VERSION
from farmcheck.service import JSON_OBJECT, Service, View, json_object
from farmcheck.store import DomainError, FrozenModel


class ContextInput(FrozenModel):
    """Optional record selection for a read-only context."""

    record_ids: tuple[str, ...] | None = None


def register_snapshot_routes(
    router: APIRouter, service: Service, workspace: Callable[[Request], str],
) -> None:
    """Serve only workspace-owned materialized handover bytes."""
    store = service.store

    @router.post("/api/handovers")
    def capture(payload: CaptureInput, ws: Annotated[str, Depends(workspace)]) -> View:
        snapshot = service.handover(ws, payload)
        return service.handover_view(snapshot, store.read(ws).workspace_revision)

    @router.get("/api/handovers/{snapshot_id}")
    def handover(snapshot_id: str, ws: Annotated[str, Depends(workspace)]) -> View:
        return service.handover_view(
            store.snapshot(ws, snapshot_id, "handover"), store.read(ws).workspace_revision,
        )

    @router.get("/api/handovers/{snapshot_id}/download")
    def download(
        snapshot_id: str, ws: Annotated[str, Depends(workspace)],
        format_name: Annotated[Literal["json", "csv", "html"], Query(alias="format")] = "json",
    ) -> Response:
        exported = export_snapshot(store.snapshot(ws, snapshot_id, "handover"), format_name)
        filename = f"farmcheck-{snapshot_id}.{exported.extension}"
        return Response(
            exported.body, media_type=exported.media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "private, no-store",
            },
        )

    @router.get("/api/agent/status")
    def agent_status(ws: Annotated[str, Depends(workspace)]) -> AgentStatus:
        _ = ws
        return AgentStatus()

    @router.post("/api/agent/context")
    def agent_context(payload: ContextInput, ws: Annotated[str, Depends(workspace)]) -> View:
        state = store.read(ws)
        snapshot = service.handover(ws, CaptureInput(
            expected_workspace_revision=state.workspace_revision, record_ids=payload.record_ids,
        ))
        return View({
            "context_id": snapshot.id, "snapshot_id": snapshot.id,
            "created_at": snapshot.created_at, "workspace_revision": snapshot.workspace_revision,
            "rule_version": RULE_VERSION, "read_only": True,
            "agent_status": json_object(AgentStatus()),
            "context": JSON_OBJECT.validate_json(snapshot.payload),
        })

    @router.post("/api/agent/query")
    def agent_query(ws: Annotated[str, Depends(workspace)]) -> View:
        _ = ws
        code = "disabled_by_owner"
        raise DomainError(code, AgentStatus().message, 503)
