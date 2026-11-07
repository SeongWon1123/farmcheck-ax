# Copyright (c) 2026 FarmCheck contributors.
"""Workspace-scoped record and original group retrieval."""

from collections import Counter
from collections.abc import Callable
from typing import Annotated, ClassVar, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from farmcheck.service import Service, View
from farmcheck.store import RecordState

type Selection = Literal["candidates", "all", "held", "needs_review", "resolved"]
type Scope = Literal["actual", "demo", "all"]


class RecordsQuery(BaseModel):
    """Parse list selection, scope and pagination at the HTTP boundary."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, populate_by_name=True)

    selection: Selection = Field(default="candidates", alias="filter")
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)
    q: str = Field(default="", max_length=200)
    scope: Scope = "all"


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
        query: Annotated[RecordsQuery, Query()],
    ) -> View:
        snapshot = service.store.read(ws)
        states = {state.record_id: state for state in snapshot.records}
        rows: list[View] = []
        counts: Counter[str] = Counter()
        for record in service.sources(ws).values():
            is_demo = record.origin in {"synthetic", "injected"}
            if (query.scope == "actual" and is_demo) or (query.scope == "demo" and not is_demo):
                continue
            state = states.get(record.id, RecordState(record_id=record.id))
            row = service.row(record, state)
            candidate = bool(row.root["candidate"])
            counts["total"] += 1
            counts["candidates"] += candidate
            counts[state.resolution_state] += 1
            counts[state.review_state] += 1
            matches = included(query.selection, state, candidate=candidate)
            if matches and query.q.casefold() in record.member_path.casefold():
                rows.append(row)
        start = (query.page - 1) * query.page_size
        return View({
            "items": [row.root for row in rows[start:start + query.page_size]],
            "page": query.page, "page_size": query.page_size, "total": len(rows),
            "summary": {
                key: counts[key]
                for key in ("total", "candidates", "held", "needs_review", "resolved")
            },
            "workspace_revision": snapshot.workspace_revision,
        })

    @router.get("/api/records/{record_id}")
    def detail(record_id: str, ws: Annotated[str, Depends(workspace)]) -> View:
        return service.detail(record_id, service.store.read(ws))

    @router.get("/api/records/{record_id}/image")
    def image(record_id: str, ws: Annotated[str, Depends(workspace)]) -> Response:
        _ = ws
        _ = service.source(record_id, ws)
        body, media = service.image_bytes(record_id, ws)
        return Response(body, media_type=media, headers={"Cache-Control": "private, no-store"})

    @router.get("/api/groups/{group_id}")
    def group(group_id: str, ws: Annotated[str, Depends(workspace)]) -> View:
        return service.group(group_id, service.store.read(ws))
