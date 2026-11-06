# Copyright (c) 2026 FarmCheck contributors.
"""Human review mutations and explicit rechecks."""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from farmcheck.review import CorrectionInput, ReviewInput, RevisionInput, correct, review
from farmcheck.rules import RULE_VERSION
from farmcheck.service import Service, View, json_object
from farmcheck.store import Event, RecordState, timestamp


def register_workflow_routes(
    router: APIRouter, service: Service, workspace: Callable[[Request], str],
) -> None:
    """Record human intent while keeping source records immutable."""
    @router.post("/api/records/{record_id}/review")
    def review_record(
        record_id: str, payload: ReviewInput, ws: Annotated[str, Depends(workspace)],
    ) -> View:
        source = service.source(record_id)

        def transform(state: RecordState) -> RecordState:
            assessment = service.assessment(source, state)
            refs = frozenset(ref for check in assessment.checks for ref in check.evidence_refs)
            return review(state, payload, "workspace_user", refs)

        _ = service.store.mutate(ws, record_id, payload.expected_revision, transform)
        return service.detail(record_id, service.store.read(ws))

    @router.post("/api/records/{record_id}/corrections")
    def correction(
        record_id: str, payload: CorrectionInput, ws: Annotated[str, Depends(workspace)],
    ) -> View:
        _ = service.source(record_id)
        _ = service.store.mutate(
            ws, record_id, payload.expected_revision,
            lambda state: correct(state, payload, "workspace_user"),
        )
        return service.detail(record_id, service.store.read(ws))

    @router.post("/api/records/{record_id}/recheck")
    def recheck(
        record_id: str, payload: RevisionInput, ws: Annotated[str, Depends(workspace)],
    ) -> View:
        source = service.source(record_id)

        def transform(state: RecordState) -> RecordState:
            assessment = service.assessment(source, state)
            event = Event(
                action="recheck", actor="workspace_user", created_at=timestamp(),
                revision=state.revision + 1, data_revision=state.data_revision,
                details={"rule_version": RULE_VERSION},
            )
            return state.model_copy(update={
                "revision": event.revision, "assessment": json_object(assessment),
                "assessed_data_revision": state.data_revision, "history": (*state.history, event),
            })

        _ = service.store.mutate(ws, record_id, payload.expected_revision, transform)
        return service.detail(record_id, service.store.read(ws))
