# Copyright (c) 2026 FarmCheck contributors.
"""Immutable, serializable contracts for source evidence and rule assessments."""

import json
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, computed_field

type RawValue = str | int | Decimal | bool | list[RawValue] | dict[str, RawValue] | None
type Origin = Literal["actual", "injected", "recorded", "synthetic", "uploaded"]
type ParseStatus = Literal["missing", "blank", "dash", "invalid", "zero", "number"]
type Presence = Literal["absent", "null", "present"]
type UnitStatus = Literal["unknown", "source_declared"]
JSON_DECODER: Final[Callable[[str], RawValue]] = json.JSONDecoder(parse_float=Decimal).decode


def decode_raw(
    text: str, *, decoder: Callable[[str], RawValue] = JSON_DECODER
) -> RawValue:
    """Decode decimal tokens without passing through binary floating point."""
    return decoder(text)


class FrozenModel(BaseModel):
    """Reject field replacement and unexpected input fields."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class Dimensions(FrozenModel):
    """Image width and height, without coordinate transformation claims."""

    width: int
    height: int


class ParsedValue(FrozenModel):
    """One exact numeric interpretation with its unchanged source token."""

    field_path: str
    raw: str | int | bool | None
    presence: Presence
    status: ParseStatus
    decimal: Decimal | None = None
    raw_unit: str | None = None
    unit_source_ref: str | None = None
    unit_status: UnitStatus = "unknown"


class ParsedTime(FrozenModel):
    """A local-clock string, explicitly without an inferred timezone."""

    raw: str | None
    parsed: datetime | None
    status: Literal["parsed", "missing", "invalid"]
    timezone_status: Literal["unknown"] = "unknown"
    basis: Literal["source_local_clock", "unknown"] = "unknown"


class TimeDifference(FrozenModel):
    """Signed environment minus photo wall-clock seconds, not elapsed UTC time."""

    seconds: int | None
    label: str = "기록된 시각 차이·시간대 미확인"
    reason: str | None = None


class SourceReference(FrozenModel):
    """Locate one field and retain the shared original-source identity."""

    record_id: str
    member_path: str
    raw_hash: str
    field_path: str
    source_group_id: str
    origin: Origin


class SourceRecord(FrozenModel):
    """Own immutable source text; decoded dictionaries are disposable copies."""

    id: str
    origin: Origin
    dataset_id: str
    member_path: str
    raw_hash: str
    source_group_id: str
    raw_json: str
    crop: str
    parsed_fields: tuple[ParsedValue, ...]
    photo_time: ParsedTime
    env_time: ParsedTime
    actual_dimensions: Dimensions
    declared_dimensions: Dimensions
    image_member_path: str
    scenario: str | None = None

    @property
    def raw_bytes(self) -> bytes:
        """Recover the exact original UTF-8 source bytes, including whitespace."""
        return self.raw_json.encode("utf-8")

    def reference(self, field_path: str) -> SourceReference:
        """Link derived evidence back to the same immutable source group."""
        return SourceReference(
            record_id=self.id, member_path=self.member_path, raw_hash=self.raw_hash,
            field_path=field_path, source_group_id=self.source_group_id, origin=self.origin,
        )

    @computed_field
    @property
    def raw(self) -> RawValue:
        """Return a fresh exact-decimal tree; mutation cannot affect this record."""
        return decode_raw(self.raw_json)

    @computed_field
    @property
    def values(self) -> dict[str, ParsedValue]:
        """Index immutable values without exposing mutable record storage."""
        return {value.field_path: value for value in self.parsed_fields}


class ModelComparison(FrozenModel):
    """An adopted model result, never an independent source measurement."""

    status: Literal["available", "failed", "stale", "not_adopted"]
    run_id: str
    origin: Origin = "actual"
    crop: str | None = None
    growth_week: int | None = None
    confidence: Decimal | None = None
    crop_confidence: Decimal | None = None
    growth_confidence: Decimal | None = None
    model_identity: dict[str, str] | None = None
    image_sha256: str | None = None
    json_sha256: str | None = None
    execution_type: Literal["real_inference"] | None = None
    elapsed_ms: float | None = None
    request_id: str | None = None
    based_on_revision: int | None = None
    based_on_data_revision: int | None = None


class RuleDefinition(FrozenModel):
    """Versioned screening purpose and limits."""

    rule_id: str
    version: str
    fields: tuple[str, ...]
    assumed_unit: str | None
    rationale: str
    not_concluded: str
    next_question: str


class RuleResult(FrozenModel):
    """A screening outcome linked to its exact source evidence."""

    rule_id: str
    version: str
    outcome: Literal["matched", "not_matched", "not_evaluable"]
    field_paths: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    reason: str
    next_question: str


class Assessment(FrozenModel):
    """Current checks with a stable original-source presentation group."""

    record_id: str
    rule_version: str
    checks: tuple[RuleResult, ...]
    candidate: bool
    group_id: str
    parsed_fields: tuple[ParsedValue, ...]

    @computed_field
    @property
    def effective_values(self) -> dict[str, ParsedValue]:
        """Return a disposable index of effective parsed values."""
        return {value.field_path: value for value in self.parsed_fields}


class Inventory(FrozenModel):
    """Aggregated counts, explicitly not counts of plants or sensor failures."""

    dataset_id: str
    source_problems: int = 0
    zip_sha256: str | None
    zip_bytes: int | None
    total_json: int
    total_images: int
    records: int
    ph_candidate_records: int
    ph_candidate_groups: int
    actual_dimensions: tuple[tuple[str, int], ...]
    declared_dimensions: tuple[tuple[str, int], ...]
    environment_after_photo: int
    environment_after_photo_1_to_10_seconds: int
