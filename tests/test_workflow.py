# Copyright (c) 2026 FarmCheck contributors.
"""Regression tests for human review, transactions and immutable snapshots."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from pydantic import ValidationError

from farmcheck.agent_boundary import AgentContextVersion, AgentOutput, validate_output
from farmcheck.handover import HandoverDocument, export_snapshot
from farmcheck.review import CorrectionInput, ReviewInput, correct, review
from farmcheck.store import DomainError, RecordState, Store, WorkspaceState


def test_hold_review_correction_are_distinct() -> None:
    """Review does not resolve a hold; correction invalidates prior judgment."""
    held = review(
        RecordState(record_id="tea"),
        ReviewInput(
            expected_revision=0, action="hold", note="원문 확인", next_action="담당자 질문",
        ),
        "human", frozenset(),
    )
    checked = review(
        held, ReviewInput(expected_revision=1, action="reviewed"), "human", frozenset(),
    )
    assert checked.resolution_state == "held"
    assert checked.review_state == "reviewed"
    assert checked.hold_reason == "원문 확인"
    changed = correct(
        checked,
        CorrectionInput(expected_revision=2, field="environment.ph", value="7", reason="입력 확인"),
        "human",
    )
    assert changed.review_state == "needs_review"
    assert changed.resolution_state == "open"
    assert changed.next_action == "담당자 질문"
    assert changed.data_revision == 1
    assert changed.assessed_data_revision is None
    assert len(changed.history) == 3
    with pytest.raises(DomainError, match="재검사"):
        _ = review(
            changed,
            ReviewInput(
                expected_revision=3, action="resolve",
                evidence_note="원문 확인", evidence_refs=("source:tea",),
            ),
            "human", frozenset({"source:tea"}),
        )


def test_invalid_hold_and_evidence_are_rejected() -> None:
    """A blank reason and invented references cannot close review."""
    state = RecordState(record_id="tea", assessed_data_revision=0)
    with pytest.raises(DomainError, match="보류"):
        _ = review(
            state, ReviewInput(expected_revision=0, action="hold"),
            "human", frozenset(),
        )
    with pytest.raises(DomainError, match="근거"):
        _ = review(
            state,
            ReviewInput(expected_revision=0, action="resolve", evidence_refs=("invented",)),
            "human", frozenset(),
        )


def test_exactly_one_concurrent_write_wins(tmp_path: Path) -> None:
    """A barrier releases both same-version writers; no timing sleeps."""
    store = Store(tmp_path / "state.sqlite")
    workspace = store.workspace(None)
    barrier = Barrier(2)

    def write_once() -> int:
        _ = barrier.wait(timeout=5)
        try:
            _ = store.mutate(
                workspace, "tea", 0,
                lambda state: review(
                    state, ReviewInput(expected_revision=0, action="reviewed"),
                    "human", frozenset(),
                ),
            )
        except DomainError as error:
            return error.status
        else:
            return 200

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (pool.submit(write_once), pool.submit(write_once))
        results = tuple(future.result(timeout=10) for future in futures)
    assert sorted(results) == [200, 409]
    assert store.read(workspace).workspace_revision == 1
    assert len(store.record(workspace, "tea").history) == 1


def test_snapshot_is_immutable_and_workspace_isolated(tmp_path: Path) -> None:
    """Editing later does not change old downloads or expose another workspace."""
    store = Store(tmp_path / "state.sqlite")
    workspace = store.workspace(None)
    other = store.workspace(None)

    def materialize(state: WorkspaceState) -> str:
        return HandoverDocument(
            workspace_revision=state.workspace_revision,
            rule_version="1", created_at="2026-11-06T00:00:00Z",
            mode="synthetic", scope_ids=(), scope_description="empty test scope",
            records=(), unresolved_count=0,
        ).model_dump_json()

    snapshot = store.capture(workspace, 0, "handover", materialize)
    before = export_snapshot(snapshot, "json")
    _ = store.mutate(
        workspace, "tea", 0,
        lambda state: review(
            state, ReviewInput(expected_revision=0, action="reviewed"), "human", frozenset(),
        ),
    )
    after = export_snapshot(store.snapshot(workspace, snapshot.id, "handover"), "json")
    assert before == after
    assert store.snapshot(workspace, snapshot.id, "handover").sha256 == snapshot.sha256
    with pytest.raises(DomainError, match="찾을"):
        _ = store.snapshot(other, snapshot.id, "handover")
    with pytest.raises(DomainError, match="변경"):
        _ = store.capture(workspace, 0, "handover", materialize)


def test_agent_boundary_rejects_stale_unknown_and_mutations() -> None:
    """A future explanation has no mutation authority or invented evidence."""
    context = AgentContextVersion(
        snapshot_id="s", workspace_revision=1, evidence_refs=frozenset({"source:tea"}),
    )
    answer = AgentOutput(
        snapshot_id="s", answer="확인 필요", evidence_refs=("source:tea",),
        questions=("원문 확인?",), next_actions=("담당자에게 확인",),
    )
    assert validate_output(answer, context, 1) == answer
    with pytest.raises(DomainError, match="이전 설명"):
        _ = validate_output(answer, context, 2)
    with pytest.raises(DomainError, match="존재하지"):
        _ = validate_output(answer.model_copy(update={"evidence_refs": ("fake",)}), context, 1)
    with pytest.raises(ValidationError):
        _ = AgentOutput.model_validate({
            **answer.model_dump(), "resolve_record": "tea",
        })
