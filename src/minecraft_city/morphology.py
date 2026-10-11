"""Street geometry statistics shared by source analysis and generated-city audits."""

from __future__ import annotations

import itertools
import math
import re

import networkx as nx
import numpy as np
from shapely import snap
from shapely.geometry import MultiPoint, Point
from shapely.ops import polygonize, unary_union


def line_parts(g):
    if g.is_empty:
        return []
    if g.geom_type == "LineString":
        return [g]
    return [p for part in getattr(g, "geoms", []) for p in line_parts(part)]


def road_graph(lines, tolerance=0.001):
    """Node crossings, including endpoints projected onto the middle of another street."""
    if not lines:
        return nx.Graph(), []
    endpoints = MultiPoint([p for line in lines for p in (line.coords[0], line.coords[-1])])
    noded = unary_union([snap(line, endpoints, tolerance) for line in lines])
    edges = line_parts(noded)
    graph = nx.Graph()
    for line in edges:
        coords = list(line.coords)
        for a, b in itertools.pairwise(coords):
            a, b = tuple(round(v, 4) for v in a), tuple(round(v, 4) for v in b)
            if a != b:
                graph.add_edge(a, b, length=math.dist(a, b))
    return graph, edges


def coefficient_variation(values):
    return float(np.std(values) / np.mean(values)) if values and np.mean(values) > 0 else 0.0


def road_width(tags):
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(m)?\s*", str(tags.get("width", "")))
    if match and 1 <= float(match[1]) <= 60:
        return float(match[1]), True
    lanes = str(tags.get("lanes", ""))
    if lanes.isdigit() and 1 <= int(lanes) <= 10:
        return int(lanes) * 3.0, False
    highway = tags.get("highway", "residential")
    return {
        "motorway": 16,
        "trunk": 12,
        "primary": 10,
        "secondary": 8,
        "tertiary": 7,
        "residential": 5,
        "service": 3,
        "living_street": 3,
        "footway": 2,
        "path": 2,
        "steps": 2,
    }.get(highway, 5), False


def measure_network(lines, boundary=None):
    graph, edges = road_graph(lines)
    angles, lengths, sinuosities, turns = [], [], [], []
    for line in lines:
        coords = np.array(line.coords)
        delta = np.diff(coords, axis=0)
        seg_lengths = np.linalg.norm(delta, axis=1)
        headings = np.arctan2(delta[:, 1], delta[:, 0])
        angles.extend(headings.tolist())
        lengths.extend(seg_lengths.tolist())
        chord = np.linalg.norm(coords[-1] - coords[0])
        if chord > 1:
            sinuosities.append(line.length / chord)
        turns.append(float(np.abs(np.diff(np.unwrap(headings))).sum() * 180 / np.pi))
    hist, _ = np.histogram(np.mod(angles, np.pi), bins=12, range=(0, np.pi), weights=lengths)
    hist = hist / max(1e-9, hist.sum())
    nonzero = hist[hist > 0]
    entropy = float(-(nonzero * np.log(nonzero)).sum() / np.log(12))
    junctions = {"t": 0, "cross": 0, "irregular": 0}
    dead = 0
    spacing = []
    # Collapse degree-2 bends for spacing between genuine junctions/endpoints.
    visited = set()
    for node, degree in graph.degree():
        if degree == 1 and (boundary is None or Point(node).distance(boundary.boundary) > 2):
            dead += 1
        if degree >= 3:
            vectors = [np.array(n) - node for n in graph.neighbors(node)]
            bearings = sorted(math.atan2(v[1], v[0]) % (2 * np.pi) for v in vectors)
            gaps = np.diff(bearings + [bearings[0] + 2 * np.pi]) * 180 / np.pi
            if degree == 3 and abs(max(gaps) - 180) <= 35:
                junctions["t"] += 1
            elif degree == 4 and all(abs(g - 90) <= 30 for g in gaps):
                junctions["cross"] += 1
            else:
                junctions["irregular"] += 1
        if degree == 2:
            continue
        for neighbor in graph.neighbors(node):
            edge = frozenset((node, neighbor))
            if edge in visited:
                continue
            previous, current, distance = node, neighbor, graph[node][neighbor]["length"]
            visited.add(edge)
            while graph.degree(current) == 2:
                next_node = next(n for n in graph.neighbors(current) if n != previous)
                edge = frozenset((current, next_node))
                if edge in visited:
                    break
                visited.add(edge)
                distance += graph[current][next_node]["length"]
                previous, current = current, next_node
            spacing.append(distance)
    blocks = [p for p in polygonize(edges) if p.area > 20]
    areas = [p.area for p in blocks]
    compactness = [4 * np.pi * p.area / p.length**2 for p in blocks]
    elongations = []
    for p in blocks:
        coords = list(p.minimum_rotated_rectangle.exterior.coords)
        sides = [math.dist(coords[i], coords[i + 1]) for i in range(4)]
        elongations.append(max(sides) / max(1e-9, min(sides)))
    junction_count = sum(junctions.values())
    stats = {
        "road_sinuosity": max(1, float(np.mean(sinuosities))) if sinuosities else 1,
        "turn_degrees_per_km": sum(turns) / max(1e-9, sum(lengths) / 1000),
        "orientation_entropy": entropy,
        "t_junction_share": junctions["t"] / max(1, junction_count),
        "cross_junction_share": junctions["cross"] / max(1, junction_count),
        "irregular_junction_share": junctions["irregular"] / max(1, junction_count),
        "dead_end_share": dead / max(1, dead + junction_count),
        "road_spacing_cv": coefficient_variation(spacing),
        "block_area_cv": coefficient_variation(areas),
        "block_elongation": max(1, float(np.mean(elongations))) if elongations else 1,
    }
    distributions = {
        "direction_15deg_share": hist.tolist(),
        "junction_spacing_m": spacing,
        "block_area_m2": areas,
        "block_compactness": compactness,
        "block_elongation": elongations,
        "road_sinuosity": sinuosities,
    }
    return stats, distributions, graph


def source_morphology(data):
    stats, distributions, _ = measure_network(data.roads, data.boundary)
    tags = data.road_tags if len(data.road_tags) == len(data.roads) else [{} for _ in data.roads]
    widths = [road_width(t) for t in tags]
    lengths = [line.length for line in data.roads]
    total = max(1e-9, sum(lengths))
    stats.update(
        {
            "arterial_share": sum(
                l
                for l, t in zip(lengths, tags)
                if t.get("highway") in {"motorway", "trunk", "primary", "secondary", "tertiary"}
            )
            / total,
            "alley_share": sum(l for l, (w, _) in zip(lengths, widths) if w <= 3.5) / total,
            "mean_road_width_m": sum(l * w for l, (w, _) in zip(lengths, widths)) / total or 5,
            "width_tag_coverage": sum(l for l, (_, measured) in zip(lengths, widths) if measured)
            / total,
            "river_density_km_km2": sum(line.length for line in data.rivers)
            * 1000
            / data.boundary.area,
            "railway_density_km_km2": sum(line.length for line in data.railways)
            * 1000
            / data.boundary.area,
            "terrain_relief_m": max(data.elevations) - min(data.elevations)
            if len(data.elevations) >= 2
            else 0,
            "elevation_sample_count": len(data.elevations),
        }
    )
    barriers = unary_union(data.rivers + data.railways)
    crossing = unary_union(data.roads).intersection(barriers)
    points = [g for g in getattr(crossing, "geoms", [crossing]) if g.geom_type == "Point"]
    stats["barrier_crossings_km2"] = len(points) * 1_000_000 / data.boundary.area
    distributions["road_width_m"] = [w for w, _ in widths]
    return stats, distributions
