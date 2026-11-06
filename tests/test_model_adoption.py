# Copyright (c) 2026 FarmCheck contributors.
"""Deterministic late-response protection and manual state preservation."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from farmcheck.cropclient import CropClient, PredictionFailure
from farmcheck.model_routes import classify_record
from farmcheck.review import CorrectionInput, ReviewInput, RevisionInput, correct, review
from farmcheck.service import Service
from farmcheck.source import load_catalog
from farmcheck.store import DomainError, Store


def test_late_model_reply_preserved_but_not_adopted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A correction while inference is pending wins without losing its reply receipt."""
    catalog = load_catalog("synthetic")
    record_id = next(iter(catalog.records))
    store = Store(tmp_path / "late.sqlite")
    service = Service(catalog, store, "green_tea")
    workspace = store.workspace(None)
    entered = Event()
    release = Event()

    def delayed(_self: CropClient, _image: bytes, _filename: str) -> PredictionFailure:
        entered.set()
        assert release.wait(timeout=5)
        return PredictionFailure(run_id="late-injected-run", error="injected_timeout")

    monkeypatch.setattr(CropClient, "predict", delayed)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            classify_record, service, workspace, record_id, RevisionInput(expected_revision=0),
        )
        assert entered.wait(timeout=5)
        _ = store.mutate(
            workspace, record_id, 1,
            lambda state: correct(
                state,
                CorrectionInput(
                    expected_revision=1, field="environment.ph", value="7", reason="injected edit",
                ),
                "human",
            ),
        )
        release.set()
        with pytest.raises(DomainError, match="변경"):
            _ = future.result(timeout=5)
    state = store.record(workspace, record_id)
    assert state.revision == 2
    assert state.data_revision == 1
    assert state.model is None
    assert state.overlays["environment.ph"] == "7"
    receipts = store.model_receipts(workspace, record_id)
    assert len(receipts) == 1
    assert receipts[0].result["run_id"] == "late-injected-run"
    assert store.model_receipts(store.workspace(None), record_id) == ()


def test_model_failure_does_not_erase_human_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed external service is not a change to reviewed source evidence."""
    catalog = load_catalog("synthetic")
    record_id = next(iter(catalog.records))
    store = Store(tmp_path / "failure.sqlite")
    workspace = store.workspace(None)
    service = Service(catalog, store, "green_tea")
    _ = store.mutate(
        workspace, record_id, 0,
        lambda state: review(
            state, ReviewInput(expected_revision=0, action="reviewed"),
            "human", frozenset(),
        ),
    )

    def failed(_self: CropClient, _image: bytes, _filename: str) -> PredictionFailure:
        return PredictionFailure(run_id="injected-failure", error="timeout")

    monkeypatch.setattr(CropClient, "predict", failed)
    _ = classify_record(service, workspace, record_id, RevisionInput(expected_revision=1))
    state = store.record(workspace, record_id)
    assert state.review_state == "reviewed"
    assert state.data_revision == 0
    assert state.model_attempt is not None
    assert state.model_attempt["error"] == "timeout"
