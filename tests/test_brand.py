"""The brand assets follow the approved colour rules and the app uses them."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "docs" / "brand"
ASSETS = ROOT / "src" / "entune" / "assets"


def fills(path: Path) -> set[str]:
    return {e.get("fill", "") for e in ET.parse(path).iter()} - {""}


def test_marks_never_put_white_on_a_light_background() -> None:
    assert fills(BRAND / "entune-mark-light.svg") == {"#6c5cd8"}
    assert fills(BRAND / "entune-mark-dark.svg") == {"#a99cf2"}
    assert fills(BRAND / "entune-logo-light.svg") == {"#6c5cd8", "#1c1c1f"}
    assert fills(BRAND / "entune-logo-dark.svg") == {"#a99cf2", "#e9e8e6"}


def test_app_icon_is_white_artwork_on_a_flat_lavender_tile() -> None:
    assert fills(BRAND / "entune-app-icon.svg") == {"#a99cf2", "#ffffff"}
    icon = Image.open(ASSETS / "icon.png").convert("RGBA")

    def rgba(point: tuple[int, int]) -> tuple[int, ...]:
        value = icon.getpixel(point)
        assert isinstance(value, tuple)
        return value

    assert icon.size == (1024, 1024)
    assert rgba((0, 0))[3] == 0  # Apple's grid leaves a transparent margin
    for point in ((120, 512), (512, 120), (905, 512), (512, 905)):  # a flat tile, no gradient
        assert rgba(point)[:3] == (0xA9, 0x9C, 0xF2)
    assert rgba((512 - 300, 505))[:3] == (0xFF, 0xFF, 0xFF)  # the white ring
    assert Image.open(ASSETS / "icon-512.png").size == (512, 512)
    bundled = Image.open(ASSETS / "Entune.icns").convert("RGBA")
    assert bundled.size == icon.size and bundled.tobytes() == icon.tobytes()


def test_menu_bar_glyph_is_a_black_template_image() -> None:
    glyph = Image.open(ASSETS / "menubar-template.png").convert("RGBA")
    assert glyph.size == (66, 66)  # 22 points at 3x
    raw = glyph.tobytes()
    pixels = [tuple(raw[i : i + 4]) for i in range(0, len(raw), 4)]
    opaque = [p for p in pixels if p[3] > 0]
    assert opaque and all(p[:3] == (0, 0, 0) for p in opaque)


def test_the_page_names_its_icon() -> None:
    page = (ROOT / "src" / "entune" / "web" / "index.html").read_text()
    assert '<link rel="icon" href="/static/favicon.svg"' in page
    assert (ROOT / "src" / "entune" / "web" / "favicon.svg").read_text().startswith("<svg")
