# Copyright (c) 2026 FarmCheck contributors.
"""Prepare private, unchanged JPEGs for a same-source capture-day diagnostic."""

import argparse
import hashlib
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Final, Literal, Self, override
from zipfile import ZipFile

from pydantic import Field, TypeAdapter, model_validator

from farmcheck.contracts import FrozenModel, SourceRecord
from farmcheck.source import load_catalog, text_at

type Partition = Literal["train", "validation", "test", "quarantine"]
type SupportedWeek = Literal[1, 3, 4, 7, 8, 12, 13]
type SHA256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
type PreparationIssue = Literal[
    "weeks", "days_overlap", "empty_partition", "unknown_day", "unknown_week",
    "quarantine_day", "photo_time", "archive_hash", "source_problems", "record_count",
    "duplicate_member", "ambiguous_pair", "cross_split_image", "unobserved_day",
]
SUPPORTED_WEEKS: Final = (1, 3, 4, 7, 8, 12, 13)
QUARANTINE_WEEKS: Final = (0, 2, 5, 6)
EVALUATION_SCOPE: Final = (
    "Exploratory held-out capture-day diagnostic from the same private source; "
    "not verified independent-plant, site or cycle evaluation. "
    "Only image pixels may be model inputs, never filenames, timestamps or other metadata."
)
WEEK_PARSER: Final = TypeAdapter(int)


@dataclass(frozen=True, slots=True)
class PreparationError(ValueError):
    """Identify an input or partition violation without silently dropping records."""

    issue: PreparationIssue
    detail: str = ""

    @override
    def __str__(self) -> str:
        """Describe the failed preparation invariant."""
        messages: dict[PreparationIssue, str] = {
            "weeks": "supported_weeks must contain exactly the seven audited weeks",
            "days_overlap": "Capture days must be unique and partition-disjoint",
            "empty_partition": "Each explicit capture-day partition must be nonempty",
            "unknown_day": "Unknown capture day",
            "unknown_week": "Unknown growth week",
            "quarantine_day": "Eligible week on a quarantine-only capture day",
            "photo_time": "Invalid images.photo_date",
            "archive_hash": "Archive SHA256 does not match the split policy",
            "source_problems": "Catalog contains source problems",
            "record_count": "Catalog record count does not match the split policy",
            "duplicate_member": "Archive contains repeated member names",
            "ambiguous_pair": "Ambiguous source JPEG pairing",
            "cross_split_image": "An identical image SHA256 crosses partitions",
            "unobserved_day": "Policy dates do not match the observed capture days",
        }
        return messages[self.issue] + (f": {self.detail}" if self.detail else "")


class SplitPolicy(FrozenModel):
    """Pin the audited source, labels, count and explicit source-local capture days."""

    archive_sha256: SHA256
    supported_weeks: tuple[SupportedWeek, ...]
    train_days: tuple[date, ...]
    validation_days: tuple[date, ...]
    test_days: tuple[date, ...]
    quarantine_days: tuple[date, ...]
    expected_records: Annotated[int, Field(strict=True, gt=0)] = 3872

    @model_validator(mode="after")
    def check_allocation(self) -> Self:
        """Reject missing/duplicate labels and overlapping or repeated dates."""
        if tuple(sorted(self.supported_weeks)) != SUPPORTED_WEEKS:
            raise PreparationError(issue="weeks")
        days = self.train_days + self.validation_days + self.test_days + self.quarantine_days
        if len(set(days)) != len(days):
            raise PreparationError(issue="days_overlap")
        if not all((self.train_days, self.validation_days, self.test_days, self.quarantine_days)):
            raise PreparationError(issue="empty_partition")
        return self


class ManifestRecord(FrozenModel):
    """Keep source provenance separate from the only model input, unchanged pixels."""

    record_id: str
    source_member: str
    source_sha256: SHA256
    image_file: str
    image_sha256: SHA256
    week: int
    capture_day: date
    split: Partition


class TrainingManifest(FrozenModel):
    """Versioned private extraction index, retaining quarantine but excluding it by split."""

    version: Literal[1] = 1
    archive_sha256: SHA256
    label_field: Literal["growth.growth_cycle"] = "growth.growth_cycle"
    supported_weeks: tuple[SupportedWeek, ...]
    evaluation_scope: str = EVALUATION_SCOPE
    records: tuple[ManifestRecord, ...]


def assign_split(policy: SplitPolicy, week: int, capture_day: date) -> Partition:
    """Assign explicit days only, quarantining noneligible audited labels first."""
    known_days = (
        policy.train_days + policy.validation_days + policy.test_days + policy.quarantine_days
    )
    if capture_day not in known_days:
        raise PreparationError(issue="unknown_day", detail=str(capture_day))
    if week in QUARANTINE_WEEKS:
        return "quarantine"
    if week not in policy.supported_weeks:
        raise PreparationError(issue="unknown_week", detail=str(week))
    if capture_day in policy.train_days:
        return "train"
    if capture_day in policy.validation_days:
        return "validation"
    if capture_day in policy.test_days:
        return "test"
    raise PreparationError(issue="quarantine_day", detail=str(capture_day))


def record_partition(record: SourceRecord, policy: SplitPolicy) -> tuple[int, date, Partition]:
    """Read the original structured week and parsed photo clock, never filename tokens."""
    week = WEEK_PARSER.validate_python(text_at(record.raw, "growth.growth_cycle"))
    if record.photo_time.parsed is None:
        raise PreparationError(issue="photo_time", detail=record.member_path)
    capture_day = record.photo_time.parsed.date()
    return week, capture_day, assign_split(policy, week, capture_day)


def prepare_dataset(zip_path: Path, policy: SplitPolicy, output: Path) -> TrainingManifest:
    """Validate the entire allocation before creating a new private output directory."""
    with zip_path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != policy.archive_sha256:
        raise PreparationError(issue="archive_hash")
    catalog = load_catalog("green_tea", zip_path)
    if catalog.problems:
        raise PreparationError(issue="source_problems", detail=str(len(catalog.problems)))
    if len(catalog.records) != policy.expected_records:
        raise PreparationError(issue="record_count")
    records: list[ManifestRecord] = []
    image_splits: dict[str, Partition] = {}
    with ZipFile(zip_path) as archive:
        names = archive.namelist()
        basenames = Counter(name.rsplit("/", 1)[-1] for name in names
                            if name.lower().endswith(".jpg"))
        if len(set(names)) != len(names):
            raise PreparationError(issue="duplicate_member")
        for record in sorted(catalog.records.values(), key=lambda item: item.id):
            if basenames[record.image_member_path.rsplit("/", 1)[-1]] != 1:
                raise PreparationError(issue="ambiguous_pair", detail=record.member_path)
            week, capture_day, split = record_partition(record, policy)
            image_digest = hashlib.sha256(archive.read(record.image_member_path)).hexdigest()
            previous = image_splits.setdefault(image_digest, split)
            if previous != split:
                raise PreparationError(issue="cross_split_image")
            records.append(ManifestRecord(
                record_id=record.id, source_member=record.member_path,
                source_sha256=record.raw_hash, image_file=f"images/{record.id}.jpg",
                image_sha256=image_digest, week=week, capture_day=capture_day, split=split,
            ))
        known_days = (
            policy.train_days + policy.validation_days + policy.test_days + policy.quarantine_days
        )
        if {record.capture_day for record in records} != set(known_days):
            raise PreparationError(issue="unobserved_day")
        manifest = TrainingManifest(
            archive_sha256=digest, supported_weeks=policy.supported_weeks, records=tuple(records),
        )
        output.mkdir(parents=True, exist_ok=False)
        (output / "images").mkdir()
        for item in records:
            original = catalog.records[item.record_id]
            _ = (output / item.image_file).write_bytes(archive.read(original.image_member_path))
    _ = (output / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return manifest


class PreparationArguments(argparse.Namespace):
    """Typed argparse boundary matching the existing source inventory command."""

    zip: Path = Path()
    policy: Path = Path()
    output: Path = Path()


def main() -> None:
    """Prepare an explicitly selected private archive without training or uploading."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--zip", type=Path, required=True)
    _ = parser.add_argument("--policy", type=Path, required=True)
    _ = parser.add_argument("--output", type=Path, required=True)
    args = PreparationArguments()
    _ = parser.parse_args(namespace=args)
    policy = SplitPolicy.model_validate_json(args.policy.read_bytes())
    _ = prepare_dataset(args.zip, policy, args.output)


if __name__ == "__main__":
    main()
