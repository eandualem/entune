"""Entune's brand assets: one source for the mark, the logo and every icon export.

    uv run python tools/brand.py

The mark is the approved design (option D of the 2026-09-23 logo review): a thick
speech-bubble ring whose stroke ends in a folded, pointed tail at the lower right, around
five rounded audio bars in a short-medium-tall-medium-short rhythm. The ring's circular
part is exact arcs; the spiral bottom and the tail are the approved outline, traced once
from the approved artwork. The wordmark is "Entune" set in Inter Bold (SIL Open Font
License 1.1), converted to outlines so it renders the same everywhere.

Colours come from the app's tokens: lavender #a99cf2 (dark accent) and violet #6c5cd8
(light accent). The app icon is white on lavender; the standalone mark is violet on light
backgrounds and lavender on dark ones, never white on white.
"""

from __future__ import annotations

import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
LAVENDER, VIOLET, WHITE = "#a99cf2", "#6c5cd8", "#ffffff"
INK_LIGHT, INK_DARK = "#1c1c1f", "#e9e8e6"  # wordmark on light and dark backgrounds

# The mark, in units where the ring's centre is (256, 256).
CENTER = (256.0, 256.0)
RING_OUTER = (
    "M76.1,319.7C77.6,323.5 79.3,326.3 80.0,327.7C80.3,328.4 80.6,329.1 80.5,329.2"
    "C80.5,330.1 88.9,345.7 91.6,350.0C92.2,350.9 93.3,352.6 94.0,353.8"
    "C96.2,357.1 99.3,361.8 101.7,365.1C102.7,366.6 107.2,372.3 107.6,372.6"
    "C107.8,372.8 108.5,373.6 109.3,374.5C110.0,375.4 110.7,376.2 110.8,376.2"
    "C110.9,376.3 112.0,377.5 113.2,378.9C120.2,386.7 132.3,397.5 140.3,403.0"
    "C140.6,403.2 141.1,403.6 141.4,403.8C142.7,404.7 146.2,407.1 148.7,408.5"
    "C150.1,409.4 151.6,410.3 152.0,410.6C153.9,411.7 155.5,412.6 162.0,415.8"
    "C168.4,419.0 168.6,419.1 173.4,421.0C174.0,421.3 175.1,421.7 175.7,422.0"
    "C176.4,422.3 177.9,422.8 179.1,423.1C180.2,423.5 181.3,423.9 181.6,424.0"
    "C181.8,424.1 182.3,424.3 182.8,424.4C183.3,424.5 184.4,424.7 185.2,425.0"
    "C188.5,426.1 190.0,426.5 191.2,426.7C191.9,426.9 193.4,427.2 194.6,427.5"
    "C197.6,428.2 203.6,429.2 207.6,429.7C209.4,429.9 211.9,430.1 213.1,430.3"
    "C215.9,430.6 231.1,430.6 233.8,430.2C235.0,430.1 237.4,429.8 239.3,429.6"
    "C242.9,429.2 248.2,428.3 251.6,427.5C252.7,427.2 254.4,426.8 255.2,426.6"
    "C256.1,426.4 257.4,426.1 258.0,425.9C263.1,424.3 266.1,423.4 267.1,423.0"
    "C267.7,422.7 268.4,422.5 268.6,422.5C268.8,422.5 269.4,422.3 269.9,422.0"
    "C270.5,421.7 271.1,421.5 271.2,421.5C271.5,421.5 272.2,421.2 275.0,420.0"
    "C275.5,419.7 276.1,419.5 276.2,419.5C276.3,419.5 276.8,419.3 277.3,419.1"
    "C277.8,418.9 278.7,418.5 279.2,418.2C281.7,417.1 282.5,416.8 284.3,415.9"
    "C285.3,415.4 287.0,414.6 287.9,414.0C288.9,413.5 290.0,412.8 290.5,412.6"
    "C291.1,412.2 298.8,407.8 300.7,406.7C300.9,406.5 301.6,406.1 302.1,405.8"
    "C304.3,404.4 304.4,405.0 302.8,409.5C300.7,415.7 298.2,422.6 297.7,423.6"
    "C297.2,424.6 294.8,431.7 293.7,435.4C293.1,437.0 291.9,440.7 290.9,443.5"
    "C287.6,452.5 286.3,456.6 286.3,457.9C286.3,462.3 291.0,464.1 295.4,461.6"
    "C296.4,461.0 297.8,460.2 298.6,459.8C299.3,459.4 300.3,458.8 300.8,458.6"
    "C301.3,458.3 302.9,457.4 304.4,456.6C305.9,455.8 307.6,454.9 308.2,454.6"
    "C308.7,454.3 309.6,453.8 310.1,453.6C310.5,453.4 311.5,452.8 312.2,452.4"
    "C313.5,451.5 316.1,450.0 318.4,448.7C319.2,448.3 320.6,447.6 321.4,447.1"
    "C322.2,446.7 323.8,445.8 324.8,445.2C325.8,444.5 327.1,443.8 327.6,443.5"
    "C328.0,443.2 329.1,442.5 329.9,442.0C330.8,441.5 332.2,440.6 333.2,440.0"
    "C334.1,439.4 335.3,438.8 335.7,438.5C336.1,438.2 336.8,437.8 337.2,437.6"
    "C337.6,437.4 338.0,437.1 338.1,437.0C338.1,436.9 338.6,436.6 339.2,436.2"
    "C341.7,434.6 342.4,434.1 342.6,434.0C342.6,433.9 343.0,433.6 343.4,433.4"
    "C343.8,433.2 345.2,432.3 346.4,431.4C347.7,430.5 349.5,429.2 350.4,428.6"
    "C351.4,427.9 353.5,426.4 355.1,425.1C356.8,423.9 358.6,422.6 359.1,422.2"
    "C360.3,421.3 368.8,414.5 369.2,414.2C369.3,414.0 370.2,413.3 371.1,412.5"
    "C371.9,411.8 372.9,411.0 373.1,410.8C373.3,410.5 374.2,409.7 375.2,408.9"
    "C379.4,405.1 387.8,396.9 392.3,392.0C393.9,390.3 395.4,388.7 395.6,388.5"
    "C395.8,388.2 396.8,387.0 397.8,385.8C398.8,384.6 399.7,383.6 399.8,383.5"
    "C400.0,383.4 403.4,379.0 404.9,377.1C405.2,376.7 406.3,375.3 407.3,374.0"
    "C410.0,370.5 415.2,363.0 418.7,357.4C419.1,356.8 419.6,356.1 419.8,355.7"
    "C420.1,355.3 420.3,354.9 420.3,354.8C420.3,354.7 420.5,354.5 420.6,354.3"
    "C421.0,353.9 422.9,348.6 424.9,344.7A190.8,190.8 0 1 0 76.1,319.7Z"
)
RING_INNER = (
    "M392.1,324.1C391.5,325.5 386.6,337.2 385.4,339.5C384.8,340.5 384.1,341.9 383.8,342.5"
    "C382.9,344.0 381.3,346.7 379.8,349.0C379.1,350.0 378.5,351.0 378.4,351.2"
    "C378.3,351.5 378.0,351.9 377.8,352.2C377.6,352.6 376.1,354.7 374.7,357.0"
    "C373.2,359.3 371.5,361.7 370.9,362.5C369.3,364.7 368.2,366.2 367.3,367.5"
    "C366.5,368.7 362.3,374.0 361.7,374.6C361.5,374.8 361.1,375.3 360.8,375.8"
    "C360.5,376.2 359.6,377.3 358.9,378.1C358.2,378.9 357.3,380.0 356.8,380.6"
    "C354.4,383.5 352.1,386.2 351.7,386.7C350.8,387.5 349.0,389.5 346.4,392.4"
    "C340.7,398.7 329.2,410.2 328.6,410.2C328.4,410.2 328.3,409.9 328.3,409.3"
    "C328.3,408.8 328.5,407.8 328.7,407.1C328.9,406.4 329.1,405.3 329.2,404.6"
    "C329.3,403.3 330.1,399.6 330.7,397.6C330.9,396.9 331.1,395.9 331.2,395.4"
    "C331.3,394.8 331.5,393.4 331.8,392.2C332.3,390.0 332.7,388.2 333.2,385.5"
    "C333.3,384.6 333.7,383.3 333.9,382.6C334.2,381.4 334.6,379.8 335.2,376.6"
    "C335.8,373.1 337.2,367.1 337.8,364.8C338.2,363.1 338.7,361.0 339.0,359.6"
    "C339.7,355.5 334.1,351.9 330.6,354.0C329.6,354.6 326.0,357.3 325.2,358.0"
    "C325.0,358.2 324.6,358.6 324.2,358.8C323.9,359.0 323.1,359.7 322.4,360.2"
    "C321.2,361.1 319.8,362.1 315.9,364.8C312.8,367.0 309.2,369.4 308.8,369.6"
    "C308.6,369.7 307.9,370.1 307.3,370.5C306.7,370.9 304.1,372.4 301.6,373.9"
    "C299.0,375.3 296.8,376.6 296.6,376.7C295.0,377.7 286.1,381.9 282.2,383.6"
    "C280.7,384.3 279.2,384.9 278.9,385.0C278.4,385.3 276.9,385.8 274.8,386.5"
    "C274.0,386.8 272.5,387.3 271.3,387.7C269.0,388.5 266.8,389.1 263.3,390.0"
    "C262.1,390.3 260.6,390.7 259.8,390.9C259.0,391.1 257.9,391.4 257.2,391.5"
    "C256.5,391.6 255.0,391.9 253.8,392.1C246.5,393.4 241.9,393.8 232.6,393.9"
    "C225.3,393.9 219.0,393.5 215.4,392.9C214.3,392.7 212.7,392.4 211.8,392.3"
    "C209.5,391.9 206.3,391.2 203.4,390.4C202.1,390.0 200.5,389.6 200.1,389.5"
    "C199.0,389.3 195.9,388.3 193.1,387.3C192.6,387.1 191.7,386.8 191.2,386.6"
    "C190.6,386.4 189.8,386.1 189.4,385.9C189.0,385.7 187.9,385.2 187.1,384.9"
    "C185.3,384.1 179.9,381.6 179.2,381.2C179.0,381.1 178.0,380.6 177.1,380.1"
    "C176.2,379.6 175.3,379.1 175.1,378.9C174.8,378.7 174.4,378.5 174.1,378.3"
    "C173.7,378.1 173.2,377.8 172.9,377.7C172.7,377.5 171.6,376.9 170.6,376.3"
    "C169.5,375.6 168.1,374.8 167.5,374.3C166.8,373.9 166.2,373.5 166.1,373.5"
    "C166.0,373.5 165.7,373.3 165.5,373.1C165.3,372.9 165.0,372.6 164.7,372.5"
    "C164.4,372.3 163.5,371.7 162.7,371.1C161.9,370.5 160.8,369.7 160.3,369.4"
    "C159.8,369.1 159.1,368.5 158.8,368.2C158.4,367.9 157.6,367.2 157.0,366.8"
    "C146.1,358.0 133.6,343.7 127.6,333.2C127.1,332.2 126.4,331.0 126.1,330.5"
    "C125.8,330.0 125.5,329.3 125.3,329.0C125.1,328.7 124.9,328.2 124.7,328.0"
    "C123.9,327.1 118.1,315.6 118.1,314.9C118.1,314.8 117.6,313.9 117.1,312.8"
    "C114.8,307.8 112.7,307.5 111.2,302.9A152.2,152.2 0 1 1 392.1,324.1Z"
)
BAR_WIDTH, BAR_PITCH, BAR_DROP = 32.5, 50.25, 3.0  # the approved art sits the bars 3 below centre
BAR_HEIGHTS = (59.0, 126.0, 197.0, 126.0, 59.0)
BARS = tuple(
    (CENTER[0] + (k - 2) * BAR_PITCH - BAR_WIDTH / 2, CENTER[1] + BAR_DROP - h / 2, BAR_WIDTH, h)
    for k, h in enumerate(BAR_HEIGHTS)
)

# "Entune" in Inter Bold, font units (2048 per em), baseline at y = 0.
WORDMARK = (
    "M135 0V-1490H1132V-1237H440V-877H1080V-628H440V-253H1134V0ZM1672 -647V0H1372V-1118"
    "H1655L1659 -887Q1706 -1004 1792.0 -1068.0Q1878 -1132 2007 -1132"
    "Q2181 -1132 2286.0 -1020.0Q2391 -908 2391 -711V0H2091V-659Q2091 -763 2037.5 -822.0"
    "Q1984 -881 1889 -881Q1793 -881 1732.5 -819.5Q1672 -758 1672 -647ZM3202 -1118V-889"
    "H2993V-327Q2993 -223 3095 -223Q3112 -223 3142.5 -227.5Q3173 -232 3190 -236L3233 -11"
    "Q3183 4 3133.5 10.0Q3084 16 3039 16Q2871 16 2782.0 -65.5Q2693 -147 2693 -301V-889"
    "H2539V-1118H2693V-1384H2993V-1118ZM3782 14Q3608 14 3502.5 -98.0Q3397 -210 3397 -407"
    "V-1118H3697V-459Q3697 -355 3751.0 -296.0Q3805 -237 3899 -237Q3995 -237 4055.5 -298.5"
    "Q4116 -360 4116 -471V-1118H4417V0H4133L4129 -232Q4082 -113 3995.5 -49.5"
    "Q3909 14 3782 14ZM4972 -647V0H4672V-1118H4955L4959 -887Q5006 -1004 5092.0 -1068.0"
    "Q5178 -1132 5307 -1132Q5481 -1132 5586.0 -1020.0Q5691 -908 5691 -711V0H5391V-659"
    "Q5391 -763 5337.5 -822.0Q5284 -881 5189 -881Q5093 -881 5032.5 -819.5"
    "Q4972 -758 4972 -647ZM6452 22Q6281 22 6157.0 -48.0Q6033 -118 5966.5 -247.0"
    "Q5900 -376 5900 -553Q5900 -726 5966.5 -856.0Q6033 -986 6153.5 -1059.0"
    "Q6274 -1132 6437 -1132Q6583 -1132 6702.0 -1070.0Q6821 -1008 6891.5 -882.0"
    "Q6962 -756 6962 -565V-481H6197Q6202 -344 6273.0 -274.0Q6344 -204 6457 -204"
    "Q6536 -204 6592.5 -237.5Q6649 -271 6673 -336L6945 -285Q6904 -146 6775.5 -62.0"
    "Q6647 22 6452 22ZM6199 -669H6673Q6662 -778 6603.0 -842.0Q6544 -906 6440 -906"
    "Q6332 -906 6270.0 -839.5Q6208 -773 6199 -669Z"
)
WORD_BOUNDS = (135, -1490, 6962, 22)
WORD_CAP = 1490

Point = tuple[float, float]
Rect = tuple[float, float, float, float]
# A colour, filled polygons, holes cut from them, rounded rectangles and their corner radius
# (0: fully rounded ends).
Layer = tuple[str, list[list[Point]], list[list[Point]], list[Rect], float]

TOKEN = re.compile(r"[MLHVCQAZ]|-?\d+(?:\.\d+)?")


def polygons(d: str, steps: int = 16) -> list[list[Point]]:
    """Flatten the path commands used here (M L H V C Q Z, and A for the ring's circles)."""
    tokens = TOKEN.findall(d)
    i, cur = 0, (0.0, 0.0)
    shapes: list[list[Point]] = []
    points: list[Point] = []

    def take(n: int) -> list[float]:
        nonlocal i
        values = [float(v) for v in tokens[i : i + n]]
        i += n
        return values

    command = ""
    while i < len(tokens):
        if tokens[i].isalpha():
            command = tokens[i]
            i += 1
            if command == "Z":
                shapes.append(points)
                points = []
                continue
        if command == "M":
            cur = tuple(take(2))  # type: ignore[assignment]
            points = [cur]
            command = "L"  # further pairs are lines
        elif command == "L":
            cur = tuple(take(2))  # type: ignore[assignment]
            points.append(cur)
        elif command == "H":
            cur = (take(1)[0], cur[1])
            points.append(cur)
        elif command == "V":
            cur = (cur[0], take(1)[0])
            points.append(cur)
        elif command in "CQ":
            values = take(6 if command == "C" else 4)
            controls = [cur, *zip(values[::2], values[1::2], strict=True)]
            for k in range(1, steps + 1):
                t = k / steps
                w: tuple[float, ...]
                if command == "C":
                    w = ((1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t**3)
                else:
                    w = ((1 - t) ** 2, 2 * (1 - t) * t, t * t)
                points.append(
                    (
                        sum(a * p[0] for a, p in zip(w, controls, strict=True)),
                        sum(a * p[1] for a, p in zip(w, controls, strict=True)),
                    )
                )
            cur = controls[-1]
        elif command == "A":
            radius, _, _, _, sweep, x, y = take(7)
            a0 = math.atan2(cur[1] - CENTER[1], cur[0] - CENTER[0])
            a1 = math.atan2(y - CENTER[1], x - CENTER[0])
            span = (a1 - a0) % (2 * math.pi) if sweep else -((a0 - a1) % (2 * math.pi))
            span = span or (2 * math.pi if sweep else -2 * math.pi)
            count = max(8, int(abs(span) * radius / 2))
            points += [
                (
                    CENTER[0] + radius * math.cos(a0 + span * k / count),
                    CENTER[1] + radius * math.sin(a0 + span * k / count),
                )
                for k in range(1, count + 1)
            ]
            cur = (x, y)
    return shapes


def mark_bounds() -> tuple[float, float, float, float]:
    xs = [p[0] for s in polygons(RING_OUTER) for p in s]
    ys = [p[1] for s in polygons(RING_OUTER) for p in s]
    return min(xs), min(ys), max(xs), max(ys)


# ---- SVG


def mark_svg_group(ring: str, bars: str, transform: str = "") -> str:
    rects = "".join(
        f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" rx="{w / 2:.2f}"/>'
        for x, y, w, h in BARS
    )
    t = f' transform="{transform}"' if transform else ""
    return (
        f'<g{t}><path fill="{ring}" fill-rule="evenodd" d="{RING_OUTER}{RING_INNER}"/>'
        f'<g fill="{bars}">{rects}</g></g>'
    )


def svg(width: float, height: float, body: str, title: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:g} {height:g}" '
        f'width="{width:g}" height="{height:g}" role="img" aria-label="{title}">'
        f"<title>{title}</title>{body}</svg>\n"
    )


def mark_file(color: str) -> str:
    x0, y0, x1, y1 = mark_bounds()
    pad = 4
    return svg(
        round(x1 - x0 + 2 * pad),
        round(y1 - y0 + 2 * pad),
        mark_svg_group(color, color, f"translate({pad - x0:.2f} {pad - y0:.2f})"),
        "Entune",
    )


# Apple's icon grid: a 1024 canvas, an 824 rounded square inset 100, corner radius 185.4.
ICON_SIZE, ICON_BODY, ICON_INSET, ICON_RADIUS = 1024, 824, 100, 185.4
ICON_SCALE = ICON_BODY / 481.5  # the approved tile was 481.5 wide
ICON_CENTER = (512.0, 504.7)  # where that tile put the ring's centre


def icon_transform() -> str:
    s = ICON_SCALE
    x, y = ICON_CENTER[0] - CENTER[0] * s, ICON_CENTER[1] - CENTER[1] * s
    return f"translate({x:.2f} {y:.2f}) scale({s:.5f})"


def icon_file() -> str:
    body = (
        f'<rect x="{ICON_INSET}" y="{ICON_INSET}" width="{ICON_BODY}" height="{ICON_BODY}" '
        f'rx="{ICON_RADIUS}" fill="{LAVENDER}"/>' + mark_svg_group(WHITE, WHITE, icon_transform())
    )
    return svg(ICON_SIZE, ICON_SIZE, body, "Entune")


def logo_file(mark: str, ink: str) -> str:
    """The mark beside the wordmark; the cap height is half the ring's diameter."""
    x0, y0, x1, y1 = mark_bounds()
    ring = 2 * 190.8
    cap = ring * 0.5
    ws = cap / WORD_CAP
    gap = ring * 0.26
    wx = x1 + gap - WORD_BOUNDS[0] * ws
    baseline = CENTER[1] + cap / 2
    word_right = wx + WORD_BOUNDS[2] * ws
    pad = 4
    width, height = word_right - x0 + 2 * pad, y1 - y0 + 2 * pad
    body = (
        f'<g transform="translate({pad - x0:.2f} {pad - y0:.2f})">'
        + mark_svg_group(mark, mark)
        + f'<path fill="{ink}" transform="translate({wx:.2f} {baseline:.2f}) scale({ws:.5f})"'
        + f' d="{WORDMARK}"/></g>'
    )
    return svg(round(width), round(height), body, "Entune")


# ---- Raster


def fill(
    size: tuple[int, int],
    transform: tuple[float, float, float],
    layers: list[Layer],
    background: tuple[int, int, int, int] = (0, 0, 0, 0),
    ss: int = 4,
) -> Image.Image:
    """Draw layers of (colour, shapes, holes, rounded rects, corner radius), supersampled."""
    w, h = size[0] * ss, size[1] * ss
    image = Image.new("RGBA", (w, h), background)

    def tf(p: tuple[float, float]) -> tuple[float, float]:
        return (
            (p[0] * transform[0] + transform[1]) * ss,
            (p[1] * transform[0] + transform[2]) * ss,
        )

    for colour, shapes, holes, rects, radius in layers:
        alpha = Image.new("L", (w, h), 0)
        draw = ImageDraw.Draw(alpha)
        for shape in shapes:
            draw.polygon([tf(p) for p in shape], fill=255)
        for shape in holes:
            draw.polygon([tf(p) for p in shape], fill=0)
        for x, y, rw, rh in rects:
            a, b = tf((x, y)), tf((x + rw, y + rh))
            draw.rounded_rectangle(
                [a, b], radius=radius * transform[0] * ss if radius else (b[0] - a[0]) / 2, fill=255
            )
        image.paste(Image.new("RGBA", (w, h), colour), (0, 0), alpha)
    return image.resize(size, Image.Resampling.LANCZOS)


def mark_layers(ring: str, bars: str) -> list[Layer]:
    return [
        (ring, polygons(RING_OUTER), polygons(RING_INNER), [], 0),
        (bars, [], [], list(BARS), 0),
    ]


def icon_png(px: int) -> Image.Image:
    s = px / ICON_SIZE
    tile: Layer = (LAVENDER, [], [], [(ICON_INSET, ICON_INSET, ICON_BODY, ICON_BODY)], ICON_RADIUS)
    ms = ICON_SCALE * s
    tile_image = fill((px, px), (s, 0, 0), [tile])
    mark = fill(
        (px, px),
        (ms, ICON_CENTER[0] * s - CENTER[0] * ms, ICON_CENTER[1] * s - CENTER[1] * ms),
        mark_layers(WHITE, WHITE),
    )
    tile_image.alpha_composite(mark)
    return tile_image


def template_png(points: int = 22, scale: int = 3, glyph: float = 17.0) -> Image.Image:
    """The menu-bar glyph: black on transparent, for macOS to tint (a template image)."""
    x0, y0, x1, y1 = mark_bounds()
    px = points * scale
    s = glyph * scale / (y1 - y0)
    ox = px / 2 - (x0 + x1) / 2 * s
    oy = px / 2 - (y0 + y1) / 2 * s
    return fill((px, px), (s, ox, oy), mark_layers("#000000", "#000000"), ss=8)


def main() -> None:
    brand = ROOT / "docs" / "brand"
    brand.mkdir(parents=True, exist_ok=True)
    (brand / "entune-mark-light.svg").write_text(mark_file(VIOLET))
    (brand / "entune-mark-dark.svg").write_text(mark_file(LAVENDER))
    (brand / "entune-logo-light.svg").write_text(logo_file(VIOLET, INK_LIGHT))
    (brand / "entune-logo-dark.svg").write_text(logo_file(LAVENDER, INK_DARK))
    (brand / "entune-app-icon.svg").write_text(icon_file())
    web = ROOT / "src" / "entune" / "web"
    (web / "favicon.svg").write_text(icon_file())
    assets = ROOT / "src" / "entune" / "assets"
    icon_png(1024).save(assets / "icon.png")
    icon_png(512).save(assets / "icon-512.png")
    template_png().save(assets / "menubar-template.png")
    icon_png(256).save(ROOT / "docs" / "demo" / "icon-256.png")
    if sys.platform == "darwin":
        with tempfile.TemporaryDirectory() as tmp:
            iconset = Path(tmp) / "Entune.iconset"
            iconset.mkdir()
            for size in (16, 32, 128, 256, 512):
                icon_png(size).save(iconset / f"icon_{size}x{size}.png")
                icon_png(size * 2).save(iconset / f"icon_{size}x{size}@2x.png")
            subprocess.run(
                [
                    "iconutil",
                    "-c",
                    "icns",
                    str(iconset),
                    "-o",
                    str(ROOT / "packaging" / "Entune.icns"),
                ],
                check=True,
            )
    print("Wrote docs/brand, the favicon, the app icons, the menu-bar template and Entune.icns.")


if __name__ == "__main__":
    main()
