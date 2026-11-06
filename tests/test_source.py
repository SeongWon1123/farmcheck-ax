# Copyright (c) 2026 FarmCheck contributors.
"""Immutable catalog and actual ZIP pairing contract tests."""

import hashlib
import io
from pathlib import Path
from zipfile import ZipFile

import pytest
from PIL import Image
from pydantic import ValidationError

from farmcheck.contracts import Dimensions, SourceRecord
from farmcheck.source import CatalogConfigurationError, load_catalog, make_record


def test_raw_cannot_change_when_decoded_copy_is_mutated() -> None:
    """Given a record, mutations of derived JSON cannot affect source bytes."""
    # Given
    text = '{"environment":{"ph":14.0000000000000001}}'
    record = make_record(text, "synthetic/raw.json", Dimensions(width=380, height=380))
    # When
    raw = record.raw
    if isinstance(raw, dict):
        raw["environment"] = None
    # Then
    assert record.raw_json == text
    assert record.raw_hash == hashlib.sha256(text.encode()).hexdigest()
    assert record.raw_bytes == text.encode()
    assert record.values["environment.ph"].decimal is not None


def test_record_is_frozen_when_field_assignment_is_attempted() -> None:
    """Given an immutable boundary record, assignment fails."""
    # Given
    record = next(iter(load_catalog("synthetic").records.values()))
    # When / Then
    with pytest.raises(ValidationError):
        record.crop = "changed"


def test_serialization_preserves_exact_decimal_when_record_roundtrips() -> None:
    """Given serializable evidence, decimal values survive JSON roundtrip."""
    # Given
    record = make_record(
        '{"environment":{"ph":14.0000000000000001}}',
        "synthetic/exact.json", Dimensions(width=380, height=380),
    )
    # When
    restored = SourceRecord.model_validate_json(
        record.model_dump_json(exclude={"raw", "values"})
    )
    # Then
    assert restored.values == record.values
    assert restored.raw_json == record.raw_json


def test_zip_pairs_image_when_filename_matches_source_reference(tmp_path: Path) -> None:
    """Given a tiny real ZIP, read only green tea and retain separate sizes."""
    # Given
    path = tmp_path / "private.zip"
    image_bytes = io.BytesIO()
    with Image.new("RGB", (380, 380)) as image:
        image.save(image_bytes, format="JPEG")
    text = (
        '{"images":{"class_name":"녹차","imagepath":"plant.jpg",'
        '"imagewidth":1920,"imageheight":1080},'
        '"environment":{"ph":"16","ec":"1","co2":"400","temp":"25",'
        '"env_date":"2024-08-03 12:25:00"},'
        '"caption":{"detail":"pH dS/m ppm ℃"}}'
    )
    with ZipFile(path, "w") as archive:
        archive.writestr("Sample/02.라벨링데이터/05.녹차/plant.json", text)
        archive.writestr("Sample/01.원천데이터/05.녹차/plant.jpg", image_bytes.getvalue())
    # When
    catalog = load_catalog("green_tea", path)
    record = next(iter(catalog.records.values()))
    # Then
    assert catalog.inventory.records == 1
    assert record.actual_dimensions == Dimensions(width=380, height=380)
    assert record.declared_dimensions == Dimensions(width=1920, height=1080)
    assert catalog.image_bytes(record.id) == (image_bytes.getvalue(), "image/jpeg")
    assert record.raw_json == text


@pytest.mark.parametrize("mode", ["unknown", "green_tea"])
def test_catalog_rejects_configuration_when_server_mode_is_incomplete(mode: str) -> None:
    """Given unsupported mode or absent private path, expose a typed error."""
    # Given / When / Then
    with pytest.raises(CatalogConfigurationError) as caught:
        _ = load_catalog(mode)
    assert caught.value.mode == mode
    assert caught.value.zip_supplied is False
