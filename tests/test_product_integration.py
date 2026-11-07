# Copyright (c) 2026 FarmCheck contributors.
"""Private pair ownership, immutable source bytes and pinned inference receipts."""

import base64
import json
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import JsonValue

from farmcheck.app import create_app
from farmcheck.contracts import ModelComparison
from farmcheck.cropclient import (
    CropClient,
    CropOutput,
    GrowthOutput,
    PredictionFailure,
    PredictionSuccess,
)
from farmcheck.model_connection import connection_status, model_identity
from farmcheck.rules import canonical_decimal
from farmcheck.service import Service, View, json_object
from farmcheck.source import Catalog, load_catalog
from farmcheck.store import Store


def object_value(value: JsonValue) -> dict[str, JsonValue]:
    """Narrow an inspected JSON object before nested assertions."""
    assert isinstance(value, dict)
    return value


def list_value(value: JsonValue) -> list[JsonValue]:
    """Narrow an inspected JSON array before indexing."""
    assert isinstance(value, list)
    return value


def pair() -> tuple[dict[str, object], bytes]:
    """Build an immutable JPEG and annotation fixture."""
    image = BytesIO()
    Image.new("RGB", (380, 380), (40, 100, 30)).save(image, "JPEG")
    body = image.getvalue()
    raw = {
        "scenario": "injected_week_difference",
        "images": {
            "imagepath": "tea.jpg",
            "imagewidth": 1200,
            "imageheight": 900,
            "class_name": "녹차",
            "photo_date": "240803122557",
        },
        "environment": {
            "ph": "16",
            "ec": "0.7",
            "co2": "400",
            "temp": "30",
            "humidity": "77",
            "env_date": "2024-08-03 12:25:58",
        },
        "growth": {"growth_cycle": "4"},
        "caption": {"detail": "private label caption pH dS/m ppm ℃ %"},
    }
    return {
        "expected_workspace_revision": 0,
        "image_name": "tea.jpg",
        "annotation_name": "tea.json",
        "image_base64": base64.b64encode(body).decode(),
        "annotation_json": json.dumps(raw, ensure_ascii=False, indent=2),
    }, body


def clients(tmp_path: Path) -> tuple[TestClient, TestClient, Path]:
    """Create two isolated workspaces sharing one application database."""
    database = tmp_path / "private.sqlite"
    app = create_app(catalog=load_catalog("synthetic"), database=database, mode="synthetic")
    owner, other = TestClient(app), TestClient(app)
    assert owner.get("/api/bootstrap").json()["capabilities"]["import_pair"] is True
    _ = other.get("/api/bootstrap")
    return owner, other, database


def test_import_private_bytes_revision_and_scenario(tmp_path: Path) -> None:
    """Preserve imported source bytes and enforce workspace ownership."""
    owner, other, database = clients(tmp_path)
    payload, image = pair()
    result = owner.post("/api/imports", json=payload)
    assert result.status_code == 200
    data = View.model_validate_json(result.text).root
    identifier = data["record_id"]
    assert isinstance(identifier, str)
    assert data["record_revision"] == 0
    assert data["workspace_revision"] == 1
    assert object_value(data["source"])["image_sha256"] == sha256(image).hexdigest()
    assert (
        object_value(data["source"])["json_sha256"]
        == sha256(str(payload["annotation_json"]).encode()).hexdigest()
    )
    assert data["warnings"]
    detail = View.model_validate_json(owner.get(f"/api/records/{identifier}").text).root
    assert detail["origin"] == "uploaded"
    assert detail["scenario"] is None
    assert object_value(detail["source"])["synthetic"] is False
    assert object_value(detail["source"])["raw_json"] == payload["annotation_json"]
    assert object_value(detail["model"])["status"] == "none"
    served = owner.get(f"/api/records/{identifier}/image")
    assert served.content == image
    assert served.headers["cache-control"] == "private, no-store"
    for suffix in ("", "/image", "/model-runs"):
        response = other.get(f"/api/records/{identifier}{suffix}")
        assert response.status_code == 404
        assert response.headers["cache-control"] == "private, no-store"
    group_id = detail["group_id"]
    assert isinstance(group_id, str)
    assert other.get("/api/groups/" + group_id).status_code == 404
    for operation in ("review", "corrections", "recheck", "classify"):
        action: dict[str, JsonValue] = (
            {"expected_revision": 0, "action": "reviewed"}
            if operation == "review"
            else {"expected_revision": 0}
        )
        if operation == "corrections":
            action.update(field="environment.ph", value="7", reason="check")
        assert other.post(f"/api/records/{identifier}/{operation}", json=action).status_code == 404
    assert other.get("/api/records?filter=all").json()["total"] == 8
    assert owner.get("/api/records?filter=all").json()["total"] == 9
    assert owner.post("/api/imports", json=payload).status_code == 409
    payload["expected_workspace_revision"] = 1
    duplicate = owner.post("/api/imports", json=payload)
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "duplicate_import"
    assert owner.get("/api/bootstrap").json()["workspace_revision"] == 1
    workspace = owner.cookies.get("farmcheck_workspace")
    assert workspace is not None
    persisted = Store(database).imported_sources(Store(database).resolve_session(workspace))
    assert len(persisted) == 1
    assert persisted[identifier].raw_json == payload["annotation_json"]
    assert persisted[identifier].raw_hash == object_value(data["source"])["json_sha256"]
    assert persisted[identifier].actual_dimensions.width == 380
    assert persisted[identifier].values["environment.ph"].raw == "16"
    # Deduplication is never global; same source can be independently owned.
    payload["expected_workspace_revision"] = 0
    assert other.post("/api/imports", json=payload).status_code == 200


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("image_base64", "data:image/jpeg;base64,AAAA"),
        ("image_base64", "../private"),
        ("image_base64", "aW52YWxpZA=="),
        ("image_name", "../tea.jpg"),
        ("image_name", "C:\\tea.jpg"),
        ("image_name", "tea%2f.jpg"),
        ("annotation_name", "other.json"),
        ("annotation_json", '{"images":{"imagepath":"../tea.jpg"}}'),
        ("annotation_json", '{"images":{"imagepath":"other.jpg"}}'),
        ("annotation_json", '{"images":{},"images":{}}'),
        ("annotation_json", '{"images":{},"x":NaN}'),
        ("annotation_json", "x" * (1024 * 1024 + 1)),
        ("expected_workspace_revision", True),
    ],
)
def test_rejected_inputs_atomic(tmp_path: Path, field: str, value: object) -> None:
    """Reject malformed inputs without advancing workspace state."""
    owner, _, _ = clients(tmp_path)
    payload, _ = pair()
    payload[field] = value
    response = owner.post("/api/imports", json=payload)
    assert response.status_code == 422
    assert "image_base64" not in response.text
    assert "annotation_json" not in response.text
    assert owner.get("/api/bootstrap").json()["workspace_revision"] == 0
    assert owner.get("/api/records?filter=all").json()["total"] == 8


def test_exif_rejected_without_transcoding(tmp_path: Path) -> None:
    """Reject private image metadata without rewriting source bytes."""
    owner, _, _ = clients(tmp_path)
    payload, _ = pair()
    image = BytesIO()
    exif = Image.Exif()
    exif[0x010E] = "private metadata"
    Image.new("RGB", (20, 20)).save(image, "JPEG", exif=exif)
    payload["image_base64"] = base64.b64encode(image.getvalue()).decode()
    response = owner.post("/api/imports", json=payload)
    assert response.status_code == 422
    assert response.json()["code"] == "exif_not_allowed"
    assert owner.get("/api/bootstrap").json()["workspace_revision"] == 0


def test_real_inference_pinned_handover_and_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin complete inference provenance across handovers and restarts."""
    monkeypatch.setenv("FARM_MODEL_PROFILE", "pilot")
    owner, other, database = clients(tmp_path)
    payload, image = pair()
    record = View.model_validate_json(owner.post("/api/imports", json=payload).text).root[
        "record_id"
    ]

    def predict(_self: CropClient, body: bytes, filename: str) -> PredictionSuccess:
        assert body == image
        assert filename == "photo.jpg"
        return PredictionSuccess(
            run_id="actual-mock-run",
            http_status=200,
            raw_response='{"contract":"mock"}',
            width=380,
            height=380,
            crop=CropOutput(class_index=4, name_ko="녹차", name_en="green_tea", confidence=0.9),
            growth_week=GrowthOutput(class_index=4, label="4주차", confidence=0.8, num_classes=14),
        )

    monkeypatch.setattr(CropClient, "predict", predict)
    detail = View.model_validate_json(
        owner.post(f"/api/records/{record}/classify", json={"expected_revision": 0}).text
    ).root
    model = object_value(detail["model"])
    assert model["confidence"] == "0.9"
    assert model["crop_confidence"] == "0.9"
    assert model["growth_confidence"] == "0.8"
    assert model["execution_type"] == "real_inference"
    assert model["model_identity"] == model_identity()
    assert model["image_sha256"] == sha256(image).hexdigest()
    assert model["based_on_revision"] == 1
    elapsed_ms = model["elapsed_ms"]
    assert isinstance(elapsed_ms, (int, float))
    assert elapsed_ms >= 0
    assert detail["revision"] == 2
    assert detail["review_state"] == "needs_review"
    runs = View.model_validate_json(owner.get(f"/api/records/{record}/model-runs").text).root
    run = object_value(list_value(runs["runs"])[0])
    assert object_value(run["result"])["model_identity"] == model_identity()
    assert object_value(run["result"])["json_sha256"] == model["json_sha256"]
    workspace_revision = detail["workspace_revision"]
    captured = View.model_validate_json(owner.post(
        "/api/handovers",
        json={"expected_workspace_revision": workspace_revision, "record_ids": [record]},
    ).text).root
    snapshot_id = captured["id"]
    url = f"/api/handovers/{snapshot_id}/download?format=json"
    original = owner.get(url).content
    assert sha256(original).hexdigest() == captured["sha256"]
    assert (
        json.loads(original)["records"][0]["detail"]["model"]["model_identity"] == model_identity()
    )
    assert owner.get("/api/handovers").json()["items"][0]["snapshot_id"] == snapshot_id
    assert other.get("/api/handovers").json()["items"] == []
    assert other.get(url).status_code == 404
    assert other.get(f"/api/handovers/{snapshot_id}").status_code == 404
    assert (
        other.post(
            "/api/handovers", json={"expected_workspace_revision": 0, "record_ids": [record]}
        ).status_code
        == 404
    )
    monkeypatch.setenv("FARM_MODEL_PROFILE", "provided")
    _ = owner.post(
        f"/api/records/{record}/corrections",
        json={
            "expected_revision": 2,
            "field": "environment.ph",
            "value": "7",
            "reason": "human check",
        },
    )
    assert owner.get(url).content == original
    restarted = TestClient(create_app(database=database, mode="synthetic"))
    restarted.cookies.update(owner.cookies)
    assert restarted.get(url).content == original
    assert (
        restarted.get(f"/api/records/{record}").json()["model"]["model_identity"]["profile"]
        == "pilot"
    )
    assert restarted.get(f"/api/records/{record}/image").content == image


def test_synthetic_remains_unavailable(tmp_path: Path) -> None:
    """Keep synthetic image inference unavailable."""
    owner, _, _ = clients(tmp_path)
    records = View.model_validate_json(owner.get("/api/records?filter=all").text).root
    record = object_value(list_value(records["items"])[0])["id"]
    assert owner.get(f"/api/records/{record}").json()["source"]["synthetic"] is True
    assert (
        owner.post(f"/api/records/{record}/classify", json={"expected_revision": 0}).status_code
        == 503
    )


def test_unknown_profile_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject unknown server model profiles."""
    monkeypatch.setenv("FARM_MODEL_PROFILE", "https://attacker.invalid")
    with pytest.raises(ValueError, match="FARM_MODEL_PROFILE must be provided or pilot"):
        _ = model_identity()


def test_pilot_multipart_has_only_opaque_image(monkeypatch: pytest.MonkeyPatch) -> None:
    """Send only opaque image bytes to the server-selected model."""
    monkeypatch.setenv("FARM_MODEL_PROFILE", "pilot")
    _, image = pair()

    def respond(request: httpx2.Request) -> httpx2.Response:
        assert (
            str(request.url) == "https://aiopen-serving.jntp-ax.or.kr/jntp-cpu/SV-9e5ca608/predict"
        )
        body = request.read()
        assert body.count(b'name="file"') == 1
        assert b"private-path" not in body
        assert b"private label caption" not in body
        assert b"annotation_json" not in body
        assert image in body
        return httpx2.Response(
            200,
            json={
                "width": 380,
                "height": 380,
                "crop": {
                    "class_index": 4,
                    "name_ko": "녹차",
                    "name_en": "green_tea",
                    "confidence": 0.9,
                },
                "growth_week": {
                    "class_index": 4,
                    "label": "4주차",
                    "confidence": 0.8,
                    "num_classes": 14,
                },
            },
        )

    result = CropClient(transport=httpx2.MockTransport(respond)).predict(
        image, "private-path/tea.jpg"
    )
    assert result.status == "success"


def test_image_byte_and_pixel_limits(tmp_path: Path) -> None:
    """Enforce image byte and pixel limits before persistence."""
    owner, _, _ = clients(tmp_path)
    payload, _ = pair()
    payload["image_base64"] = base64.b64encode(b"x" * (10 * 1024 * 1024 + 1)).decode()
    assert owner.post("/api/imports", json=payload).status_code in {413, 422}
    image = BytesIO()
    Image.new("RGB", (4001, 4000)).save(image, "JPEG")
    payload["image_base64"] = base64.b64encode(image.getvalue()).decode()
    result = owner.post("/api/imports", json=payload)
    assert result.status_code == 422
    assert result.json()["code"] == "image_format_or_dimensions"
    assert owner.get("/api/bootstrap").json()["workspace_revision"] == 0


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('{"status":"ready"}', "ready"),
        ('{"status":"ok"}', "ready"),
        ('{"status":"not_ready"}', "unavailable"),
        ("<html>login</html>", "unavailable"),
        ('{"status":[]}', "unavailable"),
        ("x" * 65537, "unavailable"),
    ],
)
def test_health_validated_without_raw_echo(
    monkeypatch: pytest.MonkeyPatch,
    body: str,
    expected: str,
) -> None:
    """Accept only bounded ready or ok health JSON without echoing it."""
    original_client = httpx2.Client

    def factory(**_kwargs: object) -> httpx2.Client:
        return original_client(
            transport=httpx2.MockTransport(
                lambda _request: httpx2.Response(200, content=body.encode()),
            )
        )

    monkeypatch.setattr(httpx2, "Client", factory)
    status = connection_status()
    assert status["status"] == expected
    assert "raw_response" not in status
    if expected == "unavailable":
        assert status["error"] == "invalid_health"


@pytest.mark.parametrize("value", ["1" + "0" * 900000, "9" * 257, "1e1025", "1e9999999"])
def test_numeric_strings_rejected_before_normalization(tmp_path: Path, value: str) -> None:
    """Bound numeric strings before expensive normalization."""
    owner, _, _ = clients(tmp_path)
    payload, _ = pair()
    annotation = View.model_validate_json(str(payload["annotation_json"])).root
    object_value(annotation["environment"])["ph"] = value
    payload["annotation_json"] = json.dumps(annotation, ensure_ascii=False)
    result = owner.post("/api/imports", json=payload)
    assert result.status_code == 422
    assert result.json()["code"] == "invalid_json"
    assert owner.get("/api/bootstrap").json()["workspace_revision"] == 0


def test_numeric_json_tokens_bounded_before_decimal(tmp_path: Path) -> None:
    """Bound original JSON numeric tokens before Decimal construction."""
    owner, _, _ = clients(tmp_path)
    payload, _ = pair()
    payload["annotation_json"] = str(payload["annotation_json"]).replace(
        '"16"', "1" + "0" * 900000 + ".0"
    )
    result = owner.post("/api/imports", json=payload)
    assert result.status_code == 422
    assert result.json()["code"] == "invalid_json"
    assert owner.get("/api/bootstrap").json()["workspace_revision"] == 0


@pytest.mark.parametrize(
    "token", ["0", "-0.000", "14", "14.000", "-1200.000", "0.00120", "1.2E+15"]
)
def test_linear_canonical_decimal_preserves_existing_ids(token: str) -> None:
    """Preserve canonical identifiers while trimming zeros linearly."""
    value = Decimal(token)
    if value == 0:
        expected = "0"
    else:
        sign, digits, exponent = value.as_tuple()
        assert isinstance(exponent, int)
        text = "".join(str(digit) for digit in digits)
        while text.endswith("0"):
            text = text[:-1]
            exponent += 1
        expected = f"{sign}:{text}:{exponent}"
    assert canonical_decimal(value) == expected


def test_canonical_large_trailing_zeros_linear() -> None:
    """Canonicalize large runs of trailing zeros without quadratic work."""
    assert canonical_decimal(Decimal("1" + "0" * 10000)) == "0:1:10000"


def test_server_catalog_provenance_sets_real_image_flag(tmp_path: Path) -> None:
    """Derive real image capability from server-owned provenance."""
    public = load_catalog("synthetic")
    source = next(iter(public.records.values())).model_copy(
        update={
            "origin": "actual",
            "scenario": "injected_week_difference",
        }
    )
    # An explicit server-owned ZIP capability, never an annotation imagepath or scenario.
    catalog = Catalog(
        {source.id: source}, public.inventory, zip_path=tmp_path / "server-owned-fixture.zip"
    )
    store = Store(tmp_path / "server-catalog.sqlite")
    workspace = store.workspace(None)
    service = Service(catalog, store, "green_tea")
    detail = service.detail(source.id, store.read(workspace)).root
    assert object_value(detail["source"])["synthetic"] is False
    assert object_value(detail["model"])["status"] == "none"
    svg_service = Service(public, store, "green_tea")
    svg_detail = svg_service.detail(source.id, store.read(workspace)).root
    assert object_value(svg_detail["source"])["synthetic"] is True


def test_failed_retry_preserves_adopted_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the adopted result comparable while exposing a failed retry."""
    # Given an uploaded record with an adopted historical result.
    owner, _, database = clients(tmp_path)
    payload, _ = pair()
    imported = View.model_validate_json(owner.post("/api/imports", json=payload).text).root
    identifier = imported["record_id"]
    assert isinstance(identifier, str)
    session = owner.cookies.get("farmcheck_workspace")
    assert session is not None
    store = Store(database)
    workspace = store.resolve_session(session)
    adopted = ModelComparison(
        status="available", run_id="prior-success", crop="녹 차", growth_week=4,
        confidence=Decimal("0.91"),
    )
    _ = store.mutate(
        workspace, identifier, 0,
        lambda state: state.model_copy(update={
            "revision": state.revision + 1,
            "model": {
                key: value for key, value in json_object(adopted).items()
                if key not in {"crop_confidence", "growth_confidence"}
            },
        }),
    )

    def fail(_self: CropClient, _body: bytes, _filename: str) -> PredictionFailure:
        return PredictionFailure(run_id="failed-retry", error="timeout")

    monkeypatch.setattr(CropClient, "predict", fail)
    # When a subsequent inference attempt fails.
    response = owner.post(f"/api/records/{identifier}/classify", json={"expected_revision": 1})
    assert response.status_code == 200
    detail = View.model_validate_json(response.text).root
    # Then the adopted comparison and the failure have independent identities.
    model = object_value(detail["model"])
    assert model["status"] == "ok"
    assert model["run_id"] == "prior-success"
    assert model["confidence"] == "0.91"
    assert model["crop_confidence"] is None
    assert model.get("growth_confidence") is None
    attempt = object_value(detail["model_attempt"])
    assert attempt["status"] == "failed"
    assert attempt["run_id"] == "failed-retry"
    assert detail["comparison"] == {
        "status": "equal", "current_week": "4", "model_week": "4",
        "week_matches": True, "crop_matches": True,
    }


def test_failed_first_attempt_remains_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Present a failed first attempt without inventing adopted evidence."""
    # Given an uploaded record with no adopted model result.
    owner, _, _ = clients(tmp_path)
    payload, _ = pair()
    imported = View.model_validate_json(owner.post("/api/imports", json=payload).text).root
    identifier = imported["record_id"]
    assert isinstance(identifier, str)

    def fail(_self: CropClient, _body: bytes, _filename: str) -> PredictionFailure:
        return PredictionFailure(run_id="first-failure", error="timeout")

    monkeypatch.setattr(CropClient, "predict", fail)
    # When the first attempt fails.
    response = owner.post(f"/api/records/{identifier}/classify", json={"expected_revision": 0})
    assert response.status_code == 200
    detail = View.model_validate_json(response.text).root
    # Then failure is visible and comparison stays unavailable.
    assert object_value(detail["model"])["status"] == "failed"
    assert object_value(detail["model_attempt"])["run_id"] == "first-failure"
    assert detail["comparison"] == {
        "status": "unavailable", "current_week": "4", "model_week": None,
        "week_matches": None, "crop_matches": None,
    }


@pytest.mark.parametrize("week", ["3", "3.0000000000000001", "not-a-number"])
def test_comparison_uses_exact_effective_week(tmp_path: Path, week: str) -> None:
    """Compare adopted weeks without JavaScript or binary float rounding."""
    # Given an effective week and a model result with integer week three.
    catalog = load_catalog("synthetic")
    source = next(iter(catalog.records.values()))
    store = Store(tmp_path / "comparison.sqlite")
    workspace = store.workspace(None)
    model = ModelComparison(status="available", run_id="exact", crop="녹_차", growth_week=3)
    _ = store.mutate(
        workspace, source.id, 0,
        lambda state: state.model_copy(update={
            "revision": state.revision + 1,
            "model": json_object(model), "overlays": {"growth.growth_cycle": week},
        }),
    )
    # When the detail presentation is materialized.
    detail = Service(catalog, store, "synthetic").detail(source.id, store.read(workspace)).root
    # Then exact equality and unavailability are preserved.
    comparison = object_value(detail["comparison"])
    expected = {"3": "equal", "3.0000000000000001": "different", "not-a-number": "unavailable"}
    assert comparison["status"] == expected[week]
    assert comparison["current_week"] == (None if week == "not-a-number" else week)
    assert comparison["model_week"] == "3"
    assert comparison["week_matches"] == (None if week == "not-a-number" else week == "3")
    assert comparison["crop_matches"] is True


def test_peers_use_effective_normalized_crop(tmp_path: Path) -> None:
    """Exclude other crops even when their week and units match."""
    # Given mixed crops and a correction to the current crop name.
    public = load_catalog("synthetic")
    original = next(iter(public.records.values()))
    current = original.model_copy(update={"id": "current"})
    same = original.model_copy(update={"id": "same", "crop": "토_마토"})
    other = original.model_copy(update={"id": "other", "crop": "녹차"})
    catalog = Catalog({item.id: item for item in (current, same, other)}, public.inventory)
    store = Store(tmp_path / "peers.sqlite")
    workspace = store.workspace(None)
    _ = store.mutate(
        workspace, current.id, 0,
        lambda state: state.model_copy(update={
            "revision": state.revision + 1, "overlays": {"images.class_name": "토 마토"},
        }),
    )
    # When same-week peers are presented.
    detail = Service(catalog, store, "synthetic").detail(current.id, store.read(workspace)).root
    # Then only the normalized matching crop is included with its crop label.
    peers = [object_value(item) for item in list_value(detail["peers"])]
    assert [item["id"] for item in peers] == ["same"]
    assert peers[0]["crop"] == "토_마토"


def test_record_scope_isolates_counts_search_and_pagination(tmp_path: Path) -> None:
    """Count the chosen scope before filters without leaking private imports."""
    # Given real server records, synthetic fixtures and one workspace-owned import.
    public = load_catalog("synthetic")
    original = next(iter(public.records.values()))
    actual = original.model_copy(update={"id": "server-actual", "origin": "actual"})
    catalog = Catalog({actual.id: actual, **public.records}, public.inventory)
    app = create_app(catalog, tmp_path / "scope.sqlite", "synthetic", "")
    owner, other = TestClient(app), TestClient(app)
    _ = owner.get("/api/bootstrap")
    _ = other.get("/api/bootstrap")
    payload, _ = pair()
    imported = View.model_validate_json(owner.post("/api/imports", json=payload).text).root
    identifier = imported["record_id"]
    corrected = owner.post(f"/api/records/{identifier}/corrections", json={
        "expected_revision": 0, "field": "environment.ph", "value": "17", "reason": "check",
    })
    assert corrected.status_code == 200
    held = owner.post(f"/api/records/{identifier}/review", json={
        "expected_revision": 1, "action": "hold", "note": "check", "next_action": "ask",
    })
    assert held.status_code == 200
    # When listing filtered, searched and paginated actual records.
    result = View.model_validate_json(owner.get(
        "/api/records?scope=actual&filter=held&page_size=1",
    ).text).root
    # Then counts describe the full scope and rows only the selected scope.
    assert result["total"] == 1
    assert object_value(list_value(result["items"])[0])["id"] == identifier
    assert result["summary"] == {
        "total": 2, "candidates": 2, "held": 1, "needs_review": 1, "resolved": 0,
    }
    empty = View.model_validate_json(owner.get(
        "/api/records?scope=actual&filter=all&q=does-not-exist&page=2&page_size=1",
    ).text).root
    assert empty["total"] == 0
    assert empty["items"] == []
    assert empty["summary"] == result["summary"]
    second = View.model_validate_json(owner.get(
        "/api/records?scope=actual&filter=all&page=2&page_size=1",
    ).text).root
    assert second["total"] == 2
    assert object_value(list_value(second["items"])[0])["id"] == identifier
    demo = View.model_validate_json(owner.get("/api/records?scope=demo&filter=all").text).root
    assert demo["total"] == 8
    assert object_value(demo["summary"])["total"] == 8
    assert all(
        object_value(item)["origin"] in {"synthetic", "injected"}
        for item in list_value(demo["items"])
    )
    private = View.model_validate_json(other.get("/api/records?scope=actual&filter=all").text).root
    assert private["total"] == 1
    assert object_value(private["summary"])["total"] == 1
    assert owner.get("/api/records?filter=all").json()["total"] == 10
    assert owner.get("/api/records?scope=unknown").status_code == 422
