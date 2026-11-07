# Copyright (c) 2026 FarmCheck contributors.
"""Public report ownership keys must never be reusable bearer credentials."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from farmcheck.app import create_app
from farmcheck.service import View
from farmcheck.store import Store
from tests.test_product_integration import clients, object_value, pair

if TYPE_CHECKING:
    from collections.abc import Callable


def test_public_report_ids_are_not_credentials(tmp_path: Path) -> None:
    """Keep credentials out of exports and reject public IDs at every boundary."""
    owner, _, database = clients(tmp_path)
    payload, _ = pair()
    imported = owner.post("/api/imports", json=payload)
    assert imported.status_code == 200
    record = View.model_validate_json(imported.text).root["record_id"]
    detail = owner.get(f"/api/records/{record}")
    assert detail.status_code == 200
    credential = owner.cookies.get("farmcheck_workspace")
    assert credential is not None
    store = Store(database)
    public_id = store.resolve_session(credential)
    source = object_value(View.model_validate_json(detail.text).root["source"])
    assert bool(credential != public_id)
    for key in ("dataset_id", "source_group_id"):
        assert bool(credential not in str(source[key]))
        assert public_id in str(source[key])
    assert bool(credential not in detail.text)
    captured = owner.post("/api/handovers", json={"expected_workspace_revision": 1})
    assert captured.status_code == 200
    assert bool(credential not in captured.text)
    snapshot = View.model_validate_json(captured.text).root["id"]
    report_url = f"/api/handovers/{snapshot}"
    assert bool(credential not in owner.get(report_url).text)
    for format_name in ("json", "csv", "html"):
        response = owner.get(report_url + "/download?format=" + format_name)
        assert response.status_code == 200
        assert bool(credential not in response.text)
        if format_name == "json":
            assert View.model_validate_json(response.text).root.get("workspace_id") in (
                None, public_id,
            )
    forged = TestClient(create_app(database=database, mode="synthetic"))
    forged.cookies.set("farmcheck_workspace", public_id)
    for route in (
        f"/api/records/{record}", f"/api/records/{record}/image", report_url,
        report_url + "/download", "/api/records", "/api/handovers",
    ):
        assert forged.get(route).status_code == 401
    assert forged.get("/api/bootstrap").status_code == 200
    assert forged.get(f"/api/records/{record}").status_code == 404
    assert forged.get(report_url).status_code == 404
    with sqlite3.connect(database) as connection:
        fetch: Callable[[], list[tuple[str]]] = connection.execute(
            "SELECT token_hash FROM workspace_sessions",
        ).fetchall
        rows = fetch()
    assert (sha256(credential.encode()).hexdigest(),) in rows
    assert all(bool(credential not in row[0]) for row in rows)
    assert owner.get("/api/bootstrap").status_code == 200
    assert bool(owner.cookies.get("farmcheck_workspace") == credential)


def test_legacy_bootstrap_rotates_without_rewriting_data(tmp_path: Path) -> None:
    """Claim only pre-upgrade owners and preserve pinned bytes across rotation."""
    owner, _, database = clients(tmp_path)
    payload, image = pair()
    imported = View.model_validate_json(owner.post("/api/imports", json=payload).text).root
    record = imported["record_id"]
    assert owner.post(f"/api/records/{record}/review", json={
        "expected_revision": 0, "action": "reviewed", "note": "preserved review",
    }).status_code == 200
    captured = View.model_validate_json(owner.post("/api/handovers", json={
        "expected_workspace_revision": 2,
    }).text).root
    report_url = f"/api/handovers/{captured['id']}"
    download = report_url + "/download?format=json"
    original = owner.get(download).content
    credential = owner.cookies.get("farmcheck_workspace")
    assert credential is not None
    legacy_id = Store(database).resolve_session(credential)
    with sqlite3.connect(database) as connection:
        _ = connection.execute("DROP TABLE legacy_workspace_claims")
        _ = connection.execute("DROP TABLE workspace_sessions")
    upgraded = create_app(database=database, mode="synthetic")
    client = TestClient(upgraded)
    client.cookies.set("farmcheck_workspace", legacy_id, domain="testserver.local")
    assert client.get(download).status_code == 401
    bootstrap = client.get("/api/bootstrap")
    assert bootstrap.status_code == 200
    assert bootstrap.json()["workspace_revision"] == 2
    rotated = client.cookies.get("farmcheck_workspace")
    assert rotated is not None
    assert bool(rotated != legacy_id)
    assert bool(rotated not in bootstrap.text)
    assert client.get(download).content == original
    assert client.get(report_url).json()["sha256"] == captured["sha256"]
    detail = client.get(f"/api/records/{record}")
    assert detail.status_code == 200
    assert detail.json()["revision"] == 1
    assert client.get(f"/api/records/{record}/image").content == image
    assert bool(rotated not in detail.text)
    attacker = TestClient(create_app(database=database, mode="synthetic"))
    attacker.cookies.set("farmcheck_workspace", legacy_id)
    for route in (f"/api/records/{record}", f"/api/records/{record}/image", download):
        assert attacker.get(route).status_code == 401
    assert attacker.get("/api/bootstrap").status_code == 200
    assert attacker.get(download).status_code == 404
    restarted = TestClient(create_app(database=database, mode="synthetic"))
    restarted.cookies.update(client.cookies)
    assert restarted.get(download).content == original


def test_only_one_concurrent_legacy_claim_succeeds(tmp_path: Path) -> None:
    """Serialize legacy claims so a public ID cannot mint multiple sessions."""
    database = tmp_path / "legacy.sqlite"
    with sqlite3.connect(database) as connection:
        _ = connection.execute(
            "CREATE TABLE workspaces(id TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0)",
        )
        _ = connection.execute("INSERT INTO workspaces(id) VALUES('legacy-public-id')")
    store = Store(database)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(store.bootstrap_session, ["legacy-public-id"] * 2))
    assert sum(owner == "legacy-public-id" for owner, _credential in results) == 1
    assert all(bool(owner != credential) for owner, credential in results)
