"""Seeded, warped road lattice, zoned blocks, and non-overlapping building lots."""

from __future__ import annotations

import itertools
import math

import networkx as nx
import numpy as np
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union
from shapely.strtree import STRtree

from .models import Building, City, CityPlan, District, Metrics


def road_area(city: City, sidewalk: bool = True):
    if city.layout == "organic":
        return unary_union(
            [
                LineString(r).buffer(
                    spec.width / 2 + (1.5 if sidewalk else 0), cap_style="round", join_style="round"
                )
                for r, spec in zip(city.roads, city.road_specs)
            ]
        )
    return unary_union([LineString(r) for r in city.roads]).buffer(
        city.plan.road_width / 2 + (2 if sidewalk else 0), cap_style="square", join_style="mitre"
    )


def building_area(building: Building):
    if building.footprint:
        return Polygon(building.footprint)
    return box(building.x, building.z, building.x + building.width, building.z + building.depth)


def _front(x, z, width, depth, roads):
    candidates = {
        "north": (x + width / 2, z),
        "south": (x + width / 2, z + depth),
        "west": (x, z + depth / 2),
        "east": (x + width, z + depth / 2),
    }
    return min(candidates, key=lambda k: Point(candidates[k]).distance(roads))


def generate_city(
    metrics: Metrics,
    sources: list[dict],
    plan: CityPlan,
    seed: int,
    size: int = 256,
    planning_source: str = "rules",
    layout: str = "legacy",
) -> City:
    if layout == "organic":
        from .organic import generate_organic

        return generate_organic(metrics, sources, plan, seed, size, planning_source)
    if layout != "legacy":
        raise ValueError("Unknown layout")
    if not 128 <= size <= 512 or not 0 <= seed <= 2**63 - 1:
        raise ValueError("size must be 128..512 and seed 0..2^63-1")
    rng = np.random.default_rng(seed)
    cells = max(3, int((size - 36) / plan.road_spacing))
    actual_spacing = (size - 36) / cells
    min_gap = min(plan.road_width + 25, actual_spacing)

    def axis():
        weights = rng.uniform(0.4, 1.6, cells)
        gaps = min_gap + weights / weights.sum() * (size - 36 - cells * min_gap)
        return np.r_[18, 18 + np.cumsum(gaps)]

    xs_axis, zs_axis = axis(), axis()
    points = {}
    # Cap geometric warp when blocks are small so every cell has a usable inner rectangle.
    jitter = max(
        0, min(plan.road_spacing * plan.irregularity, (min_gap - plan.road_width - 22) / 4)
    )
    for iz, z in enumerate(zs_axis):
        for ix, x in enumerate(xs_axis):
            points[ix, iz] = (
                round(float(x + (rng.uniform(-jitter, jitter) if 0 < ix < cells else 0)), 2),
                round(float(z + (rng.uniform(-jitter, jitter) if 0 < iz < cells else 0)), 2),
            )
    roads = []
    polygons = []
    for iz in range(cells + 1):
        for ix in range(cells + 1):
            if ix < cells:
                roads.append([points[ix, iz], points[ix + 1, iz]])
            if iz < cells:
                roads.append([points[ix, iz], points[ix, iz + 1]])
            if ix < cells and iz < cells:
                polygons.append(
                    Polygon(
                        [
                            points[ix, iz],
                            points[ix + 1, iz],
                            points[ix + 1, iz + 1],
                            points[ix, iz + 1],
                        ]
                    )
                )
    lines = unary_union([LineString(r) for r in roads])
    # Exact block counts within rounding; commercial core follows a random original centre.
    core = Point(
        float(rng.uniform(size * 0.35, size * 0.65)), float(rng.uniform(size * 0.35, size * 0.65))
    )
    n = len(polygons)
    # Reserve at least two commercial and three residential blocks for all building types.
    commercial_count = min(n - 4, max(2, round(n * plan.commercial_ratio)))
    park_count = min(n - commercial_count - 3, max(1, round(n * plan.park_ratio)))
    ranked = sorted(
        range(n), key=lambda i: polygons[i].centroid.distance(core) + rng.uniform(0, 18)
    )
    commercial_ids = set(ranked[:commercial_count])
    others = [i for i in range(n) if i not in commercial_ids]
    park_ids = {int(i) for i in rng.choice(others, size=park_count, replace=False)}
    districts, buildings = [], []
    counts = {"residential": 0, "commercial": 0}
    target = float(np.clip(math.sqrt(metrics.mean_building_area_m2 or 140) * 1.15, 12, 22))
    # Reserve one block for a small school campus and nearby housing.
    residential_ids = [i for i in range(n) if i not in commercial_ids | park_ids]
    school_id = max(residential_ids, key=lambda i: polygons[i].area)
    for i, polygon in enumerate(polygons):
        kind = "commercial" if i in commercial_ids else "park" if i in park_ids else "residential"
        districts.append(District(id=i, kind=kind, polygon=list(polygon.exterior.coords)))
        if kind == "park":
            continue
        inset = polygon.buffer(-(plan.road_width / 2 + 4), join_style="mitre")
        if inset.is_empty:
            continue
        minx, minz, maxx, maxz = inset.bounds
        nx_lots, nz_lots = max(1, int((maxx - minx) / target)), max(1, int((maxz - minz) / target))
        xs = np.linspace(minx, maxx, nx_lots + 1)
        zs = np.linspace(minz, maxz, nz_lots + 1)
        added = 0
        for zi in range(nz_lots):
            for xi in range(nx_lots):
                x, z = math.ceil(xs[xi]) + 1, math.ceil(zs[zi]) + 1
                width, depth = math.floor(xs[xi + 1]) - x - 1, math.floor(zs[zi + 1]) - z - 1
                # Shrink to fit skewed blocks; buildings remain axis-aligned Minecraft rectangles.
                while (
                    width >= 7 and depth >= 7 and not inset.covers(box(x, z, x + width, z + depth))
                ):
                    x, z, width, depth = x + 1, z + 1, width - 2, depth - 2
                if min(width, depth) < 7:
                    continue
                occupancy = min(
                    0.98, 0.5 + plan.building_coverage + metrics.building_density_km2 / 20000
                )
                if added and counts[kind] >= 4 and i != school_id and rng.random() > occupancy:
                    continue
                added += 1
                if kind == "commercial":
                    building_kind = "shop" if counts[kind] % 3 == 0 else "mixed_use"
                else:
                    building_kind = "house" if counts[kind] % 3 != 1 else "apartment"
                counts[kind] += 1
                floors = (
                    int(rng.integers(2, plan.max_floors + 1))
                    if building_kind in ("apartment", "mixed_use")
                    else int(rng.integers(1, 3))
                )
                palette = (
                    "traditional"
                    if plan.style == "traditional"
                    else "modern"
                    if plan.style == "modern"
                    else str(rng.choice(["traditional", "modern", "warm"]))
                )
                buildings.append(
                    Building(
                        id=len(buildings),
                        district_id=i,
                        kind=building_kind,
                        x=x,
                        z=z,
                        width=width,
                        depth=depth,
                        floors=floors,
                        facing=_front(x, z, width, depth, lines),
                        palette=palette,
                    )
                )
    candidates = [b for b in buildings if b.district_id == school_id]
    if candidates:
        school = max(candidates, key=lambda b: b.width * b.depth)
        school.kind, school.floors, school.palette = "school", 2, "warm"
    # On small/highly irregular plans, ensure each required type still has a representative.
    required = ["house", "apartment", "mixed_use", "shop", "school"]
    for kind in required:
        if any(b.kind == kind for b in buildings):
            continue
        zone = "commercial" if kind in ("mixed_use", "shop") else "residential"
        eligible = [
            b
            for b in buildings
            if districts[b.district_id].kind == zone
            and sum(other.kind == b.kind for other in buildings) > 1
        ]
        if not eligible:
            raise ValueError(
                "Plan leaves insufficient buildable lots; reduce irregularity/road width or spacing"
            )
        chosen = max(eligible, key=lambda b: b.width * b.depth)
        chosen.kind, chosen.floors = (
            kind,
            min(plan.max_floors, 3 if kind in ("apartment", "mixed_use") else 2),
        )
    city = City(
        size=size,
        seed=seed,
        plan=plan,
        planning_source=planning_source,
        sources=sources,
        blended_metrics=metrics,
        roads=roads,
        districts=districts,
        buildings=buildings,
        railway_z=7 if plan.railway else None,
    )
    city.validation = validate_city(city)
    return city


def validate_city(city: City) -> dict:
    if city.layout == "organic":
        from .quality import validate_organic

        return validate_organic(city)
    errors = []
    graph = nx.Graph()
    for road in city.roads:
        graph.add_edges_from(itertools.pairwise(road))
    connected = graph.number_of_nodes() > 0 and nx.is_connected(graph)
    if not connected:
        errors.append("road network is disconnected")
    boundary = box(0, 0, city.size, city.size)
    streets = road_area(city)
    if not boundary.covers(streets):
        errors.append("roads exceed city bounds")
    shapes = [building_area(b) for b in city.buildings]
    index = STRtree(shapes)
    district_lookup = {d.id: d for d in city.districts}
    for i, (building, shape) in enumerate(zip(city.buildings, shapes)):
        # The expanded footprint includes balcony/roof eaves used by the builder.
        envelope = shape.buffer(1, join_style="mitre")
        if not boundary.covers(envelope) or envelope.intersects(streets):
            errors.append(f"building {i} intersects road or boundary")
        district = district_lookup.get(building.district_id)
        if (
            district is None
            or district.kind == "park"
            or not Polygon(district.polygon).covers(envelope)
        ):
            errors.append(f"building {i} outside its buildable district")
        for j in index.query(envelope):
            if j > i and envelope.intersection(shapes[j].buffer(1, join_style="mitre")).area > 1e-6:
                errors.append(f"buildings {i} and {j} overlap including details")
        if city.railway_z is not None and envelope.intersects(
            box(0, city.railway_z - 2, city.size, city.railway_z + 3)
        ):
            errors.append(f"building {i} intersects railway")
    kinds = {b.kind for b in city.buildings}
    if not {"house", "apartment", "mixed_use", "shop", "school"} <= kinds:
        errors.append("missing required building type")
    if not {"residential", "commercial", "park"} <= {d.kind for d in city.districts}:
        errors.append("missing required district type")
    if errors:
        raise ValueError("Invalid city: " + "; ".join(errors[:12]))
    return {
        "valid": True,
        "roads_connected": connected,
        "road_components": nx.number_connected_components(graph),
        "building_overlaps": 0,
        "building_count": len(shapes),
        "district_count": len(city.districts),
        "building_types": sorted(kinds),
        "achieved_building_coverage": sum(s.area for s in shapes) / city.size**2,
    }
