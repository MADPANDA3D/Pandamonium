"""Public instance branding with an admin-only update route."""

import base64
import binascii
import json
import re
import unicodedata
from io import BytesIO

from fastapi import APIRouter, Request, Response
from PIL import Image
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from core.atomic_io import atomic_write_json
from core.middleware import require_admin
from src.constants import BRAND_FILE


DEFAULT_BRAND = {"name": "Pandamonium", "logo": "", "accent": "#e06c75"}
_MAX_NAME_CHARS = 48
_MAX_LOGO_BYTES = 512 * 1024
_MAX_LOGO_B64_CHARS = ((_MAX_LOGO_BYTES + 2) // 3) * 4
_MAX_LOGO_DIMENSION = 4096
_MAX_LOGO_PIXELS = 4_194_304
_LOGO_DATA_URL_RE = re.compile(
    r"\Adata:image/(?P<kind>png|jpeg|webp);base64,(?P<data>[A-Za-z0-9+/]*={0,2})\Z",
    re.ASCII | re.IGNORECASE,
)
_ACCENT_RE = re.compile(r"\A#[0-9A-Fa-f]{6}\Z", re.ASCII)


def _logo_has_expected_magic(kind: str, payload: bytes) -> bool:
    if kind == "png":
        return (
            len(payload) >= 24
            and payload.startswith(b"\x89PNG\r\n\x1a\n")
            and payload[12:16] == b"IHDR"
        )
    if kind == "jpeg":
        return (
            len(payload) >= 4
            and payload.startswith(b"\xff\xd8\xff")
            and payload.endswith(b"\xff\xd9")
        )
    if kind == "webp":
        return (
            len(payload) >= 12
            and payload.startswith(b"RIFF")
            and payload[8:12] == b"WEBP"
            and int.from_bytes(payload[4:8], "little") + 8 == len(payload)
        )
    return False


def _verify_logo_image(kind: str, payload: bytes) -> None:
    expected_format = {"png": "PNG", "jpeg": "JPEG", "webp": "WEBP"}[kind]

    def validate_open_image(image: Image.Image) -> None:
        if image.format != expected_format:
            raise ValueError("logo content does not match its image type")
        if getattr(image, "n_frames", 1) != 1:
            raise ValueError("logo must contain exactly one image frame")
        width, height = image.size
        if width < 1 or height < 1:
            raise ValueError("logo dimensions must be nonzero")
        if width > _MAX_LOGO_DIMENSION or height > _MAX_LOGO_DIMENSION:
            raise ValueError("logo dimensions must not exceed 4096x4096")
        if width * height > _MAX_LOGO_PIXELS:
            raise ValueError("logo must not exceed 4,194,304 pixels")

    try:
        with Image.open(BytesIO(payload)) as image:
            validate_open_image(image)
            image.verify()
        with Image.open(BytesIO(payload)) as image:
            validate_open_image(image)
            image.load()
    except ValueError:
        raise
    except (EOFError, Image.DecompressionBombError, OSError, SyntaxError) as exc:
        raise ValueError("logo must contain a valid, complete image") from exc


class BrandPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str
    logo: str
    accent: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > _MAX_NAME_CHARS:
            raise ValueError(f"name must be between 1 and {_MAX_NAME_CHARS} characters")
        if any(unicodedata.category(char).startswith("C") for char in value):
            raise ValueError("name must not contain control characters")
        return value

    @field_validator("logo")
    @classmethod
    def validate_logo(cls, value: str) -> str:
        if value == "":
            return value
        if len(value) > _MAX_LOGO_B64_CHARS + 32:
            raise ValueError("logo must be 512 KiB or smaller")
        match = _LOGO_DATA_URL_RE.fullmatch(value)
        if not match:
            raise ValueError("logo must be a PNG, JPEG, or WebP data URL")
        encoded = match.group("data")
        try:
            payload = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("logo must contain valid base64 image data") from exc
        if not payload or len(payload) > _MAX_LOGO_BYTES:
            raise ValueError("logo must be non-empty and 512 KiB or smaller")
        kind = match.group("kind").lower()
        if not _logo_has_expected_magic(kind, payload):
            raise ValueError("logo content does not match its image type")
        _verify_logo_image(kind, payload)
        return f"data:image/{kind};base64,{base64.b64encode(payload).decode('ascii')}"

    @field_validator("accent")
    @classmethod
    def validate_accent(cls, value: str) -> str:
        if not _ACCENT_RE.fullmatch(value):
            raise ValueError("accent must use #RRGGBB format")
        return value.lower()


def _load_brand() -> dict[str, str]:
    try:
        with open(BRAND_FILE, "r", encoding="utf-8") as file:
            raw = json.load(file)
        return BrandPayload.model_validate(raw).model_dump()
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError):
        return dict(DEFAULT_BRAND)


def _save_brand(brand: BrandPayload) -> dict[str, str]:
    value = brand.model_dump()
    atomic_write_json(BRAND_FILE, value, indent=2)
    return value


def setup_brand_routes() -> APIRouter:
    router = APIRouter(tags=["brand"])

    @router.get("/api/brand", response_model=BrandPayload)
    async def get_brand(response: Response) -> dict[str, str]:
        response.headers["Cache-Control"] = "no-store"
        return _load_brand()

    @router.put("/api/admin/brand", response_model=BrandPayload)
    async def set_brand(brand: BrandPayload, request: Request) -> dict[str, str]:
        require_admin(request)
        return _save_brand(brand)

    return router
