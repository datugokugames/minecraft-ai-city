"""Demand-led streets: facility graph, barrier-aware routing, and frontage-grown lots.

The routing raster is a cost-search substrate, NOT a generated road lattice.
Only selected demand paths and local branches become streets.
"""

from __future__ import annotations

import heapq
import itertools
import math

import networkx as nx
import numpy as np
from shapely import affinity, contains_xy
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import nearest_points, substring, unary_union

from .models import Building, City, District, Landscape, RoadSpec, Site

CHARACTERS = ("station", "old_town", "suburban", "planned", "industrial", "riverside")


def river_shape(city):
    return (
        LineString(city.landscape.river).buffer(city.landscape.river_width / 2)
        if city.landscape.river
        else Polygon()
    )


def smooth_path(points, iterations=2):
    coords = np.asarray(points, dtype=float)
    for _ in range(iterations):
        new = [coords[0]]
        for a, b in itertools.pairwise(coords):
            new.extend([0.75 * a + 0.25 * b, 0.25 * a + 0.75 * b])
        new.append(coords[-1])
        coords = np.array(new)
    return LineString(coords).simplify(0.25)


class Router:
    def __init__(self, size, landscape, rail_z, curvature=1.05, orientation_entropy=0.6):
        self.size = size
        self.smoothing = int(np.clip(round(2 + (curvature - 1) * 5), 2, 4))
        self.diagonal_penalty = 0.15 * (1 - orientation_entropy)
        self.step = 4
        self.coords = np.arange(10, size - 9, self.step, dtype=float)
        self.n = len(self.coords)
        x, z = np.meshgrid(self.coords, self.coords)
        self.cost = np.ones_like(x)
        for hx, hz, radius, height in landscape.hills:
            hill = np.exp(-((x - hx) ** 2 + (z - hz) ** 2) / radius**2)
            self.cost += hill * (2 + height / 3)
        river = LineString(landscape.river).buffer(landscape.river_width / 2 + 7)
        bridge_windows = unary_union([box(0, bz - 12, size, bz + 12) for bz in landscape.bridge_z])
        self.forbidden = river.difference(bridge_windows)
        if rail_z is not None:
            rail = box(0, rail_z - 8, size, rail_z + 8)
            openings = unary_union(
                [box(rx - 12, 0, rx + 12, size) for rx in landscape.rail_crossings_x]
            )
            self.forbidden = unary_union([self.forbidden, rail.difference(openings)])
        self.blocked = contains_xy(self.forbidden.buffer(0.5), x, z)

    def index(self, point):
        i, j = [round((v - 10) / self.step) for v in point]
        options = [
            (a, b)
            for a in range(max(0, i - 3), min(self.n, i + 4))
            for b in range(max(0, j - 3), min(self.n, j + 4))
            if not self.blocked[b, a]
        ]
        return min(options, key=lambda p: math.dist(point, (self.coords[p[0]], self.coords[p[1]])))

    def route(self, start, end):
        source, target = self.index(start), self.index(end)
        queue = [(0, source)]
        distance, parent = {source: 0}, {}
        while queue:
            _, current = heapq.heappop(queue)
            if current == target:
                break
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                nxt = current[0] + dx, current[1] + dz
                if (
                    not (0 <= nxt[0] < self.n and 0 <= nxt[1] < self.n)
                    or self.blocked[nxt[1], nxt[0]]
                ):
                    continue
                if (
                    dx
                    and dz
                    and (self.blocked[current[1], nxt[0]] or self.blocked[nxt[1], current[0]])
                ):
                    continue
                cost = (
                    distance[current]
                    + math.hypot(dx, dz)
                    * (self.cost[current[1], current[0]] + self.cost[nxt[1], nxt[0]])
                    * (1 + (self.diagonal_penalty if dx and dz else 0))
                    / 2
                )
                if cost < distance.get(nxt, math.inf):
                    distance[nxt], parent[nxt] = cost, current
                    heapq.heappush(queue, (cost + math.dist(nxt, target), nxt))
        if target not in distance:
            raise ValueError("No route between demand nodes")
        chain, current = [], target
        while current != source:
            chain.append((self.coords[current[0]], self.coords[current[1]]))
            current = parent[current]
        chain.append((self.coords[source[0]], self.coords[source[1]]))
        chain.reverse()
        raw = LineString([start, *chain, end])
        simplified = raw.simplify(2)
        smooth = smooth_path(simplified.coords, self.smoothing)
        if not smooth.intersects(self.forbidden):
            return smooth
        if not simplified.intersects(self.forbidden):
            return simplified
        if raw.intersects(self.forbidden):
            raise ValueError("Endpoint cannot reach permitted crossing")
        return raw


def _landscape(size, rng, metrics, railway):
    phase = rng.uniform(-0.7, 0.7)
    river = [
        (float(size * (0.70 + 0.045 * math.sin(z / size * 5 + phase))), float(z))
        for z in np.linspace(0, size, 36)
    ]
    rail_z = round(size * 0.17) if railway else None
    landscape = Landscape(
        river=river,
        river_width=float(np.clip(6 + metrics.river_density_km_km2, 6, 11)),
        bridge_z=[size * 0.43, size * 0.77],
        rail_crossings_x=[size * 0.39, size * 0.85],
        hills=[
            (
                size * 0.08,
                size * 0.17,
                size * 0.13,
                float(np.clip(metrics.terrain_relief_m / 8, 6, 14)),
            ),
            (size * 0.9, size * 0.9, size * 0.13, 7),
        ],
    )
    return landscape, rail_z


def _districts(size, rng, landscape=None, plan=None):
    centers = (
        np.array([(0.4, 0.27), (0.22, 0.49), (0.2, 0.8), (0.49, 0.77), (0.87, 0.51), (0.61, 0.61)])
        * size
    )
    centers += rng.uniform(-0.025, 0.025, centers.shape) * size
    if landscape is not None:
        river_x = np.interp(
            centers[5, 1], [p[1] for p in landscape.river], [p[0] for p in landscape.river]
        )
        centers[5, 0] = min(centers[5, 0], river_x - landscape.river_width / 2 - 15)
    # Smoothly varying demand fields; no large rectangular zone boundaries.
    step = 4
    zz, xx = np.mgrid[0:size:step, 0:size:step]
    influences = np.ones(6)
    if plan is not None:
        influences[[0, 5]] = np.clip(
            np.sqrt([plan.commercial_ratio / 0.25, plan.park_ratio / 0.1]), 0.8, 1.2
        )
        influences[[1, 2, 3]] = np.clip(math.sqrt(plan.residential_ratio / 0.65), 0.8, 1.2)
    scores = np.array(
        [
            ((xx + 2 - x) ** 2 + (zz + 2 - z) ** 2) ** 0.5
            + size * 0.035 * np.sin(xx / 19 + i) * np.cos(zz / 24 - i)
            for i, (x, z) in enumerate(centers)
        ]
    )
    scores /= influences[:, None, None]
    labels = np.argmin(scores, axis=0)
    kinds = ["commercial", "residential", "residential", "residential", "industrial", "park"]
    districts = []
    for i, character in enumerate(CHARACTERS):
        cells = [
            box(x, z, min(x + step, size), min(z + step, size))
            for z, x in zip(zz[labels == i], xx[labels == i])
        ]
        region = unary_union(cells).buffer(3).buffer(-3).intersection(box(0, 0, size, size))
        if region.geom_type == "MultiPolygon":
            region = max(region.geoms, key=lambda p: p.area)
        districts.append(
            District(
                id=i,
                kind=kinds[i],
                character=character,
                influence=float(influences[i]),
                center=tuple(centers[i]),
                polygon=list(region.exterior.coords),
            )
        )
    return districts


def _grow_roads(size, rng, metrics, plan, districts, landscape, rail_z):
    router = Router(size, landscape, rail_z, metrics.road_sinuosity, metrics.orientation_entropy)
    centers = [d.center for d in districts]
    # The station and five neighbourhood centres generate connection demand.
    demand = nx.Graph()
    for i, a in enumerate(centers):
        for j, b in enumerate(centers[:i]):
            demand.add_edge(i, j, weight=math.dist(a, b) * (0.7 if 0 in (i, j) else 1))
    connections = list(nx.minimum_spanning_tree(demand).edges())
    for edge in ((0, 3), (1, 3), (3, 4)):
        if not any(set(edge) == set(existing) for existing in connections):
            connections.append(edge)
    roads, specs = [], []
    width = float(np.clip((plan.road_width + metrics.mean_road_width_m) / 2, 6, 9))

    def add(line, kind, road_width, purpose, terminal=False):
        roads.append(line)
        specs.append(
            RoadSpec(kind=kind, width=road_width, purpose=purpose, intentional_end=terminal)
        )

    for i, j in connections:
        add(
            router.route(centers[i], centers[j]),
            "arterial",
            width,
            f"demand:{CHARACTERS[i]}-{CHARACTERS[j]}",
        )
    # Regional outlets go through permitted railway underpasses rather than ending arbitrarily.
    for start, end in (
        (centers[0], (size * 0.39, 10)),
        (centers[2], (12, size * 0.83)),
        (centers[4], (size - 12, size * 0.54)),
    ):
        add(router.route(start, end), "collector", 6, "regional_gateway", True)
    # A bank-following promenade links the bridge approaches and keeps riparian land open.
    bank = LineString(landscape.river).offset_curve(landscape.river_width / 2 + 9)
    if bank.geom_type == "LineString":
        bank = substring(bank, 0.42, 0.76, normalized=True)
        network = unary_union(roads)
        a = nearest_points(Point(bank.coords[0]), network)[1]
        b = nearest_points(Point(bank.coords[-1]), network)[1]
        try:
            first = router.route(tuple(a.coords[0]), bank.coords[0])
            last = router.route(bank.coords[-1], tuple(b.coords[0]))
            promenade = LineString([*first.coords, *list(bank.coords)[1:], *list(last.coords)[1:]])
            if promenade.is_simple and not promenade.intersects(router.forbidden):
                add(promenade, "local", 3, "riverside:bank_following")
        except ValueError:
            pass
    river = LineString(landscape.river).buffer(landscape.river_width / 2 + 2)
    # Local street demand: unsupplied points around district centres, not rows or columns.
    target_length = size * (7 + np.clip(metrics.road_density_km_km2 / 20, 0, 2))
    attempts = int(size * 1.3)
    for attempt in range(attempts):
        if sum(line.length for line in roads) > target_length and attempt > 30:
            break
        district = districts[attempt % len(districts)]
        radius = size * (0.15 if district.character != "industrial" else 0.13)
        angle = rng.uniform(0, 2 * np.pi)
        point = np.array(district.center) + np.array(
            [math.cos(angle), math.sin(angle)]
        ) * rng.uniform(radius * 0.15, radius)
        if np.any(point < 13) or np.any(point > size - 13) or Point(point).distance(river) < 6:
            continue
        if rail_z is not None and abs(point[1] - rail_z) < 10:
            continue
        network = unary_union(roads)
        origin = nearest_points(Point(point), network)[1]
        distance = origin.distance(Point(point))
        desired_gap = {
            "old_town": 17,
            "station": 20,
            "suburban": 23,
            "planned": 25,
            "industrial": 28,
            "riverside": 22,
        }[district.character]
        desired_gap *= float(rng.lognormal(0, min(0.25, metrics.road_spacing_cv * 0.2)))
        desired_gap *= float(np.clip(plan.road_spacing / 45, 0.8, 1.25))
        if not desired_gap < distance < size * 0.30:
            continue
        start = tuple(origin.coords[0])
        terminal = rng.random() < float(np.clip(metrics.dead_end_share + 0.12, 0.2, 0.45))
        try:
            first = router.route(start, tuple(point))
        except ValueError:
            continue
        if first.intersects(river) or first.length > distance * 2.2:
            continue
        kind = "alley" if district.character == "old_town" else "local"
        local_width = {
            "old_town": 2.5,
            "station": 4,
            "suburban": 4,
            "planned": 5,
            "industrial": 7,
            "riverside": 3,
        }[district.character]
        if not terminal:
            samples = [line.interpolate(t) for line in roads for t in np.arange(4, line.length, 12)]
            candidates = [
                p
                for p in samples
                if p.distance(origin) > desired_gap * 1.6
                and desired_gap < p.distance(Point(point)) < size * 0.28
            ]
            candidates.sort(key=lambda p: p.distance(Point(point)))
            loop = None
            for target in candidates[:8]:
                try:
                    extension = router.route(tuple(point), tuple(target.coords[0]))
                except ValueError:
                    continue
                combined = LineString([*first.coords, *list(extension.coords)[1:]])
                if (
                    not extension.intersects(river)
                    and combined.is_simple
                    and extension.distance(origin) > 3
                ):
                    loop = combined
                    break
            if loop is not None:
                first = loop
            else:
                terminal = True
        add(first, kind, local_width, f"{district.character}:local_demand", terminal)
    return roads, specs


def _frontage_lots(city, rng):
    from .generation import road_area

    streets = road_area(city)
    river = river_shape(city)
    rail = (
        box(0, city.railway_z - 5, city.size, city.railway_z + 5) if city.railway_z else Polygon()
    )
    forbidden = unary_union([streets, river.buffer(3), rail])
    boundary = box(3, 3, city.size - 3, city.size - 3)
    district_shapes = [Polygon(d.polygon) for d in city.districts]
    buildings, sites, occupied, paths = [], [], [], []
    ordered_roads = sorted(
        zip(city.roads, city.road_specs),
        key=lambda item: ("industrial" not in item[1].purpose, item[1].kind == "alley"),
    )
    for road, spec in ordered_roads:
        line = LineString(road)
        distances = np.arange(7, line.length - 4, 5)
        for distance in distances:
            p = np.array(line.interpolate(distance).coords[0])
            a = np.array(line.interpolate(max(0, distance - 2)).coords[0])
            b = np.array(line.interpolate(min(line.length, distance + 2)).coords[0])
            tangent = b - a
            tangent /= max(1e-9, np.linalg.norm(tangent))
            for side in (-1, 1):
                normal = np.array([-tangent[1], tangent[0]]) * side
                query = Point(p + normal * 12)
                owner = min(
                    range(len(city.districts)), key=lambda i: district_shapes[i].distance(query)
                )
                character = city.districts[owner].character
                # Transition weights affect architecture continuously across district borders.
                weights = np.array(
                    [
                        math.exp(
                            -Point(d.center).distance(query) / (city.size * 0.10 * d.influence)
                        )
                        for d in city.districts
                    ]
                )
                weights /= weights.sum()
                context = (
                    CHARACTERS[int(rng.choice(len(weights), p=weights))]
                    if rng.random() < 0.25
                    else character
                )
                if character == "riverside" and rng.random() < 0.62:
                    continue
                if context == "industrial":
                    width, depth = int(rng.integers(12, 18)), int(rng.integers(10, 15))
                elif context == "station":
                    width, depth = int(rng.integers(7, 11)), int(rng.integers(9, 13))
                else:
                    width, depth = int(rng.integers(7, 10)), int(rng.integers(7, 12))
                scale = float(
                    np.clip(
                        math.sqrt(city.blended_metrics.mean_building_area_m2 or 120) / 11, 0.8, 1.2
                    )
                )
                scale *= min(1, city.size / 192)
                width, depth = max(7, round(width * scale)), max(7, round(depth * scale))
                setback = float(
                    rng.uniform(2.4, 4.3) if character == "suburban" else rng.uniform(2.5, 3.5)
                )
                center = p + normal * (spec.width / 2 + 1.5 + setback + depth / 2)
                x, z = round(center[0] - width / 2), round(center[1] - depth / 2)
                center = np.array([x + width / 2, z + depth / 2])
                angle = math.degrees(math.atan2(tangent[1], tangent[0])) + (180 if side < 0 else 0)
                shape = affinity.rotate(
                    box(x, z, x + width, z + depth), angle, origin=tuple(center)
                )
                envelope = shape.buffer(1.6, join_style="mitre")
                if not boundary.covers(envelope) or envelope.intersects(forbidden):
                    continue
                if any(envelope.intersects(existing) for existing in occupied):
                    continue
                entrance = center - normal * depth / 2
                access = LineString([entrance, p])
                if (
                    access.intersects(river)
                    or access.intersects(rail)
                    or any(access.buffer(0.8).intersects(o) for o in occupied)
                ):
                    continue
                if any(envelope.intersects(path) for path in paths):
                    continue
                # Vacant plots and parking have the same collision-safe parcels as buildings.
                chance = rng.random()
                vacancy = float(np.clip(0.23 - city.plan.building_coverage * 0.3, 0.08, 0.2))
                if chance < (vacancy if character in ("suburban", "industrial") else vacancy / 2):
                    kind = "parking" if chance < 0.13 else "vacant"
                    sites.append(
                        Site(
                            kind=kind,
                            polygon=list(shape.exterior.coords),
                            access_path=list(access.coords),
                        )
                    )
                    occupied.append(envelope)
                    paths.append(access.buffer(0.8))
                    continue
                if context == "industrial":
                    kind, floors = "factory", int(rng.integers(1, 3))
                elif context == "station":
                    kind, floors = (
                        str(rng.choice(["mixed_use", "shop", "apartment"])),
                        int(
                            rng.integers(max(2, city.plan.max_floors - 2), city.plan.max_floors + 1)
                        ),
                    )
                elif context == "old_town":
                    kind, floors = (
                        str(rng.choice(["house", "shop", "mixed_use"], p=[0.6, 0.3, 0.1])),
                        int(rng.integers(1, 3)),
                    )
                else:
                    kind, floors = (
                        str(rng.choice(["house", "apartment"], p=[0.85, 0.15])),
                        int(rng.integers(1, 3)),
                    )
                palette = (
                    "traditional"
                    if context == "old_town"
                    else "modern"
                    if context in ("station", "industrial", "planned")
                    else str(rng.choice(["warm", "traditional", "modern"]))
                )
                if city.plan.style != "mixed":
                    palette = city.plan.style
                buildings.append(
                    Building(
                        id=len(buildings),
                        district_id=owner,
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
                    )
                )
                occupied.append(envelope)
                paths.append(access.buffer(0.8))
    # Civic anchors occupy actual road-fronting lots, with no out-of-band placements.
    for kind, center in (
        ("station", city.districts[0].center),
        ("school", city.districts[3].center),
    ):
        eligible = [b for b in buildings if b.kind not in ("station", "school")]
        if eligible:
            chosen = min(eligible, key=lambda b: Point(b.x, b.z).distance(Point(center)))
            chosen.kind, chosen.floors = kind, 2
    # Keep the MVP's five types even for 128-block towns; only reassign duplicated types.
    for kind in ("house", "apartment", "mixed_use", "shop", "school", "factory", "station"):
        if any(b.kind == kind for b in buildings):
            continue
        eligible = [b for b in buildings if sum(o.kind == b.kind for o in buildings) > 1]
        if not eligible:
            raise ValueError("Insufficient road-fronting parcels for required facilities")
        preferred = city.districts[4 if kind == "factory" else 0].center
        chosen = min(eligible, key=lambda b: Point(b.x, b.z).distance(Point(preferred)))
        chosen.kind = kind
    return buildings, sites


def generate_organic(metrics, sources, plan, seed, size=256, planning_source="rules"):
    if not 128 <= size <= 512 or not 0 <= seed <= 2**63 - 1:
        raise ValueError("size must be 128..512 and seed 0..2^63-1")
    rng = np.random.default_rng(seed)
    landscape, rail_z = _landscape(size, rng, metrics, plan.railway)
    districts = _districts(size, rng, landscape, plan)
    roads, specs = _grow_roads(size, rng, metrics, plan, districts, landscape, rail_z)
    city = City(
        size=size,
        seed=seed,
        plan=plan,
        planning_source=planning_source,
        sources=sources,
        blended_metrics=metrics,
        roads=[list(line.coords) for line in roads],
        road_specs=specs,
        districts=districts,
        buildings=[],
        railway_z=rail_z,
        layout="organic",
        landscape=landscape,
    )
    city.buildings, city.sites = _frontage_lots(city, rng)
    from .quality import evaluate_city, validate_organic

    city.validation = validate_organic(city)
    city.quality = evaluate_city(city)
    return city
