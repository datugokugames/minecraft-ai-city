"""Opt-in v0.6 street cross sections, flat markings and safe verge furniture.

The centreline graph and reserved road envelope are never changed here. Narrow
streets are shared walking/driving surfaces; wider roads reserve two nominal
blocks per side for pedestrians by painting a narrower carriageway. Furniture
is kept wholly outside every road envelope and registered access route.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

import numpy as np
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

from .generation import building_area, road_area
from .morphology import road_graph
from .organic import river_shape
from .preview import mask


@dataclass(frozen=True)
class StreetProfile:
    road_index: int
    kind: str
    reserved_width: float
    carriageway_width: float
    sidewalk_width: float
    shared_surface: bool


@dataclass
class StreetLayout:
    profiles: list[StreetProfile]
    reservation: np.ndarray
    carriageway: np.ndarray
    shared_surface: np.ndarray
    sidewalk: np.ndarray
    shoulder: np.ndarray
    centerline: np.ndarray
    crosswalk: np.ndarray
    stopline: np.ndarray
    junction_count: int
    marked_approaches: int


@dataclass(frozen=True)
class StreetFurniture:
    kind: str
    x: int
    z: int
    # Every entry is a bounded, internal Minecraft state, never LLM input.
    blocks: tuple[tuple[int, int, int, str], ...]


def street_profiles(city):
    """Return actual painted widths, separate from unchanged reserved widths."""
    return [
        StreetProfile(
            road_index=i,
            kind=spec.kind,
            reserved_width=spec.width + 3,
            carriageway_width=spec.width
            if spec.kind == "alley" or spec.width < 4
            else spec.width - 1,
            sidewalk_width=0 if spec.kind == "alley" or spec.width < 4 else 2,
            shared_surface=spec.kind == "alley" or spec.width < 4,
        )
        for i, spec in enumerate(city.road_specs)
    ]


def street_layout(city):
    """Rasterize hierarchy/markings without expanding the reserved street area.

    Zebra crossings and stop lines are cosmetic ground blocks, not traffic AI.
    Markings follow the approach direction of noded graph junctions, including
    T-junctions, rather than assuming a rectangular street grid.
    """
    size = city.size
    profiles = street_profiles(city)
    reservation = mask(road_area(city), size)
    lines = [LineString(r) for r in city.roads]
    carriageway = np.zeros((size, size), dtype=bool)
    shared = carriageway.copy()
    shoulders = carriageway.copy()
    centers = carriageway.copy()
    crosswalks = carriageway.copy()
    stops = carriageway.copy()
    graph, _ = road_graph(lines)
    junctions = [node for node, degree in graph.degree if degree >= 3]
    junction_region = unary_union([Point(node).buffer(8) for node in junctions])
    junction_mask = mask(junction_region, size)
    for line, profile in zip(lines, profiles):
        radius = profile.carriageway_width / 2
        road_pixels = mask(line.buffer(radius), size) & reservation
        carriageway |= road_pixels
        if profile.shared_surface:
            shared |= mask(line.buffer(profile.reserved_width / 2), size) & reservation
        elif profile.kind in {"arterial", "collector"}:
            shoulders |= road_pixels & ~mask(line.buffer(max(0.2, radius - 0.75)), size)
            for t in np.arange(3, line.length - 2, 7):
                # Sampling prevents a sub-voxel centred stripe from disappearing
                # on integer-aligned roads (pixel centres lie half a block away).
                for dt in np.arange(0, 2, 0.4):
                    p = line.interpolate(min(line.length, t + dt))
                    x, z = math.floor(p.x), math.floor(p.y)
                    if 0 <= x < size and 0 <= z < size and road_pixels[z, x]:
                        centers[z, x] = True
    centers &= ~junction_mask
    shoulders &= ~junction_mask
    zz, xx = np.mgrid[0:size, 0:size]
    approaches = 0
    for node in junctions:
        point = Point(node)
        incident = [(line, p) for line, p in zip(lines, profiles) if line.distance(point) < 0.01]
        if not incident:
            continue
        largest = max(p.carriageway_width for _, p in incident)
        if largest < 5:
            continue
        for neighbor in graph.neighbors(node):
            direction = np.asarray(neighbor) - node
            length = np.linalg.norm(direction)
            if length < 0.001:
                continue
            direction /= length
            sample = Point(np.asarray(node) + direction * min(length, 0.1))
            choices = [(line, p) for line, p in incident if line.distance(sample) < 0.01]
            if not choices:
                continue
            _, profile = max(choices, key=lambda pair: pair[1].carriageway_width)
            if profile.shared_surface or profile.carriageway_width < 3:
                continue
            # A too-short approach is left unmarked rather than marking another junction.
            offset = largest / 2 + 2.5
            center = np.asarray(node) + direction * offset
            if any(
                Point(center).distance(Point(other)) < 4 for other in junctions if other != node
            ):
                continue
            normal = np.array([-direction[1], direction[0]])
            dx, dz = xx + 0.5 - center[0], zz + 0.5 - center[1]
            along = dx * direction[0] + dz * direction[1]
            across = dx * normal[0] + dz * normal[1]
            width = profile.carriageway_width / 2
            band = (abs(along) <= 1.35) & (abs(across) < width) & carriageway
            zebra = band & ((np.floor(across + width).astype(int) % 2) == 0)
            if np.count_nonzero(zebra) < 2:
                continue
            crosswalks |= zebra
            # Incoming left-hand lane: the stop line is behind the crossing.
            stops |= (
                (along >= 2.2) & (along < 3.15) & (across < 0) & (across > -width) & carriageway
            )
            approaches += 1
    sidewalk = reservation & ~carriageway & ~shared
    return StreetLayout(
        profiles,
        reservation,
        carriageway,
        shared,
        sidewalk,
        shoulders & ~shared,
        centers & ~shared,
        crosswalks,
        stops,
        len(junctions),
        approaches,
    )


def access_exclusion(city, width=1.6):
    """Protect complete approach corridors, not just their centreline samples."""
    paths = [
        LineString(b.access_path).buffer(width) for b in city.buildings if len(b.access_path) > 1
    ]
    paths += [LineString(s.access_path).buffer(width) for s in city.sites if len(s.access_path) > 1]
    return unary_union(paths)


def _feature(kind, x, z, along_x=True):
    blocks = []
    if kind == "tree":
        blocks.extend((x, y, z, "oak_log[axis=y]") for y in range(4, 7))
        blocks.extend(
            (x + dx, y, z + dz, "oak_leaves[distance=1,persistent=true,waterlogged=false]")
            for y in (7, 8)
            for dx in (-1, 0, 1)
            for dz in (-1, 0, 1)
        )
    elif kind == "guardrail":
        for offset in (-1, 0, 1):
            left, right = str(offset > -1).lower(), str(offset < 1).lower()
            state = (
                f"iron_bars[east={right},north=false,south=false,west={left},waterlogged=false]"
                if along_x
                else f"iron_bars[east=false,north={left},south={right},west=false,waterlogged=false]"
            )
            blocks.append(
                (x + (offset if along_x else 0), 4, z + (0 if along_x else offset), state)
            )
    else:
        blocks.extend((x, y, z, "polished_andesite") for y in range(4, 7))
        if kind == "signal":
            blocks.extend(
                (x, y, z, block)
                for y, block in ((7, "green_concrete"), (8, "yellow_concrete"), (9, "red_concrete"))
            )
        else:
            blocks.append((x, 7, z, "sea_lantern"))
    return StreetFurniture(kind, x, z, tuple(blocks))


def street_furniture(city):
    """Deterministic conservative verge objects; unsafe/occupied sites are skipped.

    Entire tree crowns, not only trunks, must fit outside the road reservation,
    building eaves, registered sites, river, railway and 3.2-block access bands.
    This intentionally leaves very tight streets undecorated.
    """
    lines = [LineString(r) for r in city.roads]
    graph, _ = road_graph(lines)
    junctions = unary_union([Point(node) for node, degree in graph.degree if degree >= 3])
    river = river_shape(city)
    forbidden = unary_union(
        [
            road_area(city).buffer(0.15),
            river.buffer(0.4),
            access_exclusion(city),
            *[building_area(b).buffer(1.8, join_style="mitre") for b in city.buildings],
            *[Polygon(s.polygon).buffer(0.5) for s in city.sites],
            *(
                [box(0, city.railway_z - 4, city.size, city.railway_z + 4)]
                if city.railway_z is not None
                else []
            ),
        ]
    )
    boundary = box(1, 1, city.size - 1, city.size - 1)
    occupied = []
    features = []
    for index, (line, spec) in enumerate(zip(lines, city.road_specs)):
        for number, t in enumerate(
            np.arange(9, line.length - 7, 17 if spec.kind == "arterial" else 23)
        ):
            p = line.interpolate(t)
            before, after = (
                line.interpolate(max(0, t - 1)),
                line.interpolate(min(line.length, t + 1)),
            )
            delta = np.array([after.x - before.x, after.y - before.y])
            norm = np.linalg.norm(delta)
            if norm < 0.01:
                continue
            normal = np.array([-delta[1], delta[0]]) / norm
            for side in (-1, 1):
                near_junction = not junctions.is_empty and p.distance(junctions) < 14
                kind = (
                    "signal"
                    if near_junction and spec.kind in {"arterial", "collector"}
                    else "guardrail"
                    if not river.is_empty and p.distance(river) < spec.width / 2 + 9
                    else "tree"
                    if (number + index + side) % 3 == 0
                    else "light"
                )
                radius = 1 if kind in {"tree", "guardrail"} else 0
                position = np.array([p.x, p.y]) + normal * side * (spec.width / 2 + 2.5 + radius)
                x, z = np.floor(position).astype(int)
                footprint = box(x - radius, z - radius, x + radius + 1, z + radius + 1)
                if (
                    not boundary.covers(footprint)
                    or forbidden.intersects(footprint)
                    or any(footprint.distance(other) < 3 for other in occupied)
                ):
                    continue
                # Avoid elevating/flattening terrain just to accommodate furniture.
                if any(
                    top * math.exp(-((x - hx) ** 2 + (z - hz) ** 2) / r**2) >= 0.7
                    for hx, hz, r, top in city.landscape.hills
                ):
                    continue
                features.append(_feature(kind, int(x), int(z), abs(delta[0]) >= abs(delta[1])))
                occupied.append(footprint)
                if len(features) >= city.size * 2:
                    return features
    return features


def render_street_surface(volume, city, layout=None):
    layout = layout or street_layout(city)
    zz, xx = np.mgrid[0 : city.size, 0 : city.size]
    volume.blocks[3, layout.reservation] = volume.state("smooth_stone")
    volume.blocks[3, layout.sidewalk & ((xx + zz) % 4 == 0)] = volume.state("stone_bricks")
    volume.blocks[3, layout.carriageway] = volume.state("gray_concrete")
    volume.blocks[3, layout.shared_surface] = volume.state("andesite")
    volume.blocks[3, layout.shoulder] = volume.state("light_gray_concrete")
    volume.blocks[3, layout.centerline] = volume.state("white_concrete")
    volume.blocks[3, layout.crosswalk | layout.stopline] = volume.state("white_concrete")
    return layout


def render_street_furniture(volume, city):
    placed = Counter()
    for feature in street_furniture(city):
        columns = {(x, z) for x, _, z, _ in feature.blocks}
        if any(np.any(volume.blocks[4:10, z, x]) or not volume.blocks[3, z, x] for x, z in columns):
            continue
        for x, y, z, state in feature.blocks:
            volume.put(x, y, z, state)
        placed[feature.kind] += 1
    return dict(sorted(placed.items()))


def walkability_report(city, volume):
    """Audit 2-high road space and sampled entrances; doors count as openable.

    This is a voxel audit, not a Minecraft client/pathfinding test. It reports
    approach centreline obstructions separately from full street clearance.
    Roofs above two blocks do not obstruct walking. Door operation is assumed.
    """
    states = list(volume.palette)
    passable = np.array([s.endswith(":air") or "_door[" in s for s in states])
    blocked = ~passable[volume.blocks[4:6]].all(axis=0)
    unsupported_states = np.array([s.endswith(":air") or ":water[" in s for s in states])
    unsupported = unsupported_states[volume.blocks[3]]
    streets = mask(road_area(city), city.size)
    bad = []
    unsupported_access = []
    for b in city.buildings:
        if len(b.access_path) < 2:
            bad.append(b.id)
            continue
        line = LineString(b.access_path)
        samples = [line.interpolate(t) for t in np.arange(0, line.length + 0.01, 0.4)]
        cells = [
            (
                min(city.size - 1, max(0, math.floor(p.y))),
                min(city.size - 1, max(0, math.floor(p.x))),
            )
            for p in samples
        ]
        if any(blocked[z, x] for z, x in cells):
            bad.append(b.id)
        if any(unsupported[z, x] for z, x in cells):
            unsupported_access.append(b.id)
    return {
        "street_clearance_obstructed_pixels": int(np.count_nonzero(blocked & streets)),
        "street_clearance_checked_pixels": int(np.count_nonzero(streets)),
        "street_unsupported_pixels": int(np.count_nonzero(unsupported & streets)),
        "building_access_obstructed_ids": bad,
        "building_access_unsupported_ids": unsupported_access,
        "building_access_checked_count": len(city.buildings),
        "clearance_height_blocks": 2,
        "doors_assumed_openable": True,
        "method": "voxel ground-plane audit, not Minecraft client verification",
    }
