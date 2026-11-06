# Copyright (c) 2026 FarmCheck contributors.
"""Exact numeric and timestamp contract regressions."""

from decimal import Decimal

import pytest

from farmcheck.contracts import Dimensions, decode_raw
from farmcheck.normalize import field_value, parse_number, parse_time, time_difference
from farmcheck.source import make_record


@pytest.mark.parametrize(
    ("raw", "status", "decimal"),
    [
        (None, "missing", None),
        ("   ", "blank", None),
        ("-", "dash", None),
        (True, "invalid", None),
        ("NaN", "invalid", None),
        ("Infinity", "invalid", None),
        ("1,000", "invalid", None),
        ("1e999999999999999999999999999999", "invalid", None),
        ("\uff11\uff14", "invalid", None),
        ("-0", "zero", Decimal("-0")),
        (0, "zero", Decimal(0)),
        (" .5e+2 ", "number", Decimal(50)),
        ("14.0000000000000001", "number", Decimal("14.0000000000000001")),
    ],
)
def test_number_preserves_interpretation_when_raw_varies(
    status: str, decimal: Decimal | None, *, raw: str | int | bool | None
) -> None:
    """Given raw input, parsing retains its token and exact interpretation."""
    # Given / When
    parsed = parse_number(raw, "environment.ph")
    # Then
    assert (parsed.raw, parsed.status, parsed.decimal) == (raw, status, decimal)


def test_presence_distinguishes_absent_when_key_missing() -> None:
    """Given absent and explicit null entries, retain different presence."""
    # Given
    raw = decode_raw('{"present":null}')
    # When
    absent, absent_value = field_value(raw, "absent")
    present, null_value = field_value(raw, "present")
    results = (
        parse_number(absent_value, "absent", present=absent),
        parse_number(null_value, "present", present=present),
    )
    # Then
    assert tuple(item.presence for item in results) == ("absent", "null")


def test_decimal_token_remains_exact_when_json_number_is_not_string() -> None:
    """Given a decimal JSON token, decoding avoids binary float rounding."""
    # Given
    text = '{"environment":{"ph":14.0000000000000001}}'
    # When
    _, value = field_value(decode_raw(text), "environment.ph")
    parsed = parse_number(value, "environment.ph")
    # Then
    assert parsed.decimal == Decimal("14.0000000000000001")


def test_time_delta_is_signed_when_environment_is_later() -> None:
    """Given source local clocks, a positive difference is informational."""
    # Given
    text = (
        '{"images":{"photo_date":"240803122557"},'
        '"environment":{"env_date":"2024-08-03 12:26:00"}}'
    )
    record = make_record(text, "synthetic/time.json", Dimensions(width=380, height=380))
    # When
    difference = time_difference(record)
    # Then
    assert difference.seconds == 3
    assert record.photo_time.timezone_status == "unknown"


def test_time_is_invalid_when_photo_year_is_outside_source_context() -> None:
    """Given a two-digit year outside 2024, do not infer a century."""
    # Given / When
    parsed = parse_time("990803122557", photo=True)
    # Then
    assert parsed.status == "invalid"
    assert parsed.parsed is None
