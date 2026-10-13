"""Offline, bounded block subdivision and demand-based infill of an existing city.

All physical edits happen on a copy. Planning parcels record a subdivision, while
CityProject.parcels continue to mean occupied footprints for editing compatibility.
"""

from __future__ import annotations

import math
from collections import Counter
from itertools import pairwise

import networkx as nx
import numpy as np
from shapely import affinity, voronoi_polygons
from shapely.geometry import LineString, MultiPoint, Point, Polygon, box
from shapely.ops import nearest_points, split, unary_union
from shapely.strtree import STRtree

from .architecture_models import FacadeRecipe
from .generation import building_area, road_area, validate_city
from .models import Building, RoadSpec, Site
from .morphology import road_graph
from .organic import river_shape
from .project import CityProject, Parcel, city_digest
from .quality import evaluate_city
from .urban_models import RoadGraphEdge, RoadGraphNode, UrbanBlock, UrbanParcel, UrbanPlan


def polygon_parts(shape):
    if shape.is_empty:
        return []
    if isinstance(shape, Polygon):
        return [shape]
    return [p for g in getattr(shape, "geoms", []) for p in polygon_parts(g)]


def polygon_record(shape):
    return {
        "polygon": list(shape.exterior.coords),
        "holes": [list(r.coords) for r in shape.interiors],
    }


def rail_shape(city):
    return (
        box(0, city.railway_z - 5, city.size, city.railway_z + 5)
        if city.railway_z is not None
        else Polygon()
    )


def occupied_land(city, include_paths=True):
    parts = [building_area(b).buffer(1.6, join_style="mitre") for b in city.buildings]
    parts += [Polygon(s.polygon).buffer(1.2, join_style="mitre") for s in city.sites]
    if include_paths:
        parts += [
            LineString(b.access_path).buffer(1.0) for b in city.buildings if len(b.access_path) > 1
        ]
        parts += [
            LineString(s.access_path).buffer(1.0) for s in city.sites if len(s.access_path) > 1
        ]
    return unary_union(parts)


def extract_blocks(city):
    """Connected land faces after subtracting full street and barrier reservations.

    Boundary/open blocks are included and labelled; they are not misreported as
    closed road loops. Interior rings preserve dangling streets and enclosed roads.
    """
    boundary = box(2, 2, city.size - 2, city.size - 2)
    excluded = unary_union([road_area(city), river_shape(city).buffer(3), rail_shape(city)])
    shapes = sorted(polygon_parts(boundary.difference(excluded)), key=lambda p: (p.bounds, p.area))
    return [
        UrbanBlock(
            id=f"block-{i}",
            boundary_block=p.distance(boundary.boundary) < 0.01,
            **polygon_record(p),
        )
        for i, p in enumerate(shapes)
        if p.area >= 20
    ]


def _district(city, point):
    return min(
        city.districts,
        key=lambda d: (Polygon(d.polygon).distance(point), Point(d.center).distance(point), d.id),
    )


def _new_service_roads(city, limit):
    """Give deep empty land a short, connected access branch; never cut occupied land.

    Candidates maximize served depth with a length penalty. Geometry stays straight
    where unobstructed; there is no randomized grid or mandatory curvature.
    """
    original_count = len(city.roads)
    protected = unary_union([occupied_land(city), river_shape(city).buffer(4), rail_shape(city)])
    boundary = box(3, 3, city.size - 3, city.size - 3)
    for _ in range(limit):
        streets = road_area(city)
        network = unary_union([LineString(r) for r in city.roads])
        free = boundary.difference(unary_union([streets, protected]))
        candidates = []
        for z in range(18, city.size - 17, 8):
            for x in range(18, city.size - 17, 8):
                point = Point(x, z)
                if not free.covers(point.buffer(7)):
                    continue
                district = _district(city, point)
                if district.kind == "park":
                    continue
                depth = point.distance(streets)
                if depth < 17 or depth > 62:
                    continue
                relief = sum(
                    h * math.exp(-((x - hx) ** 2 + (z - hz) ** 2) / radius**2)
                    for hx, hz, radius, h in city.landscape.hills
                )
                if relief > 5:
                    continue
                origin = nearest_points(network, point)[0]
                route = LineString([origin, point])
                width = 2.5 if district.character == "old_town" else 4.0
                envelope = route.buffer(width / 2 + 1.5)
                if not boundary.covers(envelope) or envelope.intersects(protected):
                    continue
                # A new branch must leave the existing surface once, with no folded
                # paths, repeated crossings, or disconnected floating roads.
                remaining = route.difference(streets)
                if remaining.geom_type != "LineString" or remaining.length < 12:
                    continue
                score = depth - route.length * 0.12 + (5 if district.character == "station" else 0)
                candidates.append((score, x, z, route, width, district))
        if not candidates:
            break
        _, _, _, route, width, district = max(candidates, key=lambda item: item[:3])
        city.roads.append(list(route.coords))
        city.road_specs.append(
            RoadSpec(
                kind="alley" if width < 3 else "local",
                width=width,
                purpose=f"v06:{district.character}:block_access",
                intentional_end=True,
            )
        )
    return list(range(original_count, len(city.roads)))


def subdivide_region(region, streets, target_area=300, depth=0):
    """Split irregular land by balanced cuts, preferring children retaining frontage.

    No clipping to a replacement rectangle: curved boundaries and holes survive.
    Small inaccessible fragments are retained for explicit landlocked diagnostics.
    """
    if region.area <= target_area * 1.6 or depth >= 8:
        return [region]
    rectangle = list(region.minimum_rotated_rectangle.exterior.coords)
    edges = [(math.dist(a, b), np.array(b) - a) for a, b in pairwise(rectangle)]
    _, axis = max(edges, key=lambda e: e[0])
    axis /= np.linalg.norm(axis)
    normal = np.array([-axis[1], axis[0]])
    vertices = np.asarray(region.exterior.coords)
    along = vertices @ axis
    across = vertices @ normal
    best = None
    for fraction in (0.5, 0.4, 0.6):
        center = axis * (along.min() + fraction * np.ptp(along)) + normal * np.mean(across)
        cutter = LineString([center - normal * 2000, center + normal * 2000])
        children = polygon_parts(split(region, cutter))
        if len(children) < 2 or min(p.area for p in children) < 35:
            continue
        accessible = sum(p.area for p in children if p.distance(streets) < 0.15)
        score = accessible - (max(p.area for p in children) - min(p.area for p in children)) * 0.2
        if best is None or score > best[0]:
            best = score, children
    if best is None:
        return [region]
    return [
        p for child in best[1] for p in subdivide_region(child, streets, target_area, depth + 1)
    ]


def frontage_subdivision(city, region, target_area):
    """Partition irregular land around road-facing demand seeds, not a square grid.

    Voronoi cells follow a curved road's sampled frontage. Intersecting with the
    original block preserves its curved edge, holes and reserved access corridors.
    Deep pieces with no road edge are recorded as landlocked rather than built.
    """
    points = []
    pitch = float(np.clip(math.sqrt(target_area) * 0.9, 12, 20))
    for road, spec in zip(city.roads, city.road_specs):
        line = LineString(road)
        if line.distance(region) > spec.width / 2 + 2:
            continue
        for t in np.arange(3, line.length - 1, pitch):
            p = np.asarray(line.interpolate(t).coords[0])
            tangent = np.asarray(line.interpolate(min(line.length, t + 1)).coords[0]) - np.asarray(
                line.interpolate(max(0, t - 1)).coords[0]
            )
            tangent /= max(1e-9, np.linalg.norm(tangent))
            for side in (-1, 1):
                normal = np.array([-tangent[1], tangent[0]]) * side
                candidate = Point(p + normal * (spec.width / 2 + 1.5 + 7))
                if region.covers(candidate) and all(candidate.distance(q) >= 9 for q in points):
                    points.append(candidate)
    if len(points) < 2:
        return [region]
    cells = voronoi_polygons(MultiPoint(points), extend_to=region.envelope)
    return sorted(
        [p for cell in cells.geoms for p in polygon_parts(cell.intersection(region)) if p.area > 1],
        key=lambda p: p.bounds,
    )


def _road_fronts(city, region):
    return [
        i
        for i, (r, s) in enumerate(zip(city.roads, city.road_specs))
        if region.intersection(LineString(r).buffer(s.width / 2 + 1.65)).area > 0.1
    ]


def _recipe(kind, character, rng):
    return FacadeRecipe(
        roof=str(rng.choice(["gable", "flat"] if kind == "house" else ["flat", "terrace"])),
        window_spacing=int(rng.integers(3, 6)),
        window_width=int(rng.integers(1, 3)),
        balconies=kind in {"apartment", "mixed_use"},
        canopy=kind in {"shop", "school", "station"},
        signage=kind in {"shop", "mixed_use", "factory"},
        lighting=True,
    )


def _landuse(city, district, point, arterial_distance, rng):
    station = next(
        (Point(d.center) for d in city.districts if d.character == "station"),
        Point(city.size / 2, city.size / 2),
    )
    station_influence = math.exp(-point.distance(station) / (city.size * 0.16))
    if district.kind == "industrial":
        return "factory", int(rng.integers(1, 3)), "industrial", (20, 20)
    if district.kind == "park" or point.distance(river_shape(city)) < 12:
        return "house", 1, "park", (8, 9)
    if district.character == "station" or station_influence > 0.62:
        return (
            str(rng.choice(["mixed_use", "shop", "apartment"], p=[0.55, 0.2, 0.25])),
            int(rng.integers(3, city.plan.max_floors + 1)),
            "commercial",
            (16, 18),
        )
    if arterial_distance < 16 and district.character != "old_town":
        return (
            str(rng.choice(["shop", "apartment", "mixed_use"])),
            int(rng.integers(2, min(5, city.plan.max_floors) + 1)),
            "commercial",
            (11, 13),
        )
    if district.character == "old_town" and rng.random() < 0.35:
        return "shop", 2, "commercial", (8, 11)
    return (
        ("apartment" if rng.random() < 0.25 else "house"),
        int(rng.integers(1, 3)),
        "residential",
        (9, 11),
    )


class LotFitter:
    def __init__(self, city):
        self.city = city
        self.lines = [LineString(r) for r in city.roads]
        self.barriers = unary_union([river_shape(city), rail_shape(city)])
        self.occupied = [building_area(b).buffer(1.6, join_style="mitre") for b in city.buildings]
        self.occupied += [Polygon(s.polygon).buffer(1.2, join_style="mitre") for s in city.sites]
        self.paths = [
            LineString(obj.access_path).buffer(1.0)
            for obj in [*city.buildings, *city.sites]
            if len(obj.access_path) > 1
        ]
        self.refresh()

    def refresh(self):
        self.index = STRtree(self.occupied)
        self.path_index = STRtree(self.paths)

    def fit(self, region, road_ids, preferred):
        center = region.representative_point()
        candidates = []
        for i in road_ids:
            line, spec = self.lines[i], self.city.road_specs[i]
            t_center = line.project(center)
            span = max(
                26,
                math.hypot(
                    region.bounds[2] - region.bounds[0], region.bounds[3] - region.bounds[1]
                ),
            )
            for t in np.arange(
                max(1, t_center - span), min(line.length - 1, t_center + span) + 0.01, 2
            ):
                p = np.asarray(line.interpolate(t).coords[0])
                if Point(p).distance(region) > spec.width / 2 + 32:
                    continue
                tangent = np.asarray(
                    line.interpolate(min(line.length, t + 1)).coords[0]
                ) - np.asarray(line.interpolate(max(0, t - 1)).coords[0])
                tangent /= max(1e-9, np.linalg.norm(tangent))
                for side in (-1, 1):
                    normal = np.array([-tangent[1], tangent[0]]) * side
                    for width, depth in dict.fromkeys(
                        [
                            preferred,
                            (max(7, preferred[0] - 2), max(7, preferred[1] - 2)),
                            (7, 9),
                            (7, 7),
                        ]
                    ):
                        for setback in (2, 3.5, 6, 10, 16, 22, 28):
                            desired = p + normal * (spec.width / 2 + 1.5 + setback + depth / 2)
                            x, z = round(desired[0] - width / 2), round(desired[1] - depth / 2)
                            actual = np.array([x + width / 2, z + depth / 2])
                            angle = math.degrees(math.atan2(tangent[1], tangent[0])) + (
                                180 if side < 0 else 0
                            )
                            shape = affinity.rotate(box(x, z, x + width, z + depth), angle)
                            envelope = shape.buffer(1.6, join_style="mitre")
                            if not region.covers(envelope):
                                continue
                            entrance = actual - normal * depth / 2
                            path = LineString([entrance, p])
                            if path.buffer(0.8).intersects(self.barriers):
                                continue
                            if len(
                                self.index.query(path.buffer(0.8), predicate="intersects")
                            ) or len(self.index.query(envelope, predicate="intersects")):
                                continue
                            if len(self.path_index.query(envelope, predicate="intersects")):
                                continue
                            candidates.append(
                                (
                                    width * depth - path.length,
                                    x,
                                    z,
                                    width,
                                    depth,
                                    angle,
                                    shape,
                                    entrance,
                                    path,
                                )
                            )
        if not candidates:
            return None
        return max(candidates, key=lambda c: c[:3])

    def reserve(self, shape, path):
        self.occupied.append(shape.buffer(1.6, join_style="mitre"))
        self.paths.append(path.buffer(1.0))
        self.refresh()


def _graph_records(city):
    lines = [LineString(r) for r in city.roads]
    graph, _ = road_graph(lines)
    if not nx.is_connected(graph):
        raise ValueError("v0.6 road graph must remain connected")
    ids = {point: i for i, point in enumerate(sorted(graph.nodes))}
    nodes = [RoadGraphNode(id=i, x=p[0], z=p[1]) for p, i in ids.items()]
    line_index = STRtree(lines)
    edges = []
    for a, b, data in sorted(graph.edges(data=True), key=lambda e: (ids[e[0]], ids[e[1]])):
        segment = LineString([a, b])
        road_ids = sorted(
            int(i)
            for i in line_index.query(segment.buffer(0.002))
            if lines[i].buffer(0.002).covers(segment)
        )
        if not road_ids:
            raise ValueError("noded graph edge has no matching source road")
        edges.append(
            RoadGraphEdge(source=ids[a], target=ids[b], length=data["length"], road_ids=road_ids)
        )
    return nodes, edges


def validate_subdivision(city):
    plan = city.urban_plan
    if plan is None:
        return
    boundary = box(0, 0, city.size, city.size)
    blocks = {b.id: Polygon(b.polygon, b.holes) for b in plan.blocks}
    shapes = [Polygon(p.polygon, p.holes) for p in plan.parcels]
    index = STRtree(shapes)
    buildings = {b.id: b for b in city.buildings}
    roads = road_area(city)
    for i, (parcel, shape) in enumerate(zip(plan.parcels, shapes)):
        if not boundary.covers(shape) or not blocks[parcel.block_id].buffer(1e-7).covers(shape):
            raise ValueError("planning parcel exceeds its block")
        if shape.intersection(roads).area > 1e-5:
            raise ValueError("planning parcel invades a road")
        if any(j > i and shape.intersection(shapes[j]).area > 1e-5 for j in index.query(shape)):
            raise ValueError("planning parcels overlap")
        if parcel.building_id is not None:
            b = buildings.get(parcel.building_id)
            if b is None or not shape.buffer(1e-7).covers(building_area(b)):
                raise ValueError("planned building is missing or outside its parcel")
        if any(i < 0 or i >= len(city.roads) for i in parcel.frontage_road_ids):
            raise ValueError("planning parcel references missing road")
    nodes, edges = _graph_records(city)
    if plan.nodes != nodes or plan.edges != edges:
        raise ValueError("stored road graph differs from current road geometry")


def plan_urban(project, seed=42, max_additions=96, max_service_roads=6, *, preserve_buildings=True):
    if project.city.layout != "organic":
        raise ValueError("v0.6 planning requires an organic city; legacy remains readable")
    if (
        not isinstance(seed, int)
        or not 0 <= seed <= 2**63 - 1
        or not 0 <= max_additions <= 192
        or not 0 <= max_service_roads <= 12
    ):
        raise ValueError("invalid seed/addition/service-road limit")
    project = CityProject.model_validate(project.model_dump())
    if project.city.urban_plan is not None:
        raise ValueError("city already has a v0.6 planning snapshot; use the original project")
    working = project.model_copy(deep=True)
    city = working.city
    if not preserve_buildings:
        city.buildings = []
        working.parcels = [p for p in working.parcels if p.building_id is None]
    rng = np.random.default_rng(seed)
    added_roads = _new_service_roads(city, max_service_roads)
    blocks = extract_blocks(city)
    occupied = occupied_land(city)
    arterials = unary_union(
        [
            LineString(r)
            for r, s in zip(city.roads, city.road_specs)
            if s.kind in {"arterial", "collector"}
        ]
    )
    parcels = []
    area_scale = float(
        np.clip(math.sqrt(city.blended_metrics.mean_building_area_m2 or 120) / 11, 0.9, 1.15)
    )
    for block in blocks:
        free = Polygon(block.polygon, block.holes).difference(occupied)
        for region in sorted(polygon_parts(free), key=lambda p: p.bounds):
            if region.area < 35:
                continue
            d = _district(city, region.representative_point())
            target = (
                420 if d.kind == "industrial" else 300 if d.character == "station" else 240
            ) * area_scale
            for shape in frontage_subdivision(city, region, target):
                d = _district(city, shape.representative_point())
                fronts = _road_fronts(city, shape)
                parcels.append(
                    UrbanParcel(
                        id=f"lot-{len(parcels)}",
                        block_id=block.id,
                        district_id=d.id,
                        use=d.kind,
                        disposition="unfilled" if fronts else "landlocked",
                        frontage_road_ids=fronts,
                        corner=len(fronts) >= 2,
                        **polygon_record(shape),
                    )
                )
    fitter = LotFitter(city)
    order = sorted(parcels, key=lambda p: (not p.corner, p.disposition == "landlocked", p.id))
    added_buildings, added_sites = [], []
    rejections = Counter()
    for parcel in order:
        region = Polygon(parcel.polygon, parcel.holes)
        if parcel.disposition == "landlocked":
            rejections["no_frontage"] += 1
            continue
        point = region.representative_point()
        district = next(d for d in city.districts if d.id == parcel.district_id)
        kind, floors, use, preferred = _landuse(
            city, district, point, point.distance(arterials), rng
        )
        parcel.use = use
        if len(added_buildings) >= max_additions and use != "park":
            rejections["addition_limit"] += 1
            continue
        result = fitter.fit(region, parcel.frontage_road_ids, preferred)
        if result is None:
            rejections["no_safe_footprint"] += 1
            continue
        _, x, z, width, depth, angle, shape, entrance, access = result
        open_space = use == "park" or (rng.random() < 0.085 and len(added_sites) < 8)
        if open_space:
            site = Site(
                kind="park"
                if use == "park"
                else "plaza"
                if district.character == "station"
                else "parking",
                polygon=list(shape.exterior.coords),
                access_path=list(access.coords),
            )
            city.sites.append(site)
            added_sites.append(site)
            parcel.use, parcel.disposition = site.kind, "open_space"
        else:
            palette = (
                "traditional"
                if district.character == "old_town"
                else "modern"
                if use in {"commercial", "industrial"}
                else str(rng.choice(["warm", "modern", "traditional"]))
            )
            b = Building(
                id=max((b.id for b in city.buildings), default=-1) + 1,
                district_id=district.id,
                kind=kind,
                x=x,
                z=z,
                width=width,
                depth=depth,
                floors=floors,
                facing="north",
                palette=palette,
                angle=angle,
                footprint=list(shape.exterior.coords),
                entrance=tuple(entrance),
                access_path=list(access.coords),
                variant=int(rng.integers(0, 8)),
                facade=_recipe(kind, district.character, rng),
            )
            city.buildings.append(b)
            added_buildings.append(b)
            parcel.building_id, parcel.disposition = b.id, "built"
        parcel.access_path = list(access.coords)
        fitter.reserve(shape, access)
    if not preserve_buildings:
        # Assign civic anchors only on validated parcels; no out-of-band placement.
        for kind, character in [
            ("station", "station"),
            ("school", "planned"),
            ("factory", "industrial"),
            ("house", "suburban"),
            ("apartment", "station"),
            ("mixed_use", "station"),
            ("shop", "old_town"),
        ]:
            if any(b.kind == kind for b in city.buildings):
                continue
            center = Point(next(d.center for d in city.districts if d.character == character))
            eligible = [
                b for b in city.buildings if sum(o.kind == b.kind for o in city.buildings) > 1
            ]
            if not eligible:
                raise ValueError("not enough legal parcels for required civic facilities")
            b = min(eligible, key=lambda b: Point(b.entrance).distance(center))
            b.kind = kind
            if kind in {"station", "school"}:
                b.floors = 2
    nodes, edges = _graph_records(city)
    city.urban_plan = UrbanPlan(
        seed=seed,
        source_city_sha256=project.city_sha256,
        blocks=blocks,
        parcels=parcels,
        nodes=nodes,
        edges=edges,
        added_road_ids=added_roads,
        notes=[
            "Planning snapshot, not cadastral survey or live occupancy. Later edits may occupy unfilled lots.",
            "Existing sites and source roads retained; building preservation mode is recorded in the comparison report.",
            "Height/width/land-use tags may be sparse; source coverage is recorded in quality-report.json.",
            "Service roads avoid occupied land and barriers; no new bridges or rail crossings.",
        ],
    )
    validate_subdivision(city)
    city.validation = validate_city(city)
    city.quality = evaluate_city(city)
    # The managed hierarchy is preserved, including unbuilt districts and user names.
    managed = {
        d.city_district_id: d.id for d in working.districts if d.city_district_id is not None
    }
    for b in added_buildings:
        working.parcels.append(
            Parcel(
                id=f"urban-building-{b.id}",
                district_id=managed[b.district_id],
                building_id=b.id,
                polygon=b.footprint,
            )
        )
    for i in range(len(project.city.sites), len(city.sites)):
        site = city.sites[i]
        d = _district(city, Polygon(site.polygon).centroid)
        working.parcels.append(
            Parcel(
                id=f"urban-site-{i}", district_id=managed[d.id], site_index=i, polygon=site.polygon
            )
        )
    working.city_sha256 = city_digest(city)
    working = CityProject.model_validate(working.model_dump())
    return working, {
        "seed": seed,
        "preserve_buildings": preserve_buildings,
        "source_city_sha256": project.city_sha256,
        "result_city_sha256": working.city_sha256,
        "added_buildings": len(added_buildings),
        "added_sites": len(added_sites),
        "added_roads": len(added_roads),
        "block_count": len(blocks),
        "planning_parcel_count": len(parcels),
        "dispositions": dict(Counter(p.disposition for p in parcels)),
        "rejected_candidates": dict(rejections),
        "existing_buildings_preserved": city.buildings[: len(project.city.buildings)]
        == project.city.buildings,
        "existing_roads_preserved": city.roads[: len(project.city.roads)] == project.city.roads,
        "existing_sites_preserved": city.sites[: len(project.city.sites)] == project.city.sites,
    }
