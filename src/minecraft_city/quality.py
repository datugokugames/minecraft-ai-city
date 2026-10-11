"""Observable urban-quality diagnostics and hard geometry invariants."""

from __future__ import annotations

from collections import Counter

import networkx as nx
import numpy as np
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union
from shapely.strtree import STRtree

from .generation import building_area, road_area
from .morphology import coefficient_variation, measure_network
from .organic import river_shape


def evaluate_city(city):
    lines = [LineString(r) for r in city.roads]
    stats, distributions, graph = measure_network(lines)
    headings, lengths = [], []
    for line in lines:
        delta = np.diff(np.array(line.coords), axis=0)
        headings.extend(np.arctan2(delta[:, 1], delta[:, 0]))
        lengths.extend(np.linalg.norm(delta, axis=1))
    orthogonality = float(abs(np.average(np.exp(4j * np.array(headings)), weights=lengths)))
    degrees = Counter(dict(graph.degree()).values())
    terminals = [Point(r[-1]) for r, s in zip(city.roads, city.road_specs) if s.intentional_end]
    terminals += [Point(d.center) for d in city.districts if d.center is not None]
    unexpected = (
        [
            p
            for p, degree in graph.degree()
            if degree == 1 and not any(Point(p).distance(t) < 2 for t in terminals)
        ]
        if city.layout == "organic"
        else []
    )
    district_stats = {}
    for district in city.districts:
        region = Polygon(district.polygon)
        buildings = [b for b in city.buildings if b.district_id == district.id]
        selected = [
            (LineString(r), s)
            for r, s in zip(city.roads, city.road_specs)
            if LineString(r).intersects(region)
        ]
        total_length = sum(line.intersection(region).length for line, _ in selected)
        district_stats[district.character if city.layout == "organic" else str(district.id)] = {
            "building_count": len(buildings),
            "mean_floors": float(np.mean([b.floors for b in buildings])) if buildings else 0,
            "mean_footprint": float(np.mean([building_area(b).area for b in buildings]))
            if buildings
            else 0,
            "road_length_per_area": total_length / max(1, region.area),
            "mean_road_width": sum(
                line.intersection(region).length * s.width for line, s in selected
            )
            / max(1, total_length),
            "building_types": dict(Counter(b.kind for b in buildings)),
        }
    access = [LineString(b.access_path).length for b in city.buildings if b.access_path]
    monotony = (
        orthogonality > 0.8
        and stats["cross_junction_share"] > 0.35
        and stats["road_spacing_cv"] < 0.4
    )
    area_cv = stats["block_area_cv"]
    warnings = []
    if monotony:
        warnings.append("Road network is dominated by a regular orthogonal grid")
    if len(distributions["block_area_m2"]) < 3 or area_cv < 0.25:
        warnings.append("Limited enclosed-block diversity")
    if unexpected:
        warnings.append(f"{len(unexpected)} unclassified road endings")
    station = district_stats.get("station", {})
    suburban = district_stats.get("suburban", {})
    station_taller = station.get("mean_floors", 0) > suburban.get("mean_floors", 0)
    if city.layout == "organic" and not station_taller:
        warnings.append("Station-area mean height does not exceed suburban mean height")
    return {
        "metrics": stats,
        "orthogonal_concentration": orthogonality,
        "grid_monotony_detected": monotony,
        "road_components": nx.number_connected_components(graph),
        "degree_counts": dict(sorted(degrees.items())),
        "unclassified_road_endings": len(unexpected),
        "unclassified_end_coordinates": unexpected,
        "enclosed_block_count": len(distributions["block_area_m2"]),
        "block_area_quantiles": np.quantile(
            distributions["block_area_m2"], [0.1, 0.5, 0.9]
        ).tolist()
        if distributions["block_area_m2"]
        else [],
        "direction_15deg_share": distributions["direction_15deg_share"],
        "frontage_access_fraction": len(access) / max(1, len(city.buildings)),
        "mean_access_length": float(np.mean(access)) if access else 0,
        "building_area_cv": coefficient_variation([building_area(b).area for b in city.buildings]),
        "districts": district_stats,
        "station_taller_than_suburbs": station_taller,
        "warnings": warnings,
        "criteria": {
            "grid": "orthogonal>0.8 AND cross_share>0.35 AND spacing_cv<0.4",
            "block_diversity": "at least 3 closed faces and area CV>=0.25",
            "endings": "degree-1 endpoints must match recorded cul-de-sac or regional gateway",
        },
    }


def validate_organic(city):
    if len(city.roads) != len(city.road_specs) or not city.roads:
        raise ValueError("Road geometry/specification counts do not match")
    lines = [LineString(r) for r in city.roads]
    _, _, graph = measure_network(lines)
    errors = []
    if not nx.is_connected(graph):
        errors.append(
            f"road network has {nx.number_connected_components(graph)} disconnected components"
        )
    streets = road_area(city)
    boundary = box(0, 0, city.size, city.size)
    if not boundary.covers(streets):
        errors.append("roads exceed boundary")
    water = river_shape(city)
    rail = (
        box(0, city.railway_z - 4, city.size, city.railway_z + 4) if city.railway_z else Polygon()
    )
    crossings = unary_union([box(0, z - 19, city.size, z + 19) for z in city.landscape.bridge_z])
    if streets.intersection(water).difference(crossings).area > 0.01:
        errors.append("road crosses river outside designated bridges")
    underpasses = unary_union(
        [box(x - 19, 0, x + 19, city.size) for x in city.landscape.rail_crossings_x]
    )
    if streets.intersection(rail).difference(underpasses).area > 0.01:
        errors.append("road crosses railway outside underpasses")
    shapes = [building_area(b) for b in city.buildings]
    envelopes = [p.buffer(1.4, join_style="mitre") for p in shapes]
    index = STRtree(envelopes)
    for i, site in enumerate(city.sites):
        envelope = Polygon(site.polygon).buffer(1, join_style="mitre")
        if not boundary.covers(envelope) or envelope.intersects(
            unary_union([streets, water, rail])
        ):
            errors.append(f"site {i}: transport/river/boundary collision")
        if any(envelope.intersects(p) for p in envelopes):
            errors.append(f"site {i}: overlaps building")
    for i, (b, shape, envelope) in enumerate(zip(city.buildings, shapes, envelopes)):
        expected = affinity.rotate(box(b.x, b.z, b.x + b.width, b.z + b.depth), b.angle)
        if shape.symmetric_difference(expected).area > 0.001:
            errors.append(f"building {i}: footprint does not match rotation")
        if not boundary.covers(envelope) or envelope.intersects(
            unary_union([streets, water, rail])
        ):
            errors.append(f"building {i}: road/river/rail/boundary collision")
        for j in index.query(envelope):
            if j > i and envelope.intersects(envelopes[j]):
                errors.append(f"buildings {i}/{j} overlap")
        if not b.access_path or not b.entrance:
            errors.append(f"building {i}: missing road access")
            continue
        path = LineString(b.access_path)
        if (
            Point(b.entrance).distance(shape.boundary) > 0.1
            or Point(path.coords[0]).distance(Point(b.entrance)) > 0.01
        ):
            errors.append(f"building {i}: access not attached to facade")
        if (
            Point(path.coords[-1]).distance(unary_union(lines)) > 0.1
            or path.intersects(water)
            or path.intersects(rail)
        ):
            errors.append(f"building {i}: access does not reach a safe road")
        for j in index.query(path.buffer(0.7)):
            if j != i and path.buffer(0.7).intersects(envelopes[j]):
                errors.append(f"building {i}: access blocked by building {j}")
        if any(path.buffer(0.7).intersects(Polygon(site.polygon)) for site in city.sites):
            errors.append(f"building {i}: access blocked by another parcel")
    if not {"house", "apartment", "mixed_use", "shop", "school"} <= {
        b.kind for b in city.buildings
    }:
        errors.append("missing required building types")
    if errors:
        raise ValueError("Invalid organic city: " + "; ".join(errors[:15]))
    return {
        "valid": True,
        "roads_connected": True,
        "road_components": 1,
        "building_overlaps": 0,
        "building_count": len(shapes),
        "district_count": len(city.districts),
        "building_types": sorted({b.kind for b in city.buildings}),
        "frontage_validated": len(shapes),
        "achieved_building_coverage": sum(p.area for p in shapes) / city.size**2,
    }
