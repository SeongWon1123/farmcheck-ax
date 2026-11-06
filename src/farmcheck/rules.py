# Copyright (c) 2026 FarmCheck contributors.
"""Versioned review screens and original-source grouping, never diagnosis."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Final

from farmcheck.contracts import (
    Assessment,
    ModelComparison,
    ParsedValue,
    RuleDefinition,
    RuleResult,
    SourceRecord,
)
from farmcheck.normalize import NUMERIC_FIELDS, parse_number

if TYPE_CHECKING:
    from decimal import Decimal

RULE_VERSION: Final = "1.0.0"
PH_THRESHOLD: Final = 14
RULE_REGISTRY: Final = (
    RuleDefinition(
        rule_id="PH_SCREEN", version=RULE_VERSION, fields=("environment.ph",),
        assumed_unit="pH", rationale="pH > 14를 시연용 원문 확인 기준으로 사용합니다.",
        not_concluded="녹차 적정 범위나 물리적 불가능, 센서 고장으로 단정하지 않습니다.",
        next_question="원본 pH 값과 단위, 측정 기록을 확인했나요?",
    ),
    RuleDefinition(
        rule_id="REQUIRED_NUMERIC", version=RULE_VERSION, fields=NUMERIC_FIELDS,
        assumed_unit=None, rationale="필수 숫자 필드가 해석 가능한지 확인합니다.",
        not_concluded="누락된 기록이 작물의 문제를 뜻하지 않습니다.",
        next_question="누락 또는 해석할 수 없는 필드의 원본을 확인했나요?",
    ),
    RuleDefinition(
        rule_id="MODEL_RECORD_DIFFERENCE", version=RULE_VERSION,
        fields=("images.class_name", "growth.growth_cycle"), assumed_unit=None,
        rationale="채택된 모델 출력과 현재 적용 중인 기록을 비교합니다.",
        not_concluded="불일치만으로 기록이나 모델 중 어느 쪽이 맞는지 확정하지 않습니다.",
        next_question="사진과 기록의 작물·주차를 사람이 다시 확인했나요?",
    ),
)
GROUP_FIELDS: Final = (
    "environment.ph", "environment.ec", "environment.co2", "environment.temp"
)


def canonical_decimal(value: Decimal) -> str:
    """Encode numeric equality without context-dependent rounding."""
    if value == 0:
        return "0"
    sign, digits, exponent = value.as_tuple()
    match exponent:
        case int() as number:
            numeric_exponent = number
        case "n" | "N" | "F":
            return f"nonfinite:{value}"
    text = "".join(str(digit) for digit in digits)
    while text.endswith("0"):
        text = text[:-1]
        numeric_exponent += 1
    return f"{sign}:{text}:{numeric_exponent}"


def group_identity(record: SourceRecord) -> str:
    """Derive stable presentation membership only from original evidence."""
    parts = [record.dataset_id, record.crop, record.origin]
    if record.env_time.parsed is None:
        return f"singleton:{record.id}"
    parts.append(record.env_time.parsed.isoformat())
    for path in GROUP_FIELDS:
        value = record.values[path]
        if value.decimal is None:
            return f"singleton:{record.id}"
        parts.extend((
            canonical_decimal(value.decimal), value.unit_status, value.raw_unit or ""
        ))
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def effective_values(record: SourceRecord, overlays: dict[str, str]) -> dict[str, ParsedValue]:
    """Apply authorized field overlays without changing source or group keys."""
    values = record.values
    for path, raw in overlays.items():
        if path in values:
            original = values[path]
            values[path] = parse_number(raw, path).model_copy(
                update={
                    "raw_unit": original.raw_unit,
                    "unit_status": original.unit_status,
                    "unit_source_ref": original.unit_source_ref,
                }
            )
    return values


def normalize_crop(crop: str) -> str:
    """Normalize only separators in verified source crop names."""
    return crop.replace("_", "").replace(" ", "")


def assess(
    record: SourceRecord,
    overlays: dict[str, str],
    model: ModelComparison | None = None,
) -> Assessment:
    """Screen effective values while retaining original grouping membership."""
    values = effective_values(record, overlays)
    ph = values["environment.ph"].decimal
    ph_outcome = (
        "not_evaluable" if ph is None else ("matched" if ph > PH_THRESHOLD else "not_matched")
    )
    invalid = tuple(path for path in NUMERIC_FIELDS if values[path].decimal is None)
    model_outcome = "not_evaluable"
    model_reason = "현재 채택된 모델 결과가 없어 비교하지 않았습니다."
    if model is not None:
        match model.status:
            case "available":
                week = values["growth.growth_cycle"].decimal
                if model.crop is not None and model.growth_week is not None and week is not None:
                    differs = (
                        normalize_crop(model.crop)
                        != normalize_crop(overlays.get("images.class_name", record.crop))
                        or week != model.growth_week
                    )
                    model_outcome = "matched" if differs else "not_matched"
                    model_reason = (
                        f"모델 실행 {model.run_id}. 신뢰도는 보정된 정답 확률이 아닙니다."
                    )
            case "failed" | "stale" | "not_adopted":
                model_reason = f"모델 상태 {model.status}. 현재 비교에 사용할 결과가 없습니다."
    outcomes = (ph_outcome, "matched" if invalid else "not_matched", model_outcome)
    reasons = (
        "원문 숫자를 반올림 없이 해석하여 시연 기준 pH > 14와 비교했습니다.",
        (
            "확인이 필요한 필드: " + ", ".join(invalid)
            if invalid else "필수 숫자 필드를 해석했습니다."
        ),
        model_reason,
    )
    checks = tuple(
        RuleResult(
            rule_id=rule.rule_id, version=rule.version, outcome=outcome,
            field_paths=(
                invalid if rule.rule_id == "REQUIRED_NUMERIC" and invalid else rule.fields
            ),
            evidence_refs=tuple(
                f"{record.id}:{record.raw_hash}:{field}"
                for field in (
                    invalid if rule.rule_id == "REQUIRED_NUMERIC" and invalid else rule.fields
                )
            ) + ((f"model:{model.run_id}",) if model and rule.rule_id == "MODEL_RECORD_DIFFERENCE"
                 and model.status == "available" else ()),
            reason=reason, next_question=rule.next_question,
        )
        for rule, outcome, reason in zip(RULE_REGISTRY, outcomes, reasons, strict=True)
    )
    return Assessment(
        record_id=record.id, rule_version=RULE_VERSION, checks=checks,
        candidate=any(check.outcome == "matched" for check in checks),
        group_id=group_identity(record), parsed_fields=tuple(values.values()),
    )


def peers(
    record: SourceRecord, records: tuple[SourceRecord, ...], overlays: dict[str, str]
) -> tuple[SourceRecord, ...]:
    """Select at most ten same-week peers, not an individual growth trajectory."""
    values = effective_values(record, overlays)
    week = values["growth.growth_cycle"].decimal
    units = tuple((values[path].unit_status, values[path].raw_unit) for path in GROUP_FIELDS)
    matches = (
        other for other in records
        if other.id != record.id and other.crop == record.crop and week is not None
        and other.values["growth.growth_cycle"].decimal == week
        and tuple((other.values[path].unit_status, other.values[path].raw_unit)
                  for path in GROUP_FIELDS) == units
    )
    return tuple(sorted(matches, key=lambda item: (item.photo_time.raw or "", item.id))[:10])
