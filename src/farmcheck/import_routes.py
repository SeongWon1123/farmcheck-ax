# Copyright (c) 2026 FarmCheck contributors.
"""Bounded immutable JPEG/AIHub pairs, owned exclusively by one workspace."""

import base64
import binascii
import json
import re
import secrets
from collections.abc import Callable
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
from typing import Annotated, NoReturn, cast

from fastapi import APIRouter, Depends, Request
from PIL import Image, JpegImagePlugin, UnidentifiedImageError
from pydantic import Field

from farmcheck.contracts import Dimensions
from farmcheck.cropclient import MAX_IMAGE_BYTES, MAX_PIXELS
from farmcheck.normalize import DECIMAL_TEXT
from farmcheck.service import Service, View
from farmcheck.source import make_record
from farmcheck.store import DomainError, FrozenModel

type ParsedValue = (
    dict[str, "ParsedValue"] | list["ParsedValue"] | str | int | Decimal | bool | None
)

MAX_JSON_BYTES = 1024 * 1024
MAX_IMPORT_BODY = 15 * 1024 * 1024
MIN_PRINTABLE_CODEPOINT = 32
MAX_NUMERIC_TOKEN_LENGTH = 256
MAX_EXPONENT_DIGITS = 4
MAX_NUMERIC_EXPONENT = 1024
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 50000


class ImportInput(FrozenModel):
    """JSON strings preserve the uploaded source without multipart dependencies."""

    expected_workspace_revision: Annotated[int, Field(strict=True, ge=0)]
    image_name: str = Field(min_length=1, max_length=200)
    image_base64: str = Field(min_length=1, max_length=4 * ((MAX_IMAGE_BYTES + 2) // 3))
    annotation_name: str = Field(min_length=1, max_length=200)
    annotation_json: str = Field(min_length=1, max_length=MAX_JSON_BYTES)


def plain_name(name: str) -> bool:
    """Allow Unicode basenames, never paths, URL encodings or control characters."""
    return not (
        name in {".", ".."}
        or any(c in name for c in "/\\:%")
        or any(ord(c) < MIN_PRINTABLE_CODEPOINT for c in name)
        or name != name.strip()
    )


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject ambiguous JSON duplicate keys rather than silently choosing one."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            message = "duplicate_json_key"
            raise ValueError(message)
        result[key] = value
    return result


def validate_numeric_token(value: str) -> None:
    """Bound work before any Decimal/int construction, including JSON strings."""
    if len(value) > MAX_NUMERIC_TOKEN_LENGTH:
        message = "numeric_token_too_long"
        raise ValueError(message)
    exponent = re.search(r"[eE]([+-]?[0-9]+)$", value)
    if exponent is not None:
        digits = exponent.group(1).lstrip("+-")
        if len(digits) > MAX_EXPONENT_DIGITS or abs(int(exponent.group(1))) > MAX_NUMERIC_EXPONENT:
            message = "numeric_exponent_too_large"
            raise ValueError(message)


def bounded_decimal(value: str) -> Decimal:
    """Check original numeric tokens before exact decimal parsing."""
    validate_numeric_token(value)
    return Decimal(value)


def bounded_integer(value: str) -> int:
    """Check integer token length before allocating an arbitrary-sized integer."""
    validate_numeric_token(value)
    return int(value)


def reject_constant(value: str) -> None:
    """Reject non-finite JSON constants without constructing a number."""
    raise ValueError(value)


def _validate_pair_names(payload: ImportInput) -> None:
    if not plain_name(payload.image_name) or not plain_name(payload.annotation_name):
        code = "invalid_filename"
        raise DomainError(code, "경로 없이 원본 파일명만 지정하세요.")
    image_stem, dot, extension = payload.image_name.rpartition(".")
    json_stem, json_dot, json_extension = payload.annotation_name.rpartition(".")
    if (
        not dot
        or extension.lower() not in {"jpg", "jpeg"}
        or not json_dot
        or json_extension.lower() != "json"
        or image_stem != json_stem
    ):
        code = "ambiguous_pair"
        raise DomainError(code, "동일한 이름의 JPEG와 JSON 한 쌍을 선택하세요.")


def _reject_json(reason: str) -> NoReturn:
    raise ValueError(reason)


def _validate_json_tree(raw: ParsedValue) -> None:
    pending = [(raw, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > MAX_JSON_DEPTH or nodes > MAX_JSON_NODES:
            _reject_json("json_complexity")
        if isinstance(item, dict):
            for key, value in item.items():
                _ = key.encode("utf-8")
                pending.append((value, depth + 1))
        elif isinstance(item, list):
            pending.extend((value, depth + 1) for value in item)
        elif isinstance(item, str):
            _ = item.encode("utf-8")
            stripped = item.strip()
            if DECIMAL_TEXT.fullmatch(stripped):
                validate_numeric_token(stripped)


def _validate_image_path(raw: ParsedValue, image_name: str) -> None:
    if not isinstance(raw, dict):
        _reject_json("images_required")
    images = raw.get("images")
    if not isinstance(images, dict):
        _reject_json("images_required")
    image_path = images.get("imagepath")
    if not isinstance(image_path, str) or not plain_name(image_path) or image_path != image_name:
        code = "ambiguous_pair"
        message = "JSON images.imagepath는 선택한 사진 파일명과 정확히 같아야 합니다."
        raise DomainError(code, message)


def _validate_annotation(payload: ImportInput) -> bytes:
    try:
        text_bytes = payload.annotation_json.encode("utf-8")
        if len(text_bytes) > MAX_JSON_BYTES:
            code = "json_too_large"
            raise DomainError(code, "JSON은 UTF-8 1 MiB 이하만 가능합니다.", 413)
        # The decoder hooks below produce only this recursive JSON/Decimal tree.
        raw = cast("ParsedValue", json.loads(
            payload.annotation_json,
            object_pairs_hook=unique_object,
            parse_float=bounded_decimal,
            parse_int=bounded_integer,
            parse_constant=reject_constant,
        ))
        _validate_json_tree(raw)
        _validate_image_path(raw, payload.image_name)
        # Number tokens and numeric strings were bounded before normalization.
    except (ValueError, RecursionError, UnicodeError, OverflowError) as error:
        code = "invalid_json"
        message = "유효한 AIHub JSON 객체를 지정하세요. 중복 키는 허용하지 않습니다."
        raise DomainError(code, message) from error
    return text_bytes


def _decode_image(payload: ImportInput) -> bytes:
    try:
        image = base64.b64decode(payload.image_base64, validate=True)
    except (ValueError, binascii.Error) as error:
        code = "invalid_base64"
        message = "data URL이 아닌 표준 base64 이미지 바이트가 필요합니다."
        raise DomainError(code, message) from error
    if not image or len(image) > MAX_IMAGE_BYTES:
        code = "image_size"
        raise DomainError(code, "JPEG는 10 MiB 이하만 가능합니다.", 413)
    return image


def _verify_image(image: bytes) -> Dimensions:
    try:
        with Image.open(BytesIO(image)) as photo:
            if photo.format != "JPEG" or photo.width * photo.height > MAX_PIXELS:
                code = "image_format_or_dimensions"
                raise DomainError(code, "16백만 픽셀 이하 JPEG만 가능합니다.")
            if (
                photo.info.get("exif")
                or photo.getexif()
                or any(
                    marker in {"APP1", "APP13", "COM"}
                    for marker, _data in (
                        photo.applist if isinstance(photo, JpegImagePlugin.JpegImageFile) else ()
                    )
                )
            ):
                code = "exif_not_allowed"
                message = (
                    "EXIF/GPS·XMP·IPTC 또는 주석 메타데이터가 포함된 사진입니다. "
                    "메타데이터 없이 JPEG로 내보낸 후 다시 선택하세요."
                )
                raise DomainError(code, message)
            dimensions = Dimensions(width=photo.width, height=photo.height)
            photo.verify()
        with Image.open(BytesIO(image)) as decoded:
            _ = decoded.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as error:
        code = "invalid_image"
        raise DomainError(code, "완전한 JPEG 파일을 지정하세요.") from error
    return dimensions


def ingest(service: Service, ws: str, payload: ImportInput) -> View:
    """Validate fully before the single transaction; bytes are never rewritten."""
    _validate_pair_names(payload)
    text_bytes = _validate_annotation(payload)
    image = _decode_image(payload)
    dimensions = _verify_image(image)
    identifier = secrets.token_urlsafe(24)
    try:
        source = make_record(
            payload.annotation_json, payload.annotation_name, dimensions
        ).model_copy(
            update={
                "id": identifier,
                "origin": "uploaded",
                "dataset_id": "farmcheck:workspace:" + ws,
                "source_group_id": "uploaded:" + ws + ":" + sha256(text_bytes).hexdigest(),
                "image_member_path": payload.image_name,
                "scenario": None,
            }
        )
    except (ValueError, OverflowError, RecursionError) as error:
        code = "invalid_json"
        raise DomainError(code, "JSON 필드 형식을 확인하세요.") from error
    warnings = (
        ()
        if source.declared_dimensions == dimensions
        else (
            "JSON에 선언된 이미지 크기와 JPEG 실제 크기가 다릅니다. 원본 값은 변경하지 않았습니다.",
        )
    )
    state = service.store.import_pair(
        ws, payload.expected_workspace_revision, source, image, warnings
    )
    return View(
        {
            "record_id": identifier,
            "record_revision": 0,
            "workspace_revision": state.workspace_revision,
            "source": {
                "origin": "uploaded",
                "image_sha256": sha256(image).hexdigest(),
                "json_sha256": source.raw_hash,
            },
            "warnings": list(warnings),
        }
    )


def register_import_routes(
    router: APIRouter,
    service: Service,
    workspace: Callable[[Request], str],
) -> None:
    """Register workspace-owned immutable pair ingestion."""

    @router.post("/api/imports")
    def import_pair(payload: ImportInput, ws: Annotated[str, Depends(workspace)]) -> View:
        return ingest(service, ws, payload)
