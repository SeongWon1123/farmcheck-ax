# Copyright (c) 2026 FarmCheck contributors.
"""Real ASGI routes for the full manual path and isolated evidence snapshots."""

from pathlib import Path

from fastapi.testclient import TestClient

from farmcheck.app import create_app
from farmcheck.handover import HandoverDocument
from farmcheck.service import View
from farmcheck.source import load_catalog


def test_manual_workflow_and_frozen_exports(tmp_path: Path) -> None:
    """Run candidate, hold, correction, recheck and handover through real routes."""
    catalog = load_catalog("synthetic")
    candidate = next(
        record for record in catalog.records.values()
        if record.values["environment.ph"].decimal is not None
        and record.values["environment.ph"].decimal > 14
    )
    application = create_app(catalog, tmp_path / "api.sqlite", "synthetic", "")
    with TestClient(application) as client, TestClient(application) as other:
        assert client.get("/api/bootstrap").status_code == 200
        assert other.get("/api/bootstrap").status_code == 200
        route = f"/api/records/{candidate.id}"
        assert client.get(route).status_code == 200
        assert client.get(route + "/image").headers["content-type"].startswith("image/")
        held = client.post(route + "/review", json={
            "expected_revision": 0, "action": "hold",
            "note": "원문과 단위를 확인", "next_action": "측정 담당자에게 문의",
        })
        assert held.status_code == 200, held.text
        corrected = client.post(route + "/corrections", json={
            "expected_revision": 1, "field": "environment.ph", "value": "7.2",
            "reason": "입력 담당자가 확인한 값: 합성 테스트",
        })
        assert corrected.status_code == 200, corrected.text
        rechecked = client.post(route + "/recheck", json={"expected_revision": 2})
        assert rechecked.status_code == 200, rechecked.text
        assert client.post(route + "/review", json={
            "expected_revision": 0, "action": "reviewed",
        }).status_code == 409
        assert client.post(route + "/classify", json={"expected_revision": 3}).status_code == 503
        bootstrap = View.model_validate_json(client.get("/api/bootstrap").text)
        captured = client.post("/api/handovers", json={
            "expected_workspace_revision": bootstrap.root["workspace_revision"],
            "record_ids": None,
        })
        assert captured.status_code == 200, captured.text
        identifier = View.model_validate_json(captured.text).root["id"]
        url = f"/api/handovers/{identifier}/download?format=json"
        first = client.get(url)
        document = HandoverDocument.model_validate_json(first.text)
        assert candidate.id in document.scope_ids
        assert document.unresolved_count > 0
        assert any(record.next_action == "측정 담당자에게 문의" for record in document.records)
        assert client.post(route + "/review", json={
            "expected_revision": 3, "action": "reviewed",
        }).status_code == 200
        assert client.get(url).content == first.content
        assert other.get(url).status_code == 404
        assert View.model_validate_json(client.get(f"/api/handovers/{identifier}").text).root[
            "superseded_by_newer_revision"
        ] is True
        assert client.post("/api/agent/query", json={"query": "해결해"}).status_code == 503
        context = client.post("/api/agent/context", json={"record_ids": [candidate.id]})
        assert context.status_code == 200


def test_invalid_refs_mode_and_group_member_independence(tmp_path: Path) -> None:
    """Invalid references cannot resolve, and one correction cannot alter a peer."""
    catalog = load_catalog("synthetic")
    record = next(iter(catalog.records.values()))
    raw = record.raw_json
    application = create_app(catalog, tmp_path / "isolation.sqlite", "synthetic", "/demo")
    with TestClient(application) as client:
        assert client.get("/demo/").status_code == 200
        assert client.get("/demo/api/bootstrap?mode=green_tea").status_code == 200
        route = f"/demo/api/records/{record.id}"
        assert client.post(route + "/recheck", json={"expected_revision": 0}).status_code == 200
        assert client.post(route + "/review", json={
            "expected_revision": 1, "action": "resolve",
            "evidence_note": "거짓 근거", "evidence_refs": ["not-a-real-reference"],
        }).status_code == 422
        assert client.post(route + "/corrections", json={
            "expected_revision": 1, "field": "environment.ph",
            "value": "14.0000000000000001", "reason": "경계 주입",
        }).status_code == 200
        assert catalog.records[record.id].raw_json == raw
        assert client.get("/demo/api/records/no-such-record").status_code == 404
        bootstrap = View.model_validate_json(client.get("/demo/api/bootstrap").text)
        assert bootstrap.root["mode"] == "synthetic"
