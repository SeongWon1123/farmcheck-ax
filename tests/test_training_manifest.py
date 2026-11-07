# Copyright (c) 2026 FarmCheck contributors.
"""Deterministic split boundaries and unchanged private JPEG extraction."""

import hashlib
import io
import json
from datetime import date
from pathlib import Path
from zipfile import ZipFile

import pytest
from PIL import Image
from pydantic import ValidationError

from farmcheck.source import load_catalog, text_at
from farmcheck.training_manifest import (
    PreparationError,
    SplitPolicy,
    TrainingManifest,
    assign_split,
    main,
    prepare_dataset,
)


def policy_for(archive: Path) -> SplitPolicy:
    """Pin a four-record archive to four audited capture days."""
    return SplitPolicy(
        archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        supported_weeks=(1, 3, 4, 7, 8, 12, 13),
        train_days=(date(2024, 7, 11),),
        validation_days=(date(2024, 7, 15),),
        test_days=(date(2024, 7, 26),),
        quarantine_days=(date(2024, 7, 19),),
        expected_records=4,
    )


@pytest.fixture
def private_zip(tmp_path: Path) -> Path:
    """Build genuine JPEG/source pairs without source filenames encoding their labels."""
    path = tmp_path / "private.zip"
    with ZipFile(path, "w") as archive:
        for index, (week, day) in enumerate(((1, 11), (3, 15), (4, 26), (0, 19))):
            pixels = io.BytesIO()
            with Image.new("RGB", (8, 8), (index * 60, 30, 90)) as image:
                image.save(pixels, format="JPEG")
            text = json.dumps({
                "images": {
                    "class_name": "녹차", "imagepath": f"sample{index}.jpg",
                    "photo_date": f"2407{day:02d}122500",
                },
                "growth": {"growth_cycle": str(week)},
            }, ensure_ascii=False) + "\n"
            archive.writestr(f"Sample/labels/05.녹차/sample{index}.json", text)
            archive.writestr(f"Sample/photos/05.녹차/sample{index}.jpg", pixels.getvalue())
    return path


@pytest.mark.parametrize(
    ("week", "day", "expected"),
    [(1, 11, "train"), (3, 15, "validation"), (4, 26, "test"),
     (0, 11, "quarantine"), (2, 15, "quarantine"), (5, 26, "quarantine"),
     (6, 19, "quarantine")],
)
def test_split_assignment_when_days_are_explicit(
    private_zip: Path, week: int, day: int, expected: str,
) -> None:
    """Quarantine takes precedence while eligible labels use whole capture days."""
    # Given
    policy = policy_for(private_zip)
    # When
    split = assign_split(policy, week, date(2024, 7, day))
    # Then
    assert split == expected


@pytest.mark.parametrize(("week", "day"), [(9, 11), (1, 12), (1, 19), (0, 12)])
def test_split_rejects_unknown_input_when_no_allocation_exists(
    private_zip: Path, week: int, day: int,
) -> None:
    """There is no random fallback for unknown labels, days or quarantine-only days."""
    # Given
    policy = policy_for(private_zip)
    # When / Then
    with pytest.raises(PreparationError):
        _ = assign_split(policy, week, date(2024, 7, day))


def test_policy_rejects_overlap_when_day_occurs_in_two_partitions(private_zip: Path) -> None:
    """Boundary parsing rejects a day shared by train and validation."""
    # Given
    payload = policy_for(private_zip).model_dump()
    payload["validation_days"] = payload["train_days"]
    # When / Then
    with pytest.raises(ValidationError):
        _ = SplitPolicy.model_validate(payload)


@pytest.mark.parametrize(
    "weeks", [(1, 3, 4), (1, 3, 4, 7, 8, 12, 13, 13), (0, 1, 3, 4, 7, 8, 12, 13)],
)
def test_policy_rejects_changed_label_contract(
    private_zip: Path, weeks: tuple[int, ...],
) -> None:
    """Missing, duplicated and unsupported labels cannot silently alter the pilot."""
    # Given
    payload = policy_for(private_zip).model_dump()
    payload["supported_weeks"] = weeks
    # When / Then
    with pytest.raises(ValidationError):
        _ = SplitPolicy.model_validate(payload)


def test_preparation_preserves_bytes_and_provenance_when_extracting(
    private_zip: Path, tmp_path: Path,
) -> None:
    """Every source record, including quarantine, retains exact identity and JPEG bytes."""
    # Given
    policy = policy_for(private_zip)
    catalog = load_catalog("green_tea", private_zip)
    output = tmp_path / "prepared"
    # When
    manifest = prepare_dataset(private_zip, policy, output)
    # Then
    assert len(manifest.records) == 4
    assert {item.split for item in manifest.records} == {
        "train", "validation", "test", "quarantine",
    }
    assert TrainingManifest.model_validate_json((output / "manifest.json").read_bytes()) == manifest
    with ZipFile(private_zip) as archive:
        for item in manifest.records:
            original = catalog.records[item.record_id]
            jpeg = archive.read(original.image_member_path)
            assert item.source_member == original.member_path
            assert item.source_sha256 == hashlib.sha256(
                archive.read(item.source_member),
            ).hexdigest()
            assert item.image_file == f"images/{item.record_id}.jpg"
            assert (output / item.image_file).read_bytes() == jpeg
            assert item.image_sha256 == hashlib.sha256(jpeg).hexdigest()
            assert item.week == int(text_at(original.raw, "growth.growth_cycle"))
    assert len(list((output / "images").iterdir())) == 4


def test_preparation_rejects_hash_mismatch_before_writing(
    private_zip: Path, tmp_path: Path,
) -> None:
    """The pinned archive is checked before any output is created."""
    # Given
    policy = policy_for(private_zip).model_copy(update={"archive_sha256": "0" * 64})
    output = tmp_path / "prepared"
    # When / Then
    with pytest.raises(PreparationError):
        _ = prepare_dataset(private_zip, policy, output)
    assert not output.exists()


def test_preparation_rejects_unobserved_policy_day(
    private_zip: Path, tmp_path: Path,
) -> None:
    """A policy cannot mention a known-format date absent from the selected source."""
    # Given
    policy = policy_for(private_zip).model_copy(
        update={"train_days": (date(2024, 7, 11), date(2024, 7, 12))},
    )
    output = tmp_path / "prepared"
    # When / Then
    with pytest.raises(PreparationError):
        _ = prepare_dataset(private_zip, policy, output)
    assert not output.exists()


def test_preparation_rejects_duplicate_pixels_across_partitions(
    private_zip: Path, tmp_path: Path,
) -> None:
    """Exact image bytes shared by train and validation block extraction."""
    # Given
    changed = tmp_path / "duplicate.zip"
    with ZipFile(private_zip) as source, ZipFile(changed, "w") as target:
        duplicate = source.read("Sample/photos/05.녹차/sample0.jpg")
        for name in source.namelist():
            data = duplicate if name.endswith("sample1.jpg") else source.read(name)
            target.writestr(name, data)
    output = tmp_path / "prepared"
    # When / Then
    with pytest.raises(PreparationError):
        _ = prepare_dataset(changed, policy_for(changed), output)
    assert not output.exists()


def test_preparation_rejects_catalog_source_problems(
    private_zip: Path, tmp_path: Path,
) -> None:
    """Malformed source evidence must not disappear from a prepared dataset."""
    # Given
    with ZipFile(private_zip, "a") as archive:
        archive.writestr("Sample/labels/05.녹차/broken.json", "{broken")
    output = tmp_path / "prepared"
    # When / Then
    with pytest.raises(PreparationError):
        _ = prepare_dataset(private_zip, policy_for(private_zip), output)
    assert not output.exists()


def test_cli_prepares_manifest_when_given_explicit_private_paths(
    private_zip: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The actual argparse entry point consumes the serialized policy and writes a manifest."""
    # Given
    policy = policy_for(private_zip)
    policy_path = tmp_path / "policy.json"
    _ = policy_path.write_text(policy.model_dump_json(), encoding="utf-8")
    output = tmp_path / "prepared"
    monkeypatch.setattr("sys.argv", [
        "training_manifest", "--zip", str(private_zip), "--policy", str(policy_path),
        "--output", str(output),
    ])
    # When
    main()
    # Then
    manifest = TrainingManifest.model_validate_json((output / "manifest.json").read_bytes())
    assert manifest.archive_sha256 == policy.archive_sha256
    assert len(manifest.records) == policy.expected_records
