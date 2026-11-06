# Copyright (c) 2026 FarmCheck contributors.
"""Screening boundaries and stable original-source grouping regressions."""

from decimal import Decimal

from farmcheck.contracts import ModelComparison, SourceRecord
from farmcheck.rules import assess, canonical_decimal, group_identity
from farmcheck.source import load_catalog, synthetic_model


def example(scenario: str) -> SourceRecord:
    """Select an owned scenario by its explicit label."""
    return next(
        record for record in load_catalog("synthetic").records.values()
        if record.scenario == scenario
    )


def test_ph_threshold_matches_when_decimal_is_just_above_14() -> None:
    """Given an exact boundary overlay, compare without rounding."""
    # Given
    record = example("boundary_exact_14")
    # When
    assessment = assess(record, {"environment.ph": "14.0000000000000001"})
    # Then
    assert assessment.checks[0].outcome == "matched"


def test_ph_threshold_does_not_match_when_exactly_14() -> None:
    """Given pH 14, the strict greater-than screen does not match."""
    # Given
    record = example("boundary_exact_14")
    # When
    assessment = assess(record, {})
    # Then
    assert assessment.checks[0].outcome == "not_matched"


def test_group_is_stable_when_one_member_is_corrected() -> None:
    """Given shared original evidence, an overlay cannot rewrite membership."""
    # Given
    first, second = example("shared_candidate_a"), example("shared_candidate_b")
    # When
    corrected = assess(first, {"environment.ph": "7"})
    # Then
    assert corrected.group_id == group_identity(second)
    assert corrected.checks[0].outcome == "not_matched"
    assert first.values["environment.ph"].decimal == 16


def test_invalid_is_not_evaluable_when_numeric_overlay_is_bad() -> None:
    """Given invalid required input, never turn it into zero."""
    # Given
    record = example("shared_candidate_a")
    # When
    assessment = assess(record, {"environment.ph": "not a number"})
    # Then
    assert tuple(item.outcome for item in assessment.checks[:2]) == (
        "not_evaluable", "matched"
    )


def test_model_difference_matches_when_injected_week_differs() -> None:
    """Given explicitly injected model output, mismatch requests recheck."""
    # Given
    record = example("injected_week_difference")
    model = synthetic_model(record)
    # When
    assessment = assess(record, {}, model)
    # Then
    assert assessment.checks[2].outcome == "matched"
    assert record.origin == "injected"


def test_model_failure_is_not_evaluable_when_request_failed() -> None:
    """Given a failed model run, do not invent a difference."""
    # Given
    record = example("model_failure")
    model = synthetic_model(record)
    # When
    assessment = assess(record, {}, model)
    # Then
    assert assessment.checks[2].outcome == "not_evaluable"


def test_group_decimal_is_exact_when_precision_exceeds_decimal_context() -> None:
    """Given long equivalent numbers, canonicalization never rounds."""
    # Given
    first = Decimal("123456789012345678901234567890.10")
    second = Decimal("123456789012345678901234567890.1")
    # When
    keys = canonical_decimal(first), canonical_decimal(second)
    # Then
    assert keys[0] == keys[1]
    assert keys[0] != canonical_decimal(Decimal("123456789012345678901234567890.2"))


def test_unknown_unit_group_is_qualified_when_basis_is_consistent() -> None:
    """Given matching unknown bases, retain a qualified shared group."""
    # Given
    record = example("shared_candidate_a")
    fields = tuple(
        value.model_copy(update={"unit_status": "unknown", "raw_unit": None})
        for value in record.parsed_fields
    )
    first = record.model_copy(update={"parsed_fields": fields})
    second = first.model_copy(update={"id": "second-source-record"})
    # When
    groups = group_identity(first), group_identity(second)
    # Then
    assert groups[0] == groups[1]
    assert not groups[0].startswith("singleton:")


def test_unadopted_model_is_not_evaluable_when_output_was_not_adopted() -> None:
    """Given model output not adopted, no difference screen may match."""
    # Given
    record = example("boundary_exact_14")
    model = ModelComparison(
        status="not_adopted", run_id="not-adopted", crop="other", growth_week=99
    )
    # When
    assessment = assess(record, {}, model)
    # Then
    assert assessment.checks[2].outcome == "not_evaluable"
