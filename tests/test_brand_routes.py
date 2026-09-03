import asyncio
import base64
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from PIL import Image
from pydantic import ValidationError

from routes import brand_routes
from src import constants


_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
_PNG_URL = f"data:image/png;base64,{base64.b64encode(_PNG).decode('ascii')}"


def _endpoint(path: str, method: str):
    for route in brand_routes.setup_brand_routes().routes:
        if route.path == path and method in route.methods:
            return route.endpoint
    raise AssertionError(f"route not found: {method} {path}")


def _request(*, admin: bool):
    auth = SimpleNamespace(is_configured=True, is_admin=lambda user: admin and user == "admin")
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=auth)),
        headers={},
        state=SimpleNamespace(current_user="admin" if admin else "alice"),
    )


def _image_url(image_format: str, size: tuple[int, int] = (1, 1)) -> str:
    output = BytesIO()
    Image.new("RGB", size, "#e06c75").save(output, format=image_format)
    mime = "jpeg" if image_format == "JPEG" else image_format.lower()
    return f"data:image/{mime};base64,{base64.b64encode(output.getvalue()).decode('ascii')}"


def test_brand_contract_persists_only_valid_admin_updates(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    brand_file = tmp_path / "brand.json"
    settings_file = tmp_path / "settings.json"
    settings_file.write_text('{"existing": true}', encoding="utf-8")
    monkeypatch.setattr(brand_routes, "BRAND_FILE", str(brand_file))

    get_brand = _endpoint("/api/brand", "GET")
    set_brand = _endpoint("/api/admin/brand", "PUT")

    response = Response()
    assert asyncio.run(get_brand(response)) == brand_routes.DEFAULT_BRAND
    assert response.headers["Cache-Control"] == "no-store"

    brand_file.write_text("{broken", encoding="utf-8")
    assert asyncio.run(get_brand(Response())) == brand_routes.DEFAULT_BRAND

    payload = brand_routes.BrandPayload(name=" My Harness ", logo=_PNG_URL, accent="#ABCDEF")
    updated = asyncio.run(set_brand(payload, _request(admin=True)))
    assert updated == {"name": "My Harness", "logo": _PNG_URL, "accent": "#abcdef"}
    assert asyncio.run(get_brand(Response())) == updated
    assert json.loads(brand_file.read_text(encoding="utf-8")) == updated
    assert settings_file.read_text(encoding="utf-8") == '{"existing": true}'
    assert not list(tmp_path.glob("brand.json.tmp.*"))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(set_brand(payload, _request(admin=False)))
    assert exc.value.status_code == 403

    invalid = [
        {"name": 7, "logo": "", "accent": "#e06c75"},
        {"name": "\u0000bad", "logo": "", "accent": "#e06c75"},
        {"name": "x" * 49, "logo": "", "accent": "#e06c75"},
        {"name": "Pandamonium", "logo": "data:image/svg+xml;base64,PHN2Zz4=", "accent": "#e06c75"},
        {"name": "Pandamonium", "logo": _PNG_URL.replace("image/png", "image/jpeg"), "accent": "#e06c75"},
        {
            "name": "Pandamonium",
            "logo": "data:image/png;base64," + base64.b64encode(_PNG[:-12]).decode("ascii"),
            "accent": "#e06c75",
        },
        {"name": "Pandamonium", "logo": _image_url("PNG", (4097, 1)), "accent": "#e06c75"},
        {"name": "Pandamonium", "logo": _image_url("PNG", (2049, 2049)), "accent": "#e06c75"},
        {
            "name": "Pandamonium",
            "logo": "data:image/png;base64,"
            + base64.b64encode(_PNG + b"x" * brand_routes._MAX_LOGO_BYTES).decode("ascii"),
            "accent": "#e06c75",
        },
        {"name": "Pandamonium", "logo": "", "accent": "red"},
        {"name": "Pandamonium", "logo": "", "accent": "#e06c75", "script": "alert(1)"},
    ]
    for value in invalid:
        with pytest.raises(ValidationError):
            brand_routes.BrandPayload.model_validate(value)

    for image_format in ("PNG", "JPEG", "WEBP"):
        logo = _image_url(image_format)
        assert brand_routes.BrandPayload(name="Pandamonium", logo=logo, accent="#e06c75").logo == logo


def test_only_public_brand_read_is_auth_exempt():
    app_source = (Path(__file__).parents[1] / "app.py").read_text(encoding="utf-8")
    exact_block = app_source.split("AUTH_EXEMPT_EXACT = {", 1)[1].split("}", 1)[0]
    assert '"/api/brand"' in exact_block
    assert '"/api/admin/brand"' not in exact_block
    assert Path(constants.BRAND_FILE) == Path(constants.DATA_DIR) / "brand.json"
