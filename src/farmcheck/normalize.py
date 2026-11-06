# Copyright (c) 2026 FarmCheck contributors.
"""Exact numeric, unit and wall-clock interpretation without source changes."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Final

from farmcheck.contracts import (
    ParsedTime,
    ParsedValue,
    ParseStatus,
    Presence,
    RawValue,
    SourceRecord,
    TimeDifference,
)

NUMERIC_FIELDS: Final = (
    "environment.ph",
    "environment.ec",
    "environment.co2",
    "environment.temp",
    "environment.humidity",
    "growth.growth_cycle",
)
DECIMAL_TEXT: Final = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
SOURCE_YEAR: Final = 2024
SOURCE_UNITS: Final = {
    "environment.ph": "pH",
    "environment.ec": "dS/m",
    "environment.co2": "ppm",
    "environment.temp": "℃",
    "environment.humidity": "%",
}


def field_value(raw: RawValue, path: str) -> tuple[bool, RawValue]:
    """Locate a dotted JSON path, keeping absent keys distinct from null."""
    value = raw
    for part in path.split("."):
        match value:
            case dict() as mapping:
                if part not in mapping:
                    return False, None
                value = mapping[part]
            case str() | int() | Decimal() | None | list():
                return False, None
    return True, value


def parse_number(raw: RawValue, field_path: str, *, present: bool = True) -> ParsedValue:
    """Interpret only ASCII decimal syntax; zero remains a usable number."""
    scalar: str | int | Decimal | bool | None
    match raw:
        case list() | dict():
            return ParsedValue(
                field_path=field_path, raw=None, presence="present", status="invalid"
            )
        case str() | int() | Decimal() | None:
            scalar = raw
    presence: Presence = "present" if scalar is not None else ("null" if present else "absent")
    status: ParseStatus = "invalid"
    numeric: Decimal | None = None
    match scalar:
        case None:
            status = "missing"
        case bool():
            status = "invalid"
        case str() as text:
            status, numeric = parse_numeric_text(text)
        case Decimal():
            numeric = scalar
            scalar = str(scalar)
        case int():
            numeric = Decimal(scalar)
    if numeric is not None:
        if numeric.is_finite():
            status = "zero" if numeric == 0 else "number"
        else:
            numeric = None
    return ParsedValue(
        field_path=field_path, raw=scalar,
        presence=presence, status=status, decimal=numeric
    )


def parse_numeric_text(text: str) -> tuple[ParseStatus, Decimal | None]:
    """Classify a textual token before Decimal construction."""
    stripped = text.strip()
    if not stripped:
        return "blank", None
    if stripped == "-":
        return "dash", None
    if DECIMAL_TEXT.fullmatch(stripped):
        try:
            return "number", Decimal(stripped)
        except InvalidOperation:
            return "invalid", None
    return "invalid", None


def parse_fields(raw: RawValue) -> tuple[ParsedValue, ...]:
    """Read required fields and only units actually repeated by the caption."""
    _, caption_value = field_value(raw, "caption.detail")
    match caption_value:
        case str() as text:
            caption = text
        case int() | Decimal() | None | list() | dict():
            caption = ""
    fields: list[ParsedValue] = []
    for path in NUMERIC_FIELDS:
        present, value = field_value(raw, path)
        parsed = parse_number(value, path, present=present)
        unit = SOURCE_UNITS.get(path)
        if unit is not None and unit in caption:
            parsed = parsed.model_copy(
                update={
                    "raw_unit": unit,
                    "unit_status": "source_declared",
                    "unit_source_ref": "caption.detail",
                }
            )
        fields.append(parsed)
    return tuple(fields)


def parse_time(raw: RawValue, *, photo: bool = False) -> ParsedTime:
    """Parse documented 2024 source clocks without assigning a timezone."""
    match raw:
        case None:
            return ParsedTime(raw=None, parsed=None, status="missing")
        case str() as text:
            try:
                if photo:
                    if not re.fullmatch(r"24[0-9]{10}", text):
                        return ParsedTime(raw=text, parsed=None, status="invalid")
                    text_value = (
                        f"{SOURCE_YEAR}-{text[2:4]}-{text[4:6]}T"
                        f"{text[6:8]}:{text[8:10]}:{text[10:12]}"
                    )
                else:
                    if not re.fullmatch(r"[0-9]{4}(-[0-9]{2}){2} [0-9]{2}(:[0-9]{2}){2}", text):
                        return ParsedTime(raw=text, parsed=None, status="invalid")
                    text_value = text
                parsed = datetime.fromisoformat(text_value)
            except ValueError:
                return ParsedTime(raw=text, parsed=None, status="invalid")
            return ParsedTime(
                raw=text, parsed=parsed, status="parsed", basis="source_local_clock"
            )
        case bool() | int() | Decimal() | list() | dict():
            return ParsedTime(raw=str(raw), parsed=None, status="invalid")


def time_difference(record: SourceRecord) -> TimeDifference:
    """Compute only comparable documented local-clock differences."""
    photo, env = record.photo_time, record.env_time
    if photo.parsed is None or env.parsed is None:
        return TimeDifference(seconds=None, reason="missing_or_invalid_timestamp")
    if photo.basis != "source_local_clock" or env.basis != "source_local_clock":
        return TimeDifference(seconds=None, reason="incomparable_clock_basis")
    return TimeDifference(seconds=int((env.parsed - photo.parsed).total_seconds()))
