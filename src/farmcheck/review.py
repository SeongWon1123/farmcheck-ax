# Copyright (c) 2026 FarmCheck contributors.
"""Human-only review transitions, independent of source and external services."""

from typing import Final, Literal

from pydantic import Field

from farmcheck.store import DomainError, Event, FrozenModel, RecordState, timestamp

CORRECTION_FIELDS: Final = frozenset({
    "environment.ph", "environment.ec", "environment.co2", "environment.temp",
    "environment.humidity", "growth.growth_cycle",
})


class RevisionInput(FrozenModel):
    """Require explicit optimistic concurrency for every record write."""

    expected_revision: int = Field(ge=0)


class CorrectionInput(RevisionInput):
    """A justified raw-value overlay, never a source edit."""

    field: str
    value: str = Field(max_length=200)
    reason: str = Field(min_length=1, max_length=2000)


class ReviewInput(RevisionInput):
    """Human intent separate from numeric correction."""

    action: Literal["reviewed", "hold", "resolve"]
    note: str = Field(default="", max_length=2000)
    next_action: str = Field(default="", max_length=2000)
    evidence_note: str = Field(default="", max_length=2000)
    evidence_refs: tuple[str, ...] = ()


def correct(state: RecordState, request: CorrectionInput, actor: str) -> RecordState:
    """Invalidate the assessment and prior resolution after an evidence change."""
    if request.field not in CORRECTION_FIELDS or not request.reason.strip():
        code = "invalid_correction"
        raise DomainError(code, "허용된 필드와 수정 이유를 입력하세요.")
    overlays = {**state.overlays, request.field: request.value}
    event = Event(
        action="correction",
        actor=actor,
        created_at=timestamp(),
        revision=state.revision + 1,
        data_revision=state.data_revision + 1,
        details={
            "field": request.field,
            "previous_overlay": state.overlays.get(request.field),
            "value": request.value,
            "reason": request.reason,
        },
    )
    return state.model_copy(update={
        "overlays": overlays,
        "revision": event.revision,
        "data_revision": event.data_revision,
        "review_state": "needs_review",
        "resolution_state": "open",
        "assessment": None,
        "assessed_data_revision": None,
        "pending_model_request": None,
        "evidence_note": "",
        "evidence_refs": (),
        "history": (*state.history, event),
    })


def review(
    state: RecordState,
    request: ReviewInput,
    actor: str,
    allowed_refs: frozenset[str],
) -> RecordState:
    """Record human judgment without treating review as resolution."""
    if not set(request.evidence_refs).issubset(allowed_refs):
        code = "unknown_evidence"
        raise DomainError(code, "현재 기록에 속하지 않는 근거입니다.")
    match request.action:
        case "reviewed":
            updated = state.model_copy(update={
                "review_state": "reviewed", "note": request.note,
            })
        case "hold":
            if not request.note.strip() or not request.next_action.strip():
                code = "hold_requires_question"
                raise DomainError(code, "보류 이유와 다음 확인 항목이 필요합니다.")
            updated = state.model_copy(update={
                "resolution_state": "held",
                "note": request.note,
                "next_action": request.next_action,
            })
        case "resolve":
            if state.assessed_data_revision != state.data_revision:
                code = "recheck_required"
                raise DomainError(code, "현재 버전에서 재검사를 먼저 실행하세요.")
            if not request.evidence_note.strip() or not request.evidence_refs:
                code = "evidence_required"
                raise DomainError(code, "해소 근거와 출처를 명시하세요.")
            updated = state.model_copy(update={
                "resolution_state": "resolved",
                "review_state": "reviewed",
                "evidence_note": request.evidence_note,
                "evidence_refs": request.evidence_refs,
            })
    event = Event(
        action=request.action,
        actor=actor,
        created_at=timestamp(),
        revision=state.revision + 1,
        data_revision=state.data_revision,
        details={
            "note": request.note,
            "next_action": request.next_action,
            "evidence_note": request.evidence_note,
            "evidence_refs": list(request.evidence_refs),
        },
    )
    return updated.model_copy(update={
        "revision": event.revision,
        "history": (*state.history, event),
    })
