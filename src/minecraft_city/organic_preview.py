"""Plan view with continuous land-use transitions, frontage and barrier crossings."""

from pathlib import Path

import numpy as np
from PIL import Image, ImageColor, ImageDraw
from shapely.geometry import LineString, Polygon

from .generation import building_area, road_area
from .organic import river_shape
from .preview import BUILDING_COLORS, _font, mask


def render_organic(city, path, scale=4):
    size = city.size
    zz, xx = np.mgrid[0:size, 0:size]
    colors = np.array(
        [
            [222, 209, 192],
            [231, 218, 191],
            [222, 225, 196],
            [219, 225, 212],
            [213, 208, 198],
            [175, 204, 170],
        ]
    )
    weights = np.array(
        [
            np.exp(-np.hypot(xx - d.center[0], zz - d.center[1]) / (size * 0.085 * d.influence))
            for d in city.districts
        ]
    )
    weights /= weights.sum(axis=0)
    pixels = np.einsum("ihw,ic->hwc", weights, colors).astype(np.uint8)
    for hx, hz, radius, height in city.landscape.hills:
        influence = np.exp(-((xx - hx) ** 2 + (zz - hz) ** 2) / radius**2) * 0.6
        pixels = (
            pixels * (1 - influence[..., None]) + np.array([99, 145, 106]) * influence[..., None]
        ).astype(np.uint8)
    water = river_shape(city)
    pixels[mask(water.buffer(2), size)] = [158, 183, 148]
    pixels[mask(water, size)] = [106, 172, 193]
    pixels[mask(road_area(city), size)] = [192, 187, 175]
    for road, spec in zip(city.roads, city.road_specs):
        color = (
            [82, 91, 94]
            if spec.kind == "arterial"
            else [113, 119, 116]
            if spec.kind in ("collector", "local")
            else [158, 152, 135]
        )
        pixels[mask(LineString(road).buffer(spec.width / 2), size)] = color
    map_image = Image.fromarray(pixels).resize(
        (size * scale, size * scale), Image.Resampling.BICUBIC
    )
    draw = ImageDraw.Draw(map_image)

    def points(coords):
        return [(x * scale, z * scale) for x, z in coords]

    for road, spec in zip(city.roads, city.road_specs):
        line = LineString(road)
        if spec.kind == "arterial":
            for t in np.arange(4, line.length - 4, 8):
                draw.line(
                    points([line.interpolate(t).coords[0], line.interpolate(t + 2).coords[0]]),
                    fill="#d6cd9c",
                    width=max(1, scale // 2),
                )
        if spec.intentional_end and spec.purpose != "regional_gateway":
            x, z = road[-1]
            r = spec.width * 0.6
            draw.ellipse(
                ((x - r) * scale, (z - r) * scale, (x + r) * scale, (z + r) * scale), fill="#9a9b8b"
            )
    for site in city.sites:
        if site.access_path:
            draw.line(points(site.access_path), fill="#c8bda4", width=max(2, scale))
        polygon = Polygon(site.polygon)
        color = (
            "#b1b3ab"
            if site.kind == "parking"
            else "#b8be9d"
            if site.kind == "vacant"
            else "#91b489"
        )
        draw.polygon(points(site.polygon), fill=color)
        p = polygon.centroid
        draw.text(
            (p.x * scale - 3, p.y * scale - 5),
            "P" if site.kind == "parking" else "PARK" if site.kind == "park" else "·",
            font=_font(max(8, scale * 2)),
            fill="#edf0d7",
        )
    for building in city.buildings:
        shape = building_area(building)
        coords = list(shape.exterior.coords)
        draw.line(points(building.access_path), fill="#d8c7a7", width=max(2, scale))
        draw.polygon([(x * scale + 2, z * scale + 3) for x, z in coords], fill="#85877b")
        rgb = np.array(ImageColor.getrgb(BUILDING_COLORS[building.kind]))
        color = tuple(np.clip(rgb + (building.variant % 3 - 1) * 10, 0, 255).tolist())
        draw.polygon(points(coords), fill=color, outline="#f7f0de", width=max(1, scale // 3))
        center = shape.centroid
        if scale >= 3:
            draw.text(
                (center.x * scale - 4, center.y * scale - 6),
                str(building.floors),
                font=_font(10),
                fill="#fff3df",
            )
        ex, ez = building.entrance
        draw.ellipse(
            (ex * scale - 2, ez * scale - 2, ex * scale + 2, ez * scale + 2), fill="#ffffda"
        )
    if city.railway_z is not None:
        y = city.railway_z * scale
        draw.line((0, y, size * scale, y), fill="#3c4140", width=4 * scale)
        for x in range(0, size * scale, 4 * scale):
            draw.line((x, y - 2 * scale, x, y + 2 * scale), fill="#b5a48a", width=scale)
        for dy in (-scale, scale):
            draw.line((0, y + dy, size * scale, y + dy), fill="#d3d2bd", width=max(1, scale // 2))
    for d in city.districts:
        x, z = d.center
        label = d.character.replace("_", " ").upper()
        font = _font(max(9, scale * 3))
        bounds = draw.textbbox((0, 0), label, font=font)
        width = bounds[2]
        draw.rounded_rectangle(
            (x * scale - width / 2 - 4, z * scale - 20, x * scale + width / 2 + 4, z * scale - 1),
            radius=3,
            fill="#f4efdb",
        )
        draw.text((x * scale - width / 2, z * scale - 20), label, fill="#3c5151", font=font)
    canvas = Image.new("RGB", (size * scale + 64, size * scale + 244), "#f4f1e8")
    draw = ImageDraw.Draw(canvas)
    draw.text((32, 17), f"Aobamachi / seed {city.seed}", font=_font(28), fill="#263e41")
    draw.text(
        (32, 57),
        "DEMAND-LED STREETS  /  RIVER + RAIL CROSSINGS  /  ROAD-FACING BUILDINGS",
        font=_font(13),
        fill="#60706a",
    )
    canvas.paste(map_image, (32, 90))
    y = size * scale + 103
    for i, (label, color) in enumerate(BUILDING_COLORS.items()):
        x, row = 32 + i % 4 * (size * scale // 4), i // 4
        draw.rectangle((x, y + row * 22, x + 11, y + row * 22 + 11), fill=color)
        draw.text(
            (x + 17, y + row * 22 - 2), label.replace("_", " "), font=_font(12), fill="#495950"
        )
    q = city.quality
    draw.text(
        (32, y + 53),
        f"{len(city.buildings)} buildings | P = parking | light line = entrance access | N = up",
        font=_font(12),
        fill="#495950",
    )
    draw.text(
        (32, y + 76),
        f"Orthogonality {q.get('orthogonal_concentration', 0):.2f} | block area CV {q.get('metrics', {}).get('block_area_cv', 0):.2f} | original synthetic geography",
        font=_font(12),
        fill="#495950",
    )
    attribution = (
        "© OpenStreetMap contributors | ODbL 1.0 | openstreetmap.org/copyright"
        if any(s["source"] == "osm" for s in city.sources)
        else "Synthetic source fixtures; not measured OSM data."
    )
    draw.text((32, y + 101), attribution, font=_font(11), fill="#68776a")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(path)
    return path
