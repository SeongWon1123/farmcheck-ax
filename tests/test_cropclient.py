# Copyright (c) 2026 Farmcheck contributors.
"""Deterministic HTTP-boundary tests, without vendor calls or timing luck."""

from io import BytesIO

import httpx2
import pytest
from PIL import Image

from farmcheck.cropclient import (
    MAX_IMAGE_BYTES,
    MAX_RESPONSE_BYTES,
    MODEL_BASE_URL,
    CropClient,
    PredictionFailure,
    PredictionSuccess,
)


def image_bytes(size: tuple[int, int] = (12, 8)) -> bytes:
    """Build a real, small deterministic JPEG."""
    with BytesIO() as buffer:
        Image.new("RGB", size, "green").save(buffer, format="JPEG")
        return buffer.getvalue()


def vendor_reply() -> str:
    """Build a schema-shaped response with independently known fixture dimensions."""
    return (
        '{"width":12,"height":8,"crop":{"class_index":4,"name_ko":"녹차",'
        '"name_en":"green_tea","confidence":0.9,"probabilities":[]},'
        '"growth_week":{"class_index":3,"label":"3주차","confidence":0.8,'
        '"num_classes":14,"probabilities":[]}}'
    )


def test_predict_sends_only_opaque_image_when_source_filename_has_labels() -> None:
    """Given source labels, when classifying, then only an opaque file leaves."""
    # Given
    photo = image_bytes()
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, text=vendor_reply())

    client = CropClient(transport=httpx2.MockTransport(handle))
    # When
    result = client.predict(photo, "녹차_3주차_private.jpg")
    # Then
    assert isinstance(result, PredictionSuccess)
    assert result.growth_week.class_index == 3
    assert result.raw_response == vendor_reply()
    request = requests[0]
    assert str(request.url) == f"{MODEL_BASE_URL}/predict"
    assert request.method == "POST"
    assert request.content.count(b'Content-Disposition: form-data; name="file"') == 1
    assert f'filename="{result.run_id}.jpg"'.encode() in request.content
    assert "녹차".encode() not in request.content
    assert b"3" + "주차".encode() not in request.content
    assert photo in request.content


@pytest.mark.parametrize("status", [413, 422, 429, 500, 302])
def test_predict_fails_when_vendor_returns_error(status: int) -> None:
    """Given an HTTP error, when predicting, then preserve failure evidence."""
    # Given
    client = CropClient(
        transport=httpx2.MockTransport(
            lambda _: httpx2.Response(
                status, text='{"detail":"busy"}', headers={"Retry-After": "1"}
            )
        )
    )
    # When
    result = client.predict(image_bytes(), "ignored.jpg")
    # Then
    assert isinstance(result, PredictionFailure)
    assert (result.http_status, result.error, result.retry_after) == (status, "http_error", "1")
    assert result.raw_response == '{"detail":"busy"}'


@pytest.mark.parametrize(
    "reply",
    [
        "not JSON",
        "{}",
        vendor_reply().replace('"confidence":0.9', '"confidence":2'),
        vendor_reply().replace('"class_index":3', '"class_index":14'),
        vendor_reply().replace('"width":12', '"width":13'),
        vendor_reply().replace('"num_classes":14', '"num_classes":3'),
    ],
)
def test_predict_fails_when_success_reply_is_invalid(reply: str) -> None:
    """Given invalid vendor data, when parsing, then never invent success."""
    # Given
    client = CropClient(transport=httpx2.MockTransport(lambda _: httpx2.Response(200, text=reply)))
    # When
    result = client.predict(image_bytes(), "ignored.jpg")
    # Then
    assert isinstance(result, PredictionFailure)
    assert result.error == "invalid_response"
    assert result.raw_response == reply


@pytest.mark.parametrize("error", [httpx2.ReadTimeout, httpx2.ConnectError])
def test_predict_fails_when_transport_raises(error: type[httpx2.RequestError]) -> None:
    """Given transport failure, when predicting, then return an explicit outcome."""
    # Given
    def handle(request: httpx2.Request) -> httpx2.Response:
        message = "deterministic transport failure"
        raise error(message, request=request)

    client = CropClient(transport=httpx2.MockTransport(handle))
    # When
    result = client.predict(image_bytes(), "ignored.jpg")
    # Then
    assert isinstance(result, PredictionFailure)
    assert result.error == ("timeout" if error is httpx2.ReadTimeout else "transport_error")
    assert result.http_status is None


@pytest.mark.parametrize("photo", [b"", b"not image", b"x" * (MAX_IMAGE_BYTES + 1)])
def test_predict_rejects_input_before_request_when_image_is_invalid(photo: bytes) -> None:
    """Given invalid bytes, when predicting, then no request is made."""
    # Given
    def handle(_: httpx2.Request) -> httpx2.Response:
        pytest.fail("Invalid image reached transport")

    client = CropClient(transport=httpx2.MockTransport(handle))
    # When
    result = client.predict(photo, "ignored.jpg")
    # Then
    assert isinstance(result, PredictionFailure)
    assert result.http_status is None


def test_predict_rejects_when_pixel_limit_exceeded() -> None:
    """Given an over-limit image, when predicting, then reject locally."""
    # Given
    photo = image_bytes((4001, 4000))
    client = CropClient(transport=httpx2.MockTransport(lambda _: pytest.fail("Unexpected request")))
    # When
    result = client.predict(photo, "ignored.jpg")
    # Then
    assert isinstance(result, PredictionFailure)
    assert result.error == "image_format_or_dimensions"


def test_predict_rejects_when_image_has_multiple_frames() -> None:
    """Given animated PNG, when predicting, then reject locally."""
    # Given
    with BytesIO() as buffer:
        Image.new("RGB", (2, 2), "red").save(
            buffer, format="PNG", save_all=True, append_images=[Image.new("RGB", (2, 2), "blue")]
        )
        photo = buffer.getvalue()
    client = CropClient(transport=httpx2.MockTransport(lambda _: pytest.fail("Unexpected request")))
    # When
    result = client.predict(photo, "ignored.png")
    # Then
    assert isinstance(result, PredictionFailure)
    assert result.error == "image_format_or_dimensions"


def test_predict_fails_when_response_exceeds_bound() -> None:
    """Given excess response bytes, when receiving, then stop with explicit failure."""
    # Given
    client = CropClient(
        transport=httpx2.MockTransport(
            lambda _: httpx2.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1))
        )
    )
    # When
    result = client.predict(image_bytes(), "ignored.jpg")
    # Then
    assert isinstance(result, PredictionFailure)
    assert result.error == "response_too_large"
