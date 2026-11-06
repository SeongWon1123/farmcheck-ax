# Copyright (c) 2026 FarmCheck contributors.
"""Disabled live provider and testable read-only response boundary."""

from pydantic import Field

from farmcheck.store import DomainError, FrozenModel


class AgentStatus(FrozenModel):
    """Truthful provider state for today's manual workflow."""

    status: str = "disabled_by_owner"
    message: str = "AgentStream은 내일 연동 예정입니다. 수동 검토와 인수인계는 사용 가능합니다."
    live: bool = False


class AgentOutput(FrozenModel):
    """Future output contains explanations, never mutation commands."""

    snapshot_id: str
    answer: str = Field(max_length=10000)
    evidence_refs: tuple[str, ...]
    questions: tuple[str, ...]
    next_actions: tuple[str, ...]


class AgentContextVersion(FrozenModel):
    """The exact workspace and evidence scope provided to a future reader."""

    snapshot_id: str
    workspace_revision: int
    evidence_refs: frozenset[str]


def validate_output(
    response: AgentOutput,
    context: AgentContextVersion,
    current_workspace_revision: int,
) -> AgentOutput:
    """Reject stale or fabricated references without granting any write authority."""
    if response.snapshot_id != context.snapshot_id:
        code = "unknown_snapshot"
        raise DomainError(code, "설명의 스냅샷이 일치하지 않습니다.")
    if current_workspace_revision != context.workspace_revision:
        code = "stale_context"
        raise DomainError(code, "기록이 변경되어 이전 설명을 사용할 수 없습니다.", 409)
    if not set(response.evidence_refs).issubset(context.evidence_refs):
        code = "unknown_evidence"
        raise DomainError(code, "존재하지 않는 출처가 포함되었습니다.")
    return response
