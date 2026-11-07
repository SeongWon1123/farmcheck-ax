# Copyright (c) 2026 Farmcheck contributors.
"""Bounded, read-only adapter for the fixed crop classification service."""

from http import HTTPStatus
from io import BytesIO
from typing import ClassVar, Final, Literal
from uuid import uuid4

import httpx2
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from farmcheck.model_connection import model_base_url, model_identity

MODEL_BASE_URL: Final = "https://aiopen-serving.jntp-ax.or.kr/jntp-cpu/SV-c562094f"
MAX_IMAGE_BYTES: Final = 10 * 1024 * 1024
MAX_PIXELS: Final = 16_000_000
MAX_RESPONSE_BYTES: Final = 1024 * 1024
_FORMATS: Final = {
    "JPEG": ("jpg", "image/jpeg"),
    "PNG": ("png", "image/png"),
    "WEBP": ("webp", "image/webp"),
    "BMP": ("bmp", "image/bmp"),
}
_CLASS_WEEKS: Final = (3, 6, 6, 9, 14, 9, 3, 16, 16)


class CropOutput(BaseModel):
    """Validated essentials of the vendor crop result; full reply stays raw."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, strict=True, allow_inf_nan=False)
    class_index: int = Field(ge=0, le=8)
    name_ko: str = Field(min_length=1)
    name_en: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class GrowthOutput(BaseModel):
    """Validated crop-specific week classifier output."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, strict=True, allow_inf_nan=False)
    class_index: int = Field(ge=0)
    label: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    num_classes: int = Field(ge=1, le=16)


class _VendorOutput(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, strict=True)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    crop: CropOutput
    growth_week: GrowthOutput


class PredictionSuccess(_VendorOutput):
    """Successful inference, not adoption, review, or proof of correctness."""

    status: Literal["success"] = "success"
    run_id: str
    http_status: int
    raw_response: str


class PredictionFailure(BaseModel):
    """Explicit input, transport, HTTP, or response-contract failure."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    status: Literal["failed"] = "failed"
    run_id: str
    http_status: int | None = None
    raw_response: str = ""
    error: str
    retry_after: str | None = None


type Prediction = PredictionSuccess | PredictionFailure


class CropClient:
    """One bounded synchronous call; owns its HTTP resources and never writes state."""

    _transport: httpx2.BaseTransport | None

    def __init__(self, *, transport: httpx2.BaseTransport | None = None) -> None:
        """Allow deterministic transport injection, never an alternate URL."""
        self._transport = transport
        self.identity: dict[str, str] = model_identity()

    def predict(self, image: bytes, filename: str) -> Prediction:
        """Send only validated image bytes under an opaque generated filename."""
        del filename  # Source paths, crop labels, and week labels must never leave this boundary.
        run_id = str(uuid4())
        if not image or len(image) > MAX_IMAGE_BYTES:
            return PredictionFailure(run_id=run_id, error="image_size")
        try:
            with Image.open(BytesIO(image)) as photo:
                width, height = photo.size
                image_format = photo.format or ""
                if (
                    width * height > MAX_PIXELS
                    or getattr(photo, "n_frames", 1) != 1
                    or image_format not in _FORMATS
                ):
                    return PredictionFailure(run_id=run_id, error="image_format_or_dimensions")
                photo.verify()
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
            return PredictionFailure(run_id=run_id, error="invalid_image")

        prediction = self._request(image, (run_id, image_format))
        match prediction:
            case PredictionSuccess() if (
                prediction.width != width
                or prediction.height != height
                or prediction.growth_week.num_classes
                != _CLASS_WEEKS[prediction.crop.class_index]
                or prediction.growth_week.class_index >= prediction.growth_week.num_classes
                or prediction.growth_week.label != f"{prediction.growth_week.class_index}주차"
            ):
                return PredictionFailure(
                    run_id=run_id,
                    http_status=HTTPStatus.OK,
                    raw_response=prediction.raw_response,
                    error="invalid_response",
                )
            case PredictionSuccess() | PredictionFailure():
                return prediction

    def _request(self, image: bytes, upload: tuple[str, str]) -> Prediction:
        """Own the HTTP boundary separately from local image validation."""
        run_id, image_format = upload
        extension, content_type = _FORMATS[image_format]
        try:
            with (
                httpx2.Client(
                    timeout=httpx2.Timeout(connect=5, read=60, write=15, pool=5),
                    limits=httpx2.Limits(max_connections=2, max_keepalive_connections=1),
                    transport=self._transport,
                    follow_redirects=False,
                    trust_env=False,
                ) as client,
                client.stream(
                    "POST",
                    f"{model_base_url(self.identity)}/predict",
                    files={"file": (f"{run_id}.{extension}", image, content_type)},
                ) as response,
            ):
                chunks = bytearray()
                for chunk in response.iter_bytes():
                    if len(chunks) + len(chunk) > MAX_RESPONSE_BYTES:
                        return PredictionFailure(
                            run_id=run_id,
                            http_status=response.status_code,
                            error="response_too_large",
                        )
                    chunks.extend(chunk)
                raw = bytes(chunks).decode("utf-8", errors="replace")
                if response.status_code != HTTPStatus.OK:
                    return PredictionFailure(
                        run_id=run_id,
                        http_status=response.status_code,
                        raw_response=raw,
                        error="http_error",
                        retry_after=response.headers.get("Retry-After"),
                    )
        except httpx2.TimeoutException:
            return PredictionFailure(run_id=run_id, error="timeout")
        except httpx2.RequestError:
            return PredictionFailure(run_id=run_id, error="transport_error")
        try:
            parsed = _VendorOutput.model_validate_json(raw)
        except ValidationError:
            return PredictionFailure(
                run_id=run_id, http_status=200, raw_response=raw, error="invalid_response"
            )
        return PredictionSuccess(
            run_id=run_id,
            http_status=200,
            raw_response=raw,
            width=parsed.width,
            height=parsed.height,
            crop=parsed.crop,
            growth_week=parsed.growth_week,
        )
