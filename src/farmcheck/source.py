# Copyright (c) 2026 FarmCheck contributors.
"""Private ZIP-backed green-tea catalog and owned, clearly synthetic examples."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Final, override
from zipfile import ZipFile

from PIL import Image, UnidentifiedImageError

from farmcheck.contracts import (
    Dimensions,
    FrozenModel,
    Inventory,
    ModelComparison,
    RawValue,
    SourceRecord,
    decode_raw,
)
from farmcheck.normalize import field_value, parse_fields, parse_time, time_difference
from farmcheck.rules import PH_THRESHOLD, group_identity

DATASET_ID: Final = "Sample.zip:green-tea:2024"
SMALL_CLOCK_DELTA: Final = 10
SVG: Final = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="380" height="380">'
    b'<rect width="380" height="380" fill="#e5f1e3"/>'
    b'<path d="M190 300V160M190 230Q75 210 100 130Q185 115 190 230'
    b'M190 190Q295 175 280 95Q190 90 190 190" stroke="#32734b" '
    b'stroke-width="12" fill="#72ad70"/>'
    b'<text x="190" y="350" text-anchor="middle">SYNTHETIC DEMO</text></svg>'
)


@dataclass(frozen=True, slots=True)
class CatalogConfigurationError(ValueError):
    """Identify unsupported server mode or a missing private source path."""

    mode: str
    zip_supplied: bool

    @override
    def __str__(self) -> str:
        """Explain the supported server-side data configuration."""
        return (
            f"Unsupported catalog configuration: mode={self.mode}, "
            f"zip_supplied={self.zip_supplied}; use synthetic or green_tea with a ZIP path."
        )


class SourceProblem(FrozenModel):
    """Retain an unreadable source reference without inventing a valid record."""

    member_path: str
    raw_hash: str
    kind: str
    message: str


def text_at(raw: RawValue, path: str) -> str:
    """Read source text without coercing other JSON types."""
    _, value = field_value(raw, path)
    match value:
        case str() as text:
            return text
        case int() | Decimal() | None | list() | dict():
            return ""


def integer_at(raw: RawValue, path: str) -> int:
    """Read a source integer metadata field."""
    _, value = field_value(raw, path)
    match value:
        case bool():
            return 0
        case int() as number:
            return number
        case str() | Decimal() | None | list() | dict():
            return 0


def make_record(text: str, member_path: str, actual: Dimensions) -> SourceRecord:
    """Parse one source pair; identity includes the exact source byte hash."""
    raw = decode_raw(text)
    synthetic = member_path.startswith("synthetic/")
    dataset = "farmcheck:synthetic:v1" if synthetic else DATASET_ID
    digest = hashlib.sha256(text.encode()).hexdigest()
    identity = hashlib.sha256(f"{dataset}\0{member_path}\0{digest}".encode()).hexdigest()
    _, photo = field_value(raw, "images.photo_date")
    _, environment = field_value(raw, "environment.env_date")
    origin = "injected" if "injected" in member_path else ("synthetic" if synthetic else "actual")
    return SourceRecord(
        id=identity, origin=origin, dataset_id=dataset, member_path=member_path,
        raw_hash=digest, source_group_id=f"{dataset}:{digest}", raw_json=text,
        crop=text_at(raw, "images.class_name"), parsed_fields=parse_fields(raw),
        photo_time=parse_time(photo, photo=True), env_time=parse_time(environment),
        actual_dimensions=actual,
        declared_dimensions=Dimensions(
            width=integer_at(raw, "images.imagewidth"),
            height=integer_at(raw, "images.imageheight"),
        ),
        image_member_path=text_at(raw, "images.imagepath"),
        scenario=text_at(raw, "scenario") or None,
    )


@dataclass(frozen=True, slots=True)
class Catalog:
    """Keep immutable records and server-owned image locations."""

    records: dict[str, SourceRecord]
    inventory: Inventory
    zip_path: Path | None = None
    problems: tuple[SourceProblem, ...] = ()

    def image_bytes(self, record_id: str) -> tuple[bytes, str]:
        """Read only the image already paired to an authorized catalog record."""
        record = self.records[record_id]
        if self.zip_path is None:
            return SVG, "image/svg+xml"
        with ZipFile(self.zip_path) as archive:
            return archive.read(record.image_member_path), "image/jpeg"


def inventory(
    records: dict[str, SourceRecord], totals: tuple[int, int], path: Path | None
) -> Inventory:
    """Count original pH-screen records and groups, retaining both grains."""
    selected = tuple(record for record in records.values()
                     if (value := record.values["environment.ph"].decimal) is not None
                     and value > PH_THRESHOLD)
    actual = Counter(
        f"{record.actual_dimensions.width}x{record.actual_dimensions.height}"
        for record in records.values()
    )
    declared = Counter(
        f"{record.declared_dimensions.width}x{record.declared_dimensions.height}"
        for record in records.values()
    )
    digest: str | None = None
    if path is not None:
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return Inventory(
        dataset_id=DATASET_ID if path else "farmcheck:synthetic:v1",
        zip_sha256=digest, zip_bytes=path.stat().st_size if path else None,
        total_json=totals[0], total_images=totals[1], records=len(records),
        ph_candidate_records=len(selected),
        ph_candidate_groups=len({group_identity(record) for record in selected}),
        actual_dimensions=tuple(sorted(actual.items())),
        declared_dimensions=tuple(sorted(declared.items())),
        environment_after_photo=sum(
            (time_difference(record).seconds or 0) > 0 for record in records.values()
        ),
        environment_after_photo_1_to_10_seconds=sum(
            1 <= (time_difference(record).seconds or 0) <= SMALL_CLOCK_DELTA
            for record in records.values()
        ),
    )


def synthetic_records() -> dict[str, SourceRecord]:
    """Own demo data, including shared candidates, boundaries and injected cases."""
    records: dict[str, SourceRecord] = {}
    examples = (
        ("shared_candidate_a", "16", "3"),
        ("shared_candidate_b", "16", "3"),
        ("boundary_exact_14", "14", "3"),
        ("boundary_above_14", "14.0000000000000001", "3"),
        ("injected_week_difference", "7", "3"),
        ("model_failure", "7", "3"),
        ("missing_required", None, "3"),
        ("zero_is_numeric", "-0", "3"),
    )
    for index, (scenario, ph, week) in enumerate(examples):
        raw = {
            "scenario": scenario,
            "images": {
                "class_name": "녹차", "photo_date": f"2408031225{index:02d}",
                "imagewidth": 380, "imageheight": 380, "imagepath": f"{scenario}.svg",
            },
            "environment": {
                "ph": ph, "ec": "0.772", "co2": "409", "temp": "30.32",
                "humidity": "77.54", "env_date": "2024-08-03 12:25:00",
            },
            "growth": {"growth_cycle": week, "root_length": "-"},
            "caption": {"detail": "합성 시연 데이터: pH, dS/m, ppm, ℃, %"},
        }
        record = make_record(
            json.dumps(raw, ensure_ascii=False),
            f"synthetic/{scenario}.json", Dimensions(width=380, height=380),
        )
        records[record.id] = record
    return records


def synthetic_model(record: SourceRecord) -> ModelComparison | None:
    """Return explicitly injected fixtures, never claim these are live inference."""
    if record.origin not in {"synthetic", "injected"}:
        return None
    match record.scenario:
        case "injected_week_difference":
            return ModelComparison(
                status="available", run_id=f"injected:{record.id}", origin="injected",
                crop="녹차", growth_week=4, confidence=Decimal("0.9"),
            )
        case "model_failure":
            return ModelComparison(
                status="failed", run_id=f"injected:{record.id}", origin="injected"
            )
        case _:
            return None


def read_pair(
    archive: ZipFile, name: str, image_names: dict[str, str],
) -> SourceRecord | SourceProblem:
    """Keep malformed or unpaired inputs visible as immutable source problems."""
    data = archive.read(name)
    digest = hashlib.sha256(data).hexdigest()
    try:
        text = data.decode("utf-8")
        raw = decode_raw(text)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return SourceProblem(
            member_path=name, raw_hash=digest, kind="invalid_json",
            message="원문 JSON을 해석할 수 없습니다. 원본은 ZIP에 보존되어 있습니다.",
        )
    image_name = image_names.get(text_at(raw, "images.imagepath"))
    if image_name is None:
        return SourceProblem(
            member_path=name, raw_hash=digest, kind="missing_image",
            message="원문이 참조하는 사진을 찾을 수 없습니다.",
        )
    try:
        with Image.open(io.BytesIO(archive.read(image_name))) as image:
            dimensions = Dimensions(width=image.width, height=image.height)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        return SourceProblem(
            member_path=name, raw_hash=digest, kind="invalid_image",
            message="연결된 사진의 크기를 읽을 수 없습니다.",
        )
    return make_record(text, name, dimensions).model_copy(update={"image_member_path": image_name})


def load_catalog(mode: str, zip_path: Path | None = None) -> Catalog:
    """Load private green tea only on explicit server mode; default is synthetic."""
    if mode == "synthetic":
        records = synthetic_records()
        return Catalog(records, inventory(records, (len(records), len(records)), None))
    if mode != "green_tea" or zip_path is None:
        raise CatalogConfigurationError(mode=mode, zip_supplied=zip_path is not None)
    records: dict[str, SourceRecord] = {}
    problems: list[SourceProblem] = []
    with ZipFile(zip_path) as archive:
        members = archive.namelist()
        image_names = {name.rsplit("/", 1)[-1]: name for name in members
                       if name.lower().endswith(".jpg")}
        json_names = [name for name in members if name.lower().endswith(".json")]
        for name in json_names:
            if "/05.녹차/" not in name:
                continue
            match read_pair(archive, name, image_names):
                case SourceRecord() as record:
                    records[record.id] = record
                case SourceProblem() as problem:
                    problems.append(problem)
    counts = inventory(records, (len(json_names), len(image_names)), zip_path).model_copy(
        update={"source_problems": len(problems)},
    )
    return Catalog(records, counts, zip_path, tuple(problems))


class InventoryArguments(argparse.Namespace):
    """Typed argparse boundary, avoiding dynamic namespace values downstream."""

    zip: Path = Path()
    report: Path | None = None


def main() -> None:
    """Print aggregated inventory; optional report contains no original records."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--zip", type=Path, required=True)
    _ = parser.add_argument("--crop", default="녹차", choices=["녹차"])
    _ = parser.add_argument("--report", type=Path)
    args = InventoryArguments()
    _ = parser.parse_args(namespace=args)
    catalog = load_catalog("green_tea", args.zip)
    report = catalog.inventory.model_dump_json(indent=2)
    if args.report:
        report_path = args.report
        report_path.parent.mkdir(parents=True, exist_ok=True)
        _ = report_path.write_text(report, encoding="utf-8")
    _ = sys.stdout.write(report + "\n")


if __name__ == "__main__":
    main()
