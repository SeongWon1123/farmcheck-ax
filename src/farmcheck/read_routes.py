# Copyright (c) 2026 FarmCheck contributors.
"""Workspace-scoped record and original group retrieval."""

from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response

from farmcheck.service import Service, View
from farmcheck.store import RecordState

type Selection = Literal["candidates", "all", "held", "needs_review", "resolved"]


def included(selection: Selection, state: RecordState, *, candidate: bool) -> bool:
    """Apply a list filter without conflating review and resolution."""
    match selection:
        case "all":
            return True
        case "candidates":
            return candidate
        case "held" | "resolved":
            return state.resolution_state == selection
        case "needs_review":
            return state.review_state == "needs_review"


def register_read_routes(
    router: APIRouter, service: Service, workspace: Callable[[Request], str],
) -> None:
    """Register source views and independently reviewed group members."""
    @router.get("/api/records")
    def records(
        ws: Annotated[str, Depends(workspace)],
        selection: Annotated[
            Selection, Query(alias="filter"),
        ] = "candidates",
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 25,
        q: Annotated[str, Query(max_length=200)] = "",
    ) -> View:
        snapshot = service.store.read(ws)
        states = {state.record_id: state for state in snapshot.records}
        rows: list[View] = []
        for record in service.catalog.records.values():
            state = states.get(record.id, RecordState(record_id=record.id))
            row = service.row(record, state)
            matches = included(selection, state, candidate=bool(row.root["candidate"]))
            if matches and q.casefold() in record.member_path.casefold():
                rows.append(row)
        start = (page - 1) * page_size
        return View({
            "items": [row.root for row in rows[start:start + page_size]],
            "page": page, "page_size": page_size, "total": len(rows),
            "workspace_revision": snapshot.workspace_revision,
        })

    @router.get("/api/records/{record_id}")
    def detail(record_id: str, ws: Annotated[str, Depends(workspace)]) -> View:
        return service.detail(record_id, service.store.read(ws))

    @router.get("/api/records/{record_id}/image")
    def image(record_id: str, ws: Annotated[str, Depends(workspace)]) -> Response:
        _ = ws
        _ = service.source(record_id)
        body, media = service.catalog.image_bytes(record_id)
        return Response(body, media_type=media, headers={"Cache-Control": "private, no-store"})

    @router.get("/api/groups/{group_id}")
    def group(group_id: str, ws: Annotated[str, Depends(workspace)]) -> View:
        return service.group(group_id, service.store.read(ws))
