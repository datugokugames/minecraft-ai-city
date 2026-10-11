"""Small-area Overpass acquisition and metric extraction in a local metric CRS.

No Nominatim requests are needed: explicit bounding boxes make API costs predictable.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import re
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import networkx as nx
import numpy as np
import requests
from pyproj import Transformer
from shapely import make_valid, transform
from shapely.geometry import LineString, Polygon, box
from shapely.ops import polygonize, unary_union

from .models import Metrics, Profile
from .morphology import source_morphology

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OSM_ATTRIBUTION = "© OpenStreetMap contributors, ODbL 1.0 — https://www.openstreetmap.org/copyright"
PRESETS = {
    "tokyo": (35.705, 139.789, 35.711, 139.797),
    "kyoto": (35.001, 135.758, 35.007, 135.766),
}


@dataclass
class UrbanData:
    boundary: Polygon
    roads: list = field(default_factory=list)
    buildings: list = field(default_factory=list)  # (polygon, height_m, tagged)
    landuse: list = field(default_factory=list)  # (polygon, category)
    road_tags: list = field(default_factory=list)
    rivers: list = field(default_factory=list)
    railways: list = field(default_factory=list)
    elevations: list = field(default_factory=list)


def parts(geometry, types):
    if geometry.is_empty:
        return []
    if geometry.geom_type in types:
        return [geometry]
    return [p for g in getattr(geometry, "geoms", []) for p in parts(g, types)]


def extract_metrics(data: UrbanData) -> Metrics:
    area = data.boundary.area
    if area <= 0:
        raise ValueError("analysis boundary must have positive area")
    area_km2 = area / 1_000_000
    lines = parts(unary_union(data.roads), {"LineString"})
    graph = nx.Graph()
    bearings, lengths = [], []
    for line in lines:
        coords = list(line.coords)
        for a, b in itertools.pairwise(coords):
            a, b = tuple(np.round(a, 5)), tuple(np.round(b, 5))
            if a == b:
                continue
            graph.add_edge(a, b)
            bearings.append(math.atan2(b[1] - a[1], b[0] - a[0]))
            lengths.append(math.dist(a, b))
    intersections = sum(degree >= 3 for _, degree in graph.degree())
    blocks = [p for p in polygonize(lines) if p.area > 20]
    footprint = unary_union([p for p, _, _ in data.buildings])
    land_areas = {}
    for kind in ("residential", "commercial", "park"):
        land_areas[kind] = unary_union([p for p, k in data.landuse if k == kind]).area
    # Shares are relative to classified land, not a claim of complete OSM coverage.
    land_total = sum(land_areas.values())
    land_coverage = unary_union([p for p, _ in data.landuse]).area / area
    shares = (
        {k: v / land_total for k, v in land_areas.items()}
        if land_total
        else {"residential": 0.65, "commercial": 0.25, "park": 0.1}
    )
    orthogonal = abs(np.average(np.exp(4j * np.array(bearings)), weights=lengths)) if lengths else 0
    heights = [h for _, h, _ in data.buildings]
    morphology, _ = source_morphology(data)
    return Metrics(
        **morphology,
        area_km2=area_km2,
        road_density_km_km2=sum(line.length for line in lines) / 1000 / area_km2,
        intersection_count=intersections,
        intersection_density_km2=intersections / area_km2,
        building_count=len(data.buildings),
        building_density_km2=len(data.buildings) / area_km2,
        building_coverage=min(1, footprint.area / area),
        mean_building_area_m2=float(np.mean([p.area for p, _, _ in data.buildings]))
        if heights
        else 0,
        median_block_area_m2=float(np.median([p.area for p in blocks])) if blocks else 0,
        mean_block_compactness=float(np.mean([4 * math.pi * p.area / p.length**2 for p in blocks]))
        if blocks
        else 0,
        orthogonal_road_share=min(1, float(orthogonal)),
        mean_height_m=float(np.mean(heights)) if heights else 9,
        height_tag_coverage=sum(tagged for _, _, tagged in data.buildings) / max(1, len(heights)),
        landuse_tag_coverage=min(1, land_coverage),
        residential_share=shares["residential"],
        commercial_share=shares["commercial"],
        park_share=shares["park"],
    )


def sample_profile(name: str = "tokyo") -> Profile:
    """Synthetic geometry fixtures, explicitly NOT surveyed city measurements."""
    if name not in PRESETS:
        raise ValueError(f"unknown sample: {name}")
    rng = np.random.default_rng(101 if name == "tokyo" else 202)
    data = UrbanData(box(0, 0, 600, 600))
    step = 75 if name == "tokyo" else 100
    for p in range(0, 601, step):
        data.roads += [LineString([(p, 0), (p, 600)]), LineString([(0, p), (600, p)])]
    data.road_tags = [
        {"highway": "secondary" if i % 6 == 0 else "residential"} for i in range(len(data.roads))
    ]
    if name == "tokyo":
        data.roads.append(
            LineString([(0, 135), (90, 165), (210, 105), (330, 175), (450, 145), (600, 190)])
        )
        data.road_tags.append({"highway": "secondary", "width": "8"})
        for x in (75, 225, 375, 525):
            data.roads.append(LineString([(x, 300), (x + 15, 325), (x + 22, 340)]))
            data.road_tags.append({"highway": "service", "width": "3"})
    data.rivers = [LineString([(540, 0), (550, 150), (520, 350), (555, 600)])]
    data.railways = [LineString([(0, 65), (600, 65)])]
    for x in range(8, 580, 22 if name == "tokyo" else 28):
        for z in range(8, 580, 25):
            p = box(x, z, x + int(rng.integers(9, 17)), z + int(rng.integers(9, 18)))
            if any(p.distance(road) < 4 for road in data.roads) or rng.random() < 0.12:
                continue
            h = float(rng.choice([6, 9, 15, 24] if name == "tokyo" else [6, 6, 9, 12]))
            data.buildings.append((p, h, True))
    data.landuse = [
        (box(0, 0, 360, 600), "residential"),
        (box(360, 0, 540, 600), "commercial"),
        (box(540, 0, 600, 600), "park"),
    ]
    return Profile(
        name=f"{name}-inspired synthetic sample",
        source="synthetic_sample",
        attribution="Project-generated synthetic fixture; not measured OSM data.",
        notes=[
            "Offline demonstration only; regional names describe inspiration, not measured facts."
        ],
        metrics=extract_metrics(data),
        distributions=source_morphology(data)[1],
    )


def validate_bbox(bounds) -> tuple[float, float, float, float]:
    south, west, north, east = map(float, bounds)
    if not all(math.isfinite(v) for v in (south, west, north, east)):
        raise ValueError("bbox must be finite")
    if not (-80 <= south < north <= 80 and -180 <= west < east <= 180):
        raise ValueError("bbox must be south,west,north,east (no dateline crossing; latitude ±80)")
    width = (east - west) * 111_320 * math.cos(math.radians((south + north) / 2))
    height = (north - south) * 111_320
    if width * height > 4_000_000 or max(width, height) > 4000:
        raise ValueError("MVP limits OSM requests to 4 km² and each side to 4 km")
    if min(width, height) < 50:
        raise ValueError("bbox sides must be at least 50 m")
    return south, west, north, east


def fetch_osm(bounds, cache_dir: str | Path, *, session=None) -> tuple[dict, str]:
    bounds = validate_bbox(bounds)
    query = (
        "[out:json][timeout:45];("
        f'way["highway"]["area"!="yes"]({",".join(map(str, bounds))});'
        f'nwr["building"]({",".join(map(str, bounds))});'
        f'nwr["landuse"]({",".join(map(str, bounds))});'
        f'nwr["leisure"="park"]({",".join(map(str, bounds))});'
        f'way["waterway"]({",".join(map(str, bounds))});'
        f'way["railway"="rail"]({",".join(map(str, bounds))});'
        f'nwr["ele"]({",".join(map(str, bounds))});'
        ");out geom;"
    )
    cache = Path(cache_dir) / (hashlib.sha256(query.encode()).hexdigest() + ".json")
    if cache.exists():
        cached = json.loads(cache.read_text(encoding="utf-8"))
        return cached["data"], cached["retrieved_at"]
    client = session or requests
    # One bounded request, no automatic retries or geocoder traffic.
    response = client.post(
        OVERPASS_URL,
        data={"data": query},
        timeout=(10, 65),
        headers={"User-Agent": "minecraft-ai-city/0.1 educational-small-area-analysis"},
    )
    response.raise_for_status()
    payload = response.json()
    if "remark" in payload or not payload.get("elements"):
        raise ValueError("Overpass returned an incomplete or empty result")
    retrieved = datetime.now(timezone.utc).isoformat()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"retrieved_at": retrieved, "data": payload}), encoding="utf-8")
    return payload, retrieved


def height_from_tags(tags: dict) -> tuple[float, bool]:
    for key, multiplier in (("height", 1), ("building:levels", 3)):
        value = str(tags.get(key, ""))
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(m|ft|')?\s*", value)
        if match:
            height = float(match[1]) * multiplier * (0.3048 if match[2] in ("ft", "'") else 1)
            if 2 <= height <= 500:
                return height, True
    return 9.0, False


def parse_osm(payload: dict, bounds) -> UrbanData:
    south, west, north, east = validate_bbox(bounds)
    epsg = (32600 if south >= 0 else 32700) + min(60, int(((west + east) / 2 + 180) / 6) + 1)
    project = Transformer.from_crs(4326, epsg, always_xy=True).transform
    boundary = transform(box(west, south, east, north), project, interleaved=False)
    data = UrbanData(boundary)
    elements = payload.get("elements", [])
    # A tagged multipolygon and its member ways must not be counted twice.
    relation_members = {
        m["ref"]
        for e in elements
        if e["type"] == "relation"
        and (
            "building" in e.get("tags", {})
            or "landuse" in e.get("tags", {})
            or e.get("tags", {}).get("leisure") == "park"
        )
        for m in e.get("members", [])
        if m["type"] == "way"
    }
    for element in elements:
        tags = element.get("tags", {})
        geometry = None
        try:
            elevation = float(tags.get("ele", ""))
            if math.isfinite(elevation) and -500 <= elevation <= 9000:
                data.elevations.append(elevation)
        except (TypeError, ValueError):
            pass
        if element["type"] == "way" and element.get("geometry"):
            coords = [(p["lon"], p["lat"]) for p in element["geometry"]]
            if len(coords) < 2:
                continue
            if "highway" in tags and tags.get("area") != "yes":
                line = transform(LineString(coords), project, interleaved=False).intersection(
                    boundary
                )
                clipped = parts(line, {"LineString"})
                data.roads.extend(clipped)
                data.road_tags.extend([tags] * len(clipped))
            if "waterway" in tags or tags.get("railway") == "rail":
                line = transform(LineString(coords), project, interleaved=False).intersection(
                    boundary
                )
                target = data.rivers if "waterway" in tags else data.railways
                target.extend(parts(line, {"LineString"}))
            if (
                element["id"] not in relation_members
                and len(coords) >= 4
                and coords[0] == coords[-1]
            ):
                geometry = make_valid(Polygon(coords))
        elif element["type"] == "relation":
            outer, inner = [], []
            for member in element.get("members", []):
                coords = [(p["lon"], p["lat"]) for p in member.get("geometry", [])]
                if len(coords) >= 2:
                    (inner if member.get("role") == "inner" else outer).append(LineString(coords))
            outer_polys = list(polygonize(unary_union(outer)))
            inner_polys = list(polygonize(unary_union(inner)))
            if outer_polys:
                geometry = unary_union(outer_polys).difference(unary_union(inner_polys))
        if geometry is None:
            continue
        geometry = make_valid(transform(geometry, project, interleaved=False)).intersection(
            boundary
        )
        polygons = parts(geometry, {"Polygon"})
        if tags.get("building") not in (None, "no"):
            height, tagged = height_from_tags(tags)
            data.buildings.extend((p, height, tagged) for p in polygons if p.area > 1)
        kind = tags.get("landuse")
        if tags.get("leisure") == "park" or kind in (
            "recreation_ground",
            "village_green",
            "forest",
        ):
            kind = "park"
        if kind in ("retail", "industrial"):
            kind = "commercial"
        if kind in ("residential", "commercial", "park"):
            data.landuse.extend((p, kind) for p in polygons)
    if not data.roads:
        raise ValueError("No usable roads in OSM response")
    return data


def analyze_osm(name: str, bounds, cache_dir, fallback: str | None = "tokyo") -> Profile:
    validate_bbox(bounds)  # Invalid input must not silently turn into a sample.
    try:
        payload, retrieved = fetch_osm(bounds, cache_dir)
        data = parse_osm(payload, bounds)
        return Profile(
            name=name,
            source="osm",
            attribution=OSM_ATTRIBUTION,
            bounds=list(bounds),
            retrieved_at=retrieved,
            metrics=extract_metrics(data),
            distributions=source_morphology(data)[1],
            notes=[
                "Unspecified building heights use 9 m; levels use 3 m/level.",
                "Widths use width tags, then lanes*3m, then highway-class defaults; coverage is recorded.",
                "Curvature is polyline turning/sinuosity; spacing is along-road junction spacing, not perpendicular street spacing.",
                "Elevation tags are sparse samples, not a DEM. No terrain observation is inferred when absent.",
                "Blocks are closed faces of a 2D road graph, not cadastral parcels.",
                "2D intersections include bridge/tunnel crossings; not traffic topology.",
                "Land-use shares use classified polygons only; missing tags use 65/25/10.",
            ],
        )
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        if fallback is None:
            raise
        warnings.warn(
            f"OSM acquisition failed ({type(exc).__name__}); using labeled synthetic sample.",
            stacklevel=2,
        )
        profile = sample_profile(fallback)
        profile.notes.append(
            f"Requested {name}, bbox={list(bounds)}; acquisition failed: {type(exc).__name__}"
        )
        return profile
