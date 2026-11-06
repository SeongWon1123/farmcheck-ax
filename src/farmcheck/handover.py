# Copyright (c) 2026 FarmCheck contributors.
"""Deterministic exports from stored handover bytes, never live records."""

import csv
from html import escape
from io import StringIO
from typing import Literal, assert_never

from pydantic import Field, JsonValue

from farmcheck.store import FrozenModel, Snapshot


class CaptureInput(FrozenModel):
    """Capture an explicit scope at the workspace version the user reviewed."""

    expected_workspace_revision: int = Field(ge=0)
    record_ids: tuple[str, ...] | None = None


class HandoverRecord(FrozenModel):
    """Current record evidence and unresolved questions at capture time."""

    record_id: str
    origin: str
    revision: int
    data_revision: int
    review_state: str
    resolution_state: str
    next_action: str
    detail: dict[str, JsonValue]


class HandoverDocument(FrozenModel):
    """Immutable materialized scope and evidence."""

    schema_version: str = "1"
    workspace_revision: int
    rule_version: str
    created_at: str
    mode: str
    scope_ids: tuple[str, ...]
    scope_description: str
    records: tuple[HandoverRecord, ...]
    unresolved_count: int
    disclaimer: str = "사진 기록 검토 인수인계입니다. 작물 진단이나 독립적인 측정 횟수가 아닙니다."


class Export(FrozenModel):
    """An export generated only from frozen content."""

    body: bytes
    media_type: str
    extension: str


def safe_cell(value: str) -> str:
    """Prevent spreadsheet formula execution, including leading whitespace."""
    if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def export_snapshot(snapshot: Snapshot, format_name: Literal["json", "csv", "html"]) -> Export:
    """Export the same saved payload identically across future edits."""
    document = HandoverDocument.model_validate_json(snapshot.payload)
    match format_name:
        case "json":
            return Export(
                body=snapshot.payload.encode(),
                media_type="application/json",
                extension="json",
            )
        case "csv":
            stream = StringIO(newline="")
            writer = csv.writer(stream, lineterminator="\r\n")
            writer.writerow((
                "record_id", "origin", "revision", "data_revision",
                "review_state", "resolution_state", "next_action", "evidence_json",
            ))
            for record in document.records:
                writer.writerow((
                    safe_cell(record.record_id), safe_cell(record.origin),
                    record.revision, record.data_revision, record.review_state,
                    record.resolution_state, safe_cell(record.next_action),
                    safe_cell(record.model_dump_json()),
                ))
            return Export(
                body=("\ufeff" + stream.getvalue()).encode(),
                media_type="text/csv",
                extension="csv",
            )
        case "html":
            articles = "".join(
                "<article><h2>" + escape(record.record_id) + "</h2><p>"
                + escape(record.resolution_state) + " · " + escape(record.next_action)
                + "</p><pre>" + escape(record.model_dump_json(indent=2)) + "</pre></article>"
                for record in document.records
            )
            html = "".join((
                '<!doctype html><html lang="ko"><meta charset="utf-8">',
                '<meta name="viewport" content="width=device-width,initial-scale=1">',
                "<title>팜체크 인수인계</title><style>",
                "body{font:16px system-ui;max-width:960px;margin:32px auto;padding:16px}",
                "pre{white-space:pre-wrap;overflow-wrap:anywhere}",
                "article{border-top:1px solid #aaa}",
                "</style><h1>팜체크 인수인계 고정본</h1><p>",
                escape(document.disclaimer), "</p><p>미해결 ",
                str(document.unresolved_count), "개 사진 기록 · SHA256 ",
                snapshot.sha256, "</p>", articles, "</html>",
            ))
            return Export(body=html.encode(), media_type="text/html", extension="html")
    assert_never(format_name)
