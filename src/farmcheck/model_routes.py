# Copyright (c) 2026 FarmCheck contributors.
"""Version-bound external inference, separated from local manual operations."""

import secrets
from collections.abc import Callable
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from farmcheck.contracts import ModelComparison
from farmcheck.cropclient import CropClient, PredictionFailure, PredictionSuccess
from farmcheck.review import RevisionInput
from farmcheck.service import Service, View, json_object
from farmcheck.store import DomainError, Event, ModelReceipt, RecordState, timestamp


def classify_record(service: Service, ws: str, record_id: str, payload: RevisionInput) -> View:
    """Bind a request and adopt its result only if both revisions still match."""
    _ = service.source(record_id)
    if service.mode == "synthetic":
        code = "not_available_in_synthetic"
        raise DomainError(code, "합성 그림에는 실제 이미지 모델을 호출하지 않습니다.", 503)
    request_id = secrets.token_urlsafe(18)

    def begin(state: RecordState) -> RecordState:
        return state.model_copy(update={
            "revision": state.revision + 1, "pending_model_request": request_id,
        })

    started = service.store.mutate(ws, record_id, payload.expected_revision, begin)
    body, _ = service.catalog.image_bytes(record_id)
    prediction = CropClient().predict(body, "photo.jpg")
    service.store.save_model_receipt(ws, ModelReceipt(
        request_id=request_id, record_id=record_id,
        based_on_revision=started.revision, based_on_data_revision=started.data_revision,
        created_at=timestamp(), result=json_object(prediction),
    ))

    def adopt(state: RecordState) -> RecordState:
        if (
            state.pending_model_request != request_id
            or state.data_revision != started.data_revision
        ):
            code = "stale_model_response"
            raise DomainError(code, "기록이 바뀌어 이전 모델 결과를 적용하지 않았습니다.", 409)
        match prediction:
            case PredictionSuccess():
                comparison = ModelComparison(
                    status="available", run_id=prediction.run_id,
                    crop=prediction.crop.name_ko,
                    growth_week=prediction.growth_week.class_index,
                    confidence=Decimal(str(prediction.crop.confidence)),
                )
            case PredictionFailure():
                failed = Event(
                    action="classify_failed", actor="crop_model", created_at=timestamp(),
                    revision=state.revision + 1, data_revision=state.data_revision,
                    details=json_object(prediction),
                )
                return state.model_copy(update={
                    "revision": failed.revision, "pending_model_request": None,
                    "model_attempt": json_object(prediction),
                    "history": (*state.history, failed),
                })
        event = Event(
            action="classify", actor="crop_model", created_at=timestamp(),
            revision=state.revision + 1, data_revision=state.data_revision + 1,
            details=json_object(prediction),
        )
        return state.model_copy(update={
            "revision": event.revision, "data_revision": event.data_revision,
            "model": json_object(comparison), "pending_model_request": None,
            "model_attempt": json_object(prediction),
            "review_state": "needs_review", "resolution_state": "open",
            "assessment": None, "assessed_data_revision": None,
            "evidence_note": "", "evidence_refs": (),
            "history": (*state.history, event),
        })

    _ = service.store.mutate(ws, record_id, started.revision, adopt)
    return service.detail(record_id, service.store.read(ws))


def register_model_routes(
    router: APIRouter, service: Service, workspace: Callable[[Request], str],
) -> None:
    """Expose the sole allowed image inference destination."""
    @router.post("/api/records/{record_id}/classify")
    def classify(
        record_id: str, payload: RevisionInput, ws: Annotated[str, Depends(workspace)],
    ) -> View:
        return classify_record(service, ws, record_id, payload)

    @router.get("/api/records/{record_id}/model-runs")
    def model_runs(record_id: str, ws: Annotated[str, Depends(workspace)]) -> View:
        _ = service.source(record_id)
        return View({
            "runs": [
                json_object(receipt) for receipt in service.store.model_receipts(ws, record_id)
            ],
            "note": "외부 응답 보관본입니다. 현재 버전에 채택되었다는 뜻은 아닙니다.",
        })
