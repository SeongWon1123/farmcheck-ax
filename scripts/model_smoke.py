# Copyright (c) 2026 Farmcheck contributors.
# /// script
# requires-python = ">=3.12"
# dependencies = ["farmcheck"]
# ///
"""Private five-week connectivity smoke; never an accuracy evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Final
from zipfile import ZipFile

import httpx2
from pydantic import BaseModel, ConfigDict, Field

from farmcheck.cropclient import MODEL_BASE_URL, CropClient, Prediction

ROOT: Final = Path(__file__).resolve().parents[1]
SMOKE_COUNT: Final = 5


class _Images(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    imagepath: str
    class_name: str


class _Growth(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    growth_cycle: str = Field(pattern=r"^[0-9]+$")


class _Record(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    images: _Images
    growth: _Growth


class _Observation(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    source_member: str
    source_week: int
    image_sha256: str
    image_bytes: int
    upload_filename: str
    prediction: Prediction


class _Arguments(argparse.Namespace):
    """Typed mutable namespace populated by the standard-library CLI parser."""

    zip_path: Path = Path()
    output: Path = ROOT / "artifacts/private/model-smoke.json"


def main() -> int:
    """Run only the authorized ZIP-to-fixed-vendor flow and save private evidence."""
    # Standard-library argparse avoids adding a CLI dependency for this single script.
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--zip", required=True, type=Path, dest="zip_path")
    _ = parser.add_argument("--crop", choices=["녹차"], default="녹차")
    _ = parser.add_argument("--count", choices=[5], type=int, default=5)
    _ = parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts/private/model-smoke.json"
    )
    args = parser.parse_args(namespace=_Arguments())
    zip_path = args.zip_path
    output = args.output.resolve()
    _ = output.relative_to(ROOT / "artifacts/private")
    output.parent.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(UTC).isoformat()
    with httpx2.Client(
        timeout=httpx2.Timeout(connect=5, read=30, write=15, pool=5),
        limits=httpx2.Limits(max_connections=1, max_keepalive_connections=1),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        schema_response = client.get(f"{MODEL_BASE_URL}/openapi.json")
        _ = schema_response.raise_for_status()
        schema_raw = schema_response.text
        _ = (output.parent / "model-openapi.json").write_text(schema_raw, encoding="utf-8")
    observations: list[_Observation] = []
    selected: dict[int, tuple[str, str]] = {}
    with ZipFile(zip_path) as archive:
        members = sorted(archive.namelist())
        images = {Path(name).name: name for name in members if name.lower().endswith(".jpg")}
        for name in members:
            if not name.endswith(".json") or "/05.녹차/" not in name:
                continue
            record = _Record.model_validate_json(archive.read(name))
            week = int(record.growth.growth_cycle)
            if record.images.class_name == "녹차" and week not in selected:
                selected[week] = (name, images[record.images.imagepath])
        weeks = sorted(selected)[:SMOKE_COUNT]
        if len(weeks) != SMOKE_COUNT:
            parser.error("The source ZIP must contain at least five distinct green-tea weeks")
        gateway = CropClient()
        for week in weeks:
            source_member, image_member = selected[week]
            image = archive.read(image_member)
            prediction = gateway.predict(image, "private-source.jpg")
            observations.append(
                _Observation(
                    source_member=source_member,
                    source_week=week,
                    image_sha256=hashlib.sha256(image).hexdigest(),
                    image_bytes=len(image),
                    upload_filename=f"{prediction.run_id}.jpg",
                    prediction=prediction,
                )
            )
            _ = sys.stdout.write(
                f"week={week} status={prediction.status} run_id={prediction.run_id}\n"
            )
            _ = sys.stdout.flush()
    report = {
        "purpose": "connectivity_smoke_not_accuracy",
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "method": "POST",
        "url": f"{MODEL_BASE_URL}/predict",
        "multipart_fields": ["file"],
        "source_labels_sent": False,
        "selection": (
            "lexicographically first record in each of five lowest available distinct weeks"
        ),
        "available_weeks": sorted(selected),
        "openapi_sha256": hashlib.sha256(schema_raw.encode()).hexdigest(),
        "observations": [item.model_dump(mode="json") for item in observations],
    }
    _ = output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return int(any(item.prediction.status == "failed" for item in observations))


if __name__ == "__main__":
    raise SystemExit(main())
