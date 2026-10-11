"""Readable 2D PNG preview from the same validated geometry as the schematic."""

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from shapely import contains_xy
from shapely.geometry import Polygon

from .generation import road_area
from .models import City

DISTRICT_COLORS = {"residential": "#e8dfc8", "commercial": "#ead2ba", "park": "#9fc79b"}
BUILDING_COLORS = {
    "house": "#af7953",
    "apartment": "#527c91",
    "mixed_use": "#34516f",
    "shop": "#d6a44a",
    "school": "#c26a63",
    "factory": "#7c8391",
    "station": "#9971a5",
}


def mask(geometry, size):
    z, x = np.mgrid[0:size, 0:size]
    return contains_xy(geometry, x + 0.5, z + 0.5)


def _font(size):
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def render_preview(city: City, path: str | Path, scale: int = 4):
    if city.layout == "organic":
        from .organic_preview import render_organic

        return render_organic(city, path, scale)
    size = city.size
    map_image = Image.new("RGB", (size, size), "#bdd0ac")
    pixels = np.array(map_image)
    from PIL import ImageColor

    for district in city.districts:
        pixels[mask(Polygon(district.polygon), size)] = ImageColor.getrgb(
            DISTRICT_COLORS[district.kind]
        )
    pixels[mask(road_area(city), size)] = ImageColor.getrgb("#d6d4cc")
    pixels[mask(road_area(city, False), size)] = ImageColor.getrgb("#555b60")
    map_image = Image.fromarray(pixels).resize(
        (size * scale, size * scale), Image.Resampling.NEAREST
    )
    draw = ImageDraw.Draw(map_image)
    for road in city.roads:
        # Centre markings mirror the direction of each generated road segment.
        a, b = np.array(road[0]), np.array(road[-1])
        length = np.linalg.norm(b - a)
        for distance in np.arange(3, length - 2, 7):
            start, end = (
                a + (b - a) * distance / length,
                a + (b - a) * min(distance + 2, length) / length,
            )
            draw.line(
                [tuple(start * scale), tuple(end * scale)], fill="#e8d8a3", width=max(1, scale // 2)
            )
    for district in city.districts:
        if district.kind == "park":
            p = Polygon(district.polygon).centroid
            draw.text(
                (p.x * scale - 12, p.y * scale - 4),
                "PARK",
                fill="#355c3b",
                font=_font(max(8, 2 * scale)),
            )
    for building in city.buildings:
        x, z, w, d = building.x, building.z, building.width, building.depth
        bounds = (x * scale, z * scale, (x + w) * scale - 1, (z + d) * scale - 1)
        draw.rectangle((bounds[0] + 2, bounds[1] + 3, bounds[2] + 2, bounds[3] + 3), fill="#8c8c7f")
        draw.rectangle(bounds, fill=BUILDING_COLORS[building.kind], outline="#f3edda", width=1)
        if scale >= 3:
            draw.text(
                (bounds[0] + 3, bounds[1] + 2), str(building.floors), fill="#fff4de", font=_font(10)
            )
    if city.railway_z is not None:
        z = city.railway_z * scale
        draw.line((0, z, size * scale, z), fill="#454742", width=4 * scale)
        for x in range(0, size * scale, 3 * scale):
            draw.line((x, z - 2 * scale, x, z + 2 * scale), fill="#ae9a79", width=scale)
        for offset in (-scale, scale):
            draw.line((0, z + offset, size * scale, z + offset), fill="#e2ded0", width=scale)
    canvas = Image.new("RGB", (size * scale + 64, size * scale + 242), "#f5f2e9")
    draw = ImageDraw.Draw(canvas)
    title = city.plan.name.encode("ascii", errors="replace").decode()  # Portable default font.
    draw.text((32, 18), title, font=_font(28), fill="#243a3c")
    draw.text(
        (32, 58),
        f"ORIGINAL JAPANESE CITY  /  {size} x {size} blocks  /  seed {city.seed}",
        font=_font(14),
        fill="#526161",
    )
    canvas.paste(map_image, (32, 92))
    y = size * scale + 105
    legend = {**DISTRICT_COLORS, **BUILDING_COLORS, "road": "#555b60"}
    for index, (label, color) in enumerate(legend.items()):
        x = 32 + (index % 5) * ((size * scale) // 5)
        yy = y + (index // 5) * 25
        draw.rectangle((x, yy + 2, x + 12, yy + 14), fill=color)
        draw.text((x + 18, yy), label.replace("_", " "), fill="#384b4b", font=_font(12))
    draw.text(
        (32, y + 56),
        f"{len(city.buildings)} buildings | {len(city.districts)} districts | floor counts shown on roofs | N = up",
        font=_font(12),
        fill="#526161",
    )
    has_osm = any(source["source"] == "osm" for source in city.sources)
    draw.text(
        (32, y + 78),
        "Based on statistics only: original roads and building positions.",
        font=_font(12),
        fill="#526161",
    )
    draw.text(
        (32, y + 99),
        "© OpenStreetMap contributors | ODbL 1.0 | openstreetmap.org/copyright"
        if has_osm
        else "Synthetic demonstration profiles (not measured OSM city data).",
        font=_font(11),
        fill="#526161",
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(path)
    return path
