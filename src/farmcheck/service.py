# Copyright (c) 2026 FarmCheck contributors.
"""Compose immutable source evidence with one workspace's human workflow."""

from collections import Counter
from typing import Final

from pydantic import BaseModel, JsonValue, RootModel, TypeAdapter

from farmcheck.contracts import Assessment, ModelComparison, SourceRecord
from farmcheck.handover import CaptureInput, HandoverDocument, HandoverRecord
from farmcheck.normalize import time_difference
from farmcheck.review import CORRECTION_FIELDS
from farmcheck.rules import (
    GROUP_FIELDS,
    RULE_REGISTRY,
    RULE_VERSION,
    assess,
    group_identity,
    normalize_crop,
)
from farmcheck.source import Catalog, synthetic_model
from farmcheck.store import DomainError, RecordState, Snapshot, Store, WorkspaceState, timestamp

JSON_OBJECT: Final = TypeAdapter(dict[str, JsonValue])
MAX_PEERS: Final = 10
FIELD_LABELS: Final = {
    "environment.ph": "pH",
    "environment.ec": "EC",
    "environment.co2": "CO2",
    "environment.temp": "온도",
    "environment.humidity": "습도",
    "growth.growth_cycle": "주차",
}


class View(RootModel[dict[str, JsonValue]]):
    """A JSON presentation boundary containing validated serializable values."""


def json_object(model: BaseModel) -> dict[str, JsonValue]:
    """Serialize through the declared Pydantic JSON boundary without Any."""
    return JSON_OBJECT.validate_json(model.model_dump_json())


class Service:
    """Read source evidence and materialize consistent UI and handover views."""

    def __init__(self, catalog: Catalog, store: Store, mode: str) -> None:
        """Index original grouping once; corrections never alter that membership."""
        self.catalog: Catalog = catalog
        self.store: Store = store
        self.mode: str = mode
        self.groups: dict[str, list[str]] = {}
        for record in catalog.records.values():
            self.groups.setdefault(group_identity(record), []).append(record.id)

    def sources(self, workspace: str = "") -> dict[str, SourceRecord]:
        """Return a disposable combined view, never mutate the public catalog."""
        return {**self.catalog.records, **self.store.imported_sources(workspace)}

    def image_bytes(self, record_id: str, workspace: str) -> tuple[bytes, str]:
        """Resolve bytes only from the owning workspace or server catalog."""
        source = self.source(record_id, workspace)
        if source.origin == "uploaded":
            return self.store.imported_image(workspace, record_id), "image/jpeg"
        return self.catalog.image_bytes(record_id)

    def source(self, record_id: str, workspace: str = "") -> SourceRecord:
        """Only IDs belonging to the server-selected catalog are addressable."""
        record = self.sources(workspace).get(record_id)
        if record is None:
            code = "not_found"
            raise DomainError(code, "사진 기록을 찾을 수 없습니다.", 404)
        return record

    def assessment(self, record: SourceRecord, state: RecordState) -> Assessment:
        """Compare only explicitly adopted model evidence."""
        model = (
            synthetic_model(record)
            if state.model is None
            else ModelComparison.model_validate(state.model)
        )
        return assess(record, state.overlays, model)

    def summary(self, snapshot: WorkspaceState) -> View:
        """Count photo records and presentation groups separately."""
        states = {state.record_id: state for state in snapshot.records}
        counts: Counter[str] = Counter()
        candidates: set[str] = set()
        for record in self.sources(snapshot.workspace_id).values():
            state = states.get(record.id, RecordState(record_id=record.id))
            assessment = self.assessment(record, state)
            counts[state.resolution_state] += 1
            counts[state.review_state] += 1
            if assessment.candidate:
                counts["candidates"] += 1
                candidates.add(assessment.group_id)
        return View(
            {
                "total": len(self.sources(snapshot.workspace_id)),
                "candidates": counts["candidates"],
                "held": counts["held"],
                "needs_review": counts["needs_review"],
                "resolved": counts["resolved"],
                "baseline_records": self.catalog.inventory.ph_candidate_records,
                "baseline_groups": self.catalog.inventory.ph_candidate_groups,
                "current_candidate_groups": len(candidates),
            }
        )

    def row(self, record: SourceRecord, state: RecordState) -> View:
        """Present one list row with separate human and rule states."""
        assessment = self.assessment(record, state)
        week = assessment.effective_values["growth.growth_cycle"].decimal
        return View(
            {
                "id": record.id,
                "label": record.member_path.rsplit("/", 1)[-1],
                "crop": record.crop,
                "growth_week": None if week is None else str(week),
                "origin": record.origin,
                "scenario": record.scenario,
                "candidate": assessment.candidate,
                "matched_rules": [
                    check.rule_id for check in assessment.checks if check.outcome == "matched"
                ],
                "group_id": assessment.group_id,
                "review_state": state.review_state,
                "resolution_state": state.resolution_state,
                "revision": state.revision,
                "data_revision": state.data_revision,
                "env_date_raw": record.env_time.raw,
            }
        )

    def group(self, group_id: str, snapshot: WorkspaceState) -> View:
        """Expose all original member records without merging their judgments."""
        member_ids = [
            record.id
            for record in self.sources(snapshot.workspace_id).values()
            if group_identity(record) == group_id
        ]
        if not member_ids:
            code = "not_found"
            raise DomainError(code, "후보 묶음을 찾을 수 없습니다.", 404)
        states = {state.record_id: state for state in snapshot.records}
        first = self.source(member_ids[0], snapshot.workspace_id)
        key: dict[str, JsonValue] = {
            path: None if first.values[path].decimal is None else str(first.values[path].decimal)
            for path in GROUP_FIELDS
        }
        key["env_date"] = first.env_time.raw
        key["origin"] = first.origin
        key["units"] = {
            path: {
                "status": first.values[path].unit_status,
                "raw_unit": first.values[path].raw_unit,
                "source_ref": first.values[path].unit_source_ref,
            }
            for path in GROUP_FIELDS
        }
        members: list[JsonValue] = [
            self.row(
                self.source(identifier, snapshot.workspace_id),
                states.get(identifier, RecordState(record_id=identifier)),
            ).root
            for identifier in member_ids
        ]
        note = "원본 필드가 같은 사진 기록 묶음입니다. 독립 측정 횟수나 동일 식물은 아닙니다."
        return View(
            {
                "id": group_id,
                "size": len(member_ids),
                "key": key,
                "members": members,
                "note": note,
                "interpretation": note,
            }
        )

    def detail(self, record_id: str, snapshot: WorkspaceState) -> View:
        """Build a consistent detail view from a single workspace read."""
        record = self.source(record_id, snapshot.workspace_id)
        states = {state.record_id: state for state in snapshot.records}
        state = states.get(record_id, RecordState(record_id=record_id))
        assessment = self.assessment(record, state)
        values: list[JsonValue] = []
        for parsed in assessment.parsed_fields:
            original = record.values[parsed.field_path]
            raw = json_object(original)["raw"]
            values.append(
                {
                    "field_path": parsed.field_path,
                    "label": FIELD_LABELS.get(parsed.field_path, parsed.field_path),
                    "raw": raw,
                    "parsed": None if original.decimal is None else str(original.decimal),
                    "status": parsed.status,
                    "presence": original.presence,
                    "raw_unit": parsed.raw_unit,
                    "unit_status": parsed.unit_status,
                    "unit_source_ref": parsed.unit_source_ref,
                    "effective": None if parsed.decimal is None else str(parsed.decimal),
                    "effective_raw": json_object(parsed)["raw"],
                    "corrected": parsed.field_path in state.overlays,
                }
            )
        checks: list[JsonValue] = []
        reference_labels: dict[str, str] = {}
        for check, definition in zip(assessment.checks, RULE_REGISTRY, strict=True):
            checks.append(
                {
                    **json_object(check),
                    "fields": list(check.field_paths),
                    "not_concluded": definition.not_concluded,
                    "assumed_unit": definition.assumed_unit,
                }
            )
            reference_labels.update(
                {
                    ref: (
                        "모델 실행 · " + ref + " (주입 시연)"
                        if ref.startswith("model:injected:")
                        else "모델 실행 · " + ref
                        if ref.startswith("model:")
                        else ref.rsplit(":", 1)[-1] + " · 원본 출처"
                    )
                    for ref in check.evidence_refs
                }
            )
        delta = time_difference(record)
        model: dict[str, JsonValue] = {
            "status": "none",
            "message": "아직 실제 모델을 호출하지 않았습니다.",
        }
        adopted = (
            synthetic_model(record)
            if state.model is None
            else ModelComparison.model_validate(state.model)
        )
        if adopted is not None:
            model = {
                **json_object(adopted),
                "status": "ok" if adopted.status == "available" else adopted.status,
                **(
                    {"message": "오류 주입 시연입니다. 실제 모델 호출 결과가 아닙니다."}
                    if state.model is None else {}
                ),
            }
        if (
            state.model_attempt is not None
            and state.model_attempt.get("status") == "failed"
            and model["status"] != "ok"
        ):
            model = {
                **state.model_attempt,
                "message": "최근 모델 호출에 실패했습니다. 수동 판단은 그대로 유지됩니다.",
                "previous_adopted_result": state.model,
            }
        week = assessment.effective_values["growth.growth_cycle"].decimal
        crop = state.overlays.get("images.class_name", record.crop)
        comparable = adopted if adopted is not None and adopted.status == "available" else None
        model_week = None if comparable is None else comparable.growth_week
        model_crop = None if comparable is None else comparable.crop
        week_matches = None if week is None or model_week is None else week == model_week
        crop_matches = (
            None if model_crop is None or not normalize_crop(crop)
            else normalize_crop(crop) == normalize_crop(model_crop)
        )
        comparison: dict[str, JsonValue] = {
            "status": (
                "unavailable" if week_matches is None or crop_matches is None
                else "equal" if week_matches and crop_matches else "different"
            ),
            "current_week": None if week is None else str(week),
            "model_week": None if model_week is None else str(model_week),
            "week_matches": week_matches,
            "crop_matches": crop_matches,
        }
        peers: list[JsonValue] = []
        units = tuple((value.unit_status, value.raw_unit) for value in assessment.parsed_fields)
        for other in sorted(
            self.sources(snapshot.workspace_id).values(),
            key=lambda item: (item.photo_time.raw or "", item.id),
        ):
            if other.id == record_id:
                continue
            other_state = states.get(other.id, RecordState(record_id=other.id))
            other_crop = other_state.overlays.get("images.class_name", other.crop)
            if normalize_crop(other_crop) != normalize_crop(crop):
                continue
            other_assessment = self.assessment(other, other_state)
            other_week = other_assessment.effective_values["growth.growth_cycle"].decimal
            other_units = tuple(
                (value.unit_status, value.raw_unit) for value in other_assessment.parsed_fields
            )
            if week is not None and other_week == week and other_units == units:
                peer_values: dict[str, JsonValue] = {
                    path: (
                        None
                        if other_assessment.effective_values[path].decimal is None
                        else str(other_assessment.effective_values[path].decimal)
                    )
                    for path in GROUP_FIELDS
                }
                peers.append(
                    {
                        "id": other.id,
                        "crop": other_crop,
                        "growth_week": str(other_week),
                        "timestamp": other.photo_time.raw,
                        "values": peer_values,
                        "unit_status": (
                            "unknown"
                            if any(
                                other_assessment.effective_values[path].unit_status == "unknown"
                                for path in GROUP_FIELDS
                            )
                            else "source_declared"
                        ),
                        "origin": other.origin,
                        "source_group_id": other.source_group_id,
                    }
                )
                if len(peers) >= MAX_PEERS:
                    break
        history: list[JsonValue] = [
            {
                **json_object(event),
                "at": event.created_at,
                "type": event.action,
                "summary": str(event.details),
            }
            for event in state.history
        ]
        return View(
            {
                **self.row(record, state).root,
                "workspace_revision": snapshot.workspace_revision,
                "source": {
                    "dataset_id": record.dataset_id,
                    "member_path": record.member_path,
                    "raw_hash": record.raw_hash,
                    "source_group_id": record.source_group_id,
                    "raw_json": record.raw_json,
                    "synthetic": record.origin != "uploaded" and self.catalog.zip_path is None,
                    **(
                        self.store.import_metadata(snapshot.workspace_id, record_id)
                        if record.origin == "uploaded"
                        else {"origin": record.origin}
                    ),
                    "image": {
                        "available": True,
                        "actual_width": record.actual_dimensions.width,
                        "actual_height": record.actual_dimensions.height,
                        "declared_width": record.declared_dimensions.width,
                        "declared_height": record.declared_dimensions.height,
                    },
                },
                "values": values,
                "checks": checks,
                "time": {
                    "photo_raw": record.photo_time.raw,
                    "photo_parsed": (
                        None
                        if record.photo_time.parsed is None
                        else record.photo_time.parsed.isoformat()
                    ),
                    "env_raw": record.env_time.raw,
                    "env_parsed": (
                        None
                        if record.env_time.parsed is None
                        else record.env_time.parsed.isoformat()
                    ),
                    "timezone_status": "unknown",
                    "delta_seconds": delta.seconds,
                    "delta_label": delta.label,
                    "reason": delta.reason,
                },
                "group": self.group(assessment.group_id, snapshot).root,
                "peers": peers,
                "peers_note": (
                    "동일 작물·주차·단위의 비교 기록입니다. "
                    "정상 범위나 성장 추세가 아닙니다."
                ),
                "review": {
                    "review_state": state.review_state,
                    "resolution_state": state.resolution_state,
                    "hold": {"reason": state.hold_reason, "next_action": state.next_action},
                    "resolution": {
                        "evidence_note": state.evidence_note,
                        "evidence_refs": list(state.evidence_refs),
                    },
                    "recheck_needed": state.assessed_data_revision != state.data_revision,
                    "last_recheck_data_revision": state.assessed_data_revision,
                },
                "model": model,
                "model_attempt": state.model_attempt,
                "comparison": comparison,
                "correctable_fields": [
                    {"field": field, "label": FIELD_LABELS.get(field, field)}
                    for field in sorted(CORRECTION_FIELDS)
                ],
                "evidence_candidates": [
                    {
                        "ref": ref,
                        "label": label,
                        "kind": "model" if ref.startswith("model:") else "source",
                        "origin": (
                            "injected"
                            if ref.startswith("model:injected:")
                            else "actual"
                            if ref.startswith("model:")
                            else record.origin
                        ),
                    }
                    for ref, label in reference_labels.items()
                ],
                "history": history,
            }
        )

    def handover(self, workspace: str, request: CaptureInput) -> Snapshot:
        """Capture selected records or candidates plus every touched record."""

        def materialize(snapshot: WorkspaceState) -> str:
            states = {state.record_id: state for state in snapshot.records}
            selected = request.record_ids
            if selected is None:
                selected = tuple(
                    record.id
                    for record in self.sources(snapshot.workspace_id).values()
                    if record.id in states
                    or self.assessment(
                        record,
                        RecordState(record_id=record.id),
                    ).candidate
                )
            records: list[HandoverRecord] = []
            for identifier in dict.fromkeys(selected):
                source = self.source(identifier, snapshot.workspace_id)
                state = states.get(identifier, RecordState(record_id=identifier))
                assessment = self.assessment(source, state)
                question = (
                    state.next_action
                    or " ".join(
                        check.next_question
                        for check in assessment.checks
                        if check.outcome == "matched"
                    )
                    or "원본 및 검토 상태를 다음 담당자가 확인하세요."
                )
                records.append(
                    HandoverRecord(
                        record_id=identifier,
                        origin=source.origin,
                        revision=state.revision,
                        data_revision=state.data_revision,
                        review_state=state.review_state,
                        resolution_state=state.resolution_state,
                        next_action=question,
                        detail=self.detail(identifier, snapshot).root,
                    )
                )
            return HandoverDocument(
                workspace_revision=snapshot.workspace_revision,
                rule_version=RULE_VERSION,
                created_at=timestamp(),
                mode=self.mode,
                scope_ids=tuple(record.record_id for record in records),
                scope_description=(
                    "선택한 사진 기록"
                    if request.record_ids is not None
                    else "현재 후보와 처리 이력이 있는 사진 기록"
                ),
                records=tuple(records),
                unresolved_count=sum(record.resolution_state != "resolved" for record in records),
            ).model_dump_json()

        return self.store.capture(
            workspace,
            request.expected_workspace_revision,
            "handover",
            materialize,
        )

    def handover_view(self, snapshot: Snapshot, current_revision: int) -> View:
        """Add current staleness outside the immutable historical payload."""
        document = HandoverDocument.model_validate_json(snapshot.payload)
        counts = Counter(record.resolution_state for record in document.records)
        return View(
            {
                "id": snapshot.id,
                "created_at": snapshot.created_at,
                "workspace_revision": snapshot.workspace_revision,
                "sha256": snapshot.sha256,
                "rule_version": document.rule_version,
                "superseded_by_newer_revision": current_revision != snapshot.workspace_revision,
                "scope": {
                    "description": document.scope_description,
                    "record_ids": list(document.scope_ids),
                    "count": len(document.scope_ids),
                },
                "counts": {
                    "open": counts["open"],
                    "held": counts["held"],
                    "resolved": counts["resolved"],
                    "needs_review": sum(
                        record.review_state == "needs_review" for record in document.records
                    ),
                },
                "questions": [
                    {
                        "record_id": record.record_id,
                        "state": record.resolution_state,
                        "question": record.next_action,
                        "next_action": record.next_action,
                    }
                    for record in document.records
                    if record.resolution_state != "resolved"
                ],
            }
        )
