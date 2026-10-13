"""Deterministic city-planning diagnostics; never changes a city or its world.

One horizontal block is treated as one metre for density/capacity comparisons.
OSM observations, inferred parameters and generated-world measurements are separate.
"""

from __future__ import annotations

from collections import Counter

import networkx as nx
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union
from shapely.strtree import STRtree
from shapely.validation import explain_validity

from .generation import building_area, road_area, validate_city
from .morphology import measure_network
from .organic import river_shape
from .urban_quality import measure_quality, roof_footprint


def _field(record, name, default=None):
    return record.get(name, default) if isinstance(record, dict) else getattr(record, name, default)


def _polygon(record):
    return Polygon(_field(record, "polygon", []), _field(record, "holes", []))


def metric_provenance(city):
    """Report input quality without describing a generated city as an OSM observation."""
    sources = city.sources
    total_weight = sum(float(s.get("weight", 1)) for s in sources)
    osm_weight = sum(float(s.get("weight", 1)) for s in sources if s.get("source") == "osm")
    osm_fraction = osm_weight / total_weight if total_weight > 0 else 0.0
    metrics = city.blended_metrics.model_dump()
    coverage_keys = {
        "mean_height_m": "height_tag_coverage",
        "mean_road_width_m": "width_tag_coverage",
        "residential_share": "landuse_tag_coverage",
        "commercial_share": "landuse_tag_coverage",
        "park_share": "landuse_tag_coverage",
    }
    result = {}
    for name, value in metrics.items():
        record = {"value": value, "osm_source_weight": osm_fraction}
        if osm_fraction == 0:
            record["status"] = "synthetic_sample" if sources else "unknown_source"
        elif osm_fraction < 1 - 1e-9:
            record["status"] = "mixed_osm_and_synthetic"
        else:
            record["status"] = "derived_from_osm_geometry"
        if name in coverage_keys:
            coverage = metrics[coverage_keys[name]]
            record["tag_coverage"] = coverage
            record["tag_coverage_metric"] = coverage_keys[name]
            if osm_fraction > 0:
                record["osm_component_status"] = (
                    "estimated_default"
                    if coverage == 0
                    else "partial_tags_with_estimation"
                    if coverage < 1
                    else "derived_from_tags"
                )
                if osm_fraction >= 1 - 1e-9:
                    record["status"] = record["osm_component_status"]
        if name == "terrain_relief_m":
            count = metrics["elevation_sample_count"]
            record["sample_count"] = count
            if osm_fraction > 0:
                record["osm_component_status"] = (
                    "sparse_elevation_tags" if count >= 2 else "unobserved"
                )
                if osm_fraction >= 1 - 1e-9:
                    record["status"] = record["osm_component_status"]
        result[name] = record
    return {
        "metrics": result,
        "sources": [dict(source) for source in sources],
        "notes": [
            "Geometry-derived OSM metrics describe mapped features, not surveyed ground truth.",
            "Tag coverage is a blended input statistic; height levels may be converted to metres.",
            "Land-use tags cover only mapped polygons; uncovered land is not observed land use.",
            "OSM 2D graph intersections do not establish real traffic connectivity at overpasses.",
            "Sparse elevation tags are not a DEM; zero relief without samples is unobserved.",
        ],
    }


def _preflight(city, parcels, blocks):
    errors = []
    records = [(f"district {d.id}", d) for d in city.districts]
    records += [(f"site {i}", s) for i, s in enumerate(city.sites)]
    records += [(f"parcel {i}", p) for i, p in enumerate(parcels)]
    records += [(f"block {i}", p) for i, p in enumerate(blocks)]
    for label, record in records:
        try:
            geometry = _polygon(record)
        except (TypeError, ValueError) as error:
            errors.append(f"{label}: invalid polygon: {error}")
            continue
        if geometry.is_empty or not geometry.is_valid or geometry.area <= 0:
            errors.append(f"{label}: {explain_validity(geometry)}; area={geometry.area}")
    for b in city.buildings:
        try:
            geometries = [building_area(b), roof_footprint(b)]
        except (TypeError, ValueError) as error:
            errors.append(f"building {b.id}: invalid footprint: {error}")
            continue
        for shape in geometries:
            if shape.is_empty or not shape.is_valid or shape.area <= 0:
                errors.append(f"building {b.id}: {explain_validity(shape)}; area={shape.area}")
                break
    for i, coords in enumerate(city.roads):
        if len(coords) < 2 or LineString(coords).length <= 0:
            errors.append(f"road {i}: missing positive-length geometry")
    if city.layout == "organic" and len(city.roads) != len(city.road_specs):
        errors.append("road geometry/specification counts do not match")
    return errors


def _catchment(city, roofs, center, radius):
    region = Point(center).buffer(radius, quad_segs=32)
    clipped = [shape.intersection(region) for shape in roofs]
    count = sum(region.covers(shape.representative_point()) for shape in roofs)
    return {
        "center": list(center),
        "area_blocks2": region.area,
        "building_count": count,
        "buildings_per_hectare": count * 10000 / region.area,
        "roof_coverage": unary_union(clipped).area / region.area,
        "gross_floor_area_ratio_estimate": sum(
            shape.area * b.floors for shape, b in zip(clipped, city.buildings)
        )
        / region.area,
    }


def _station_comparison(city, roofs):
    station = next((d for d in city.districts if d.character == "station" and d.center), None)
    suburb = next((d for d in city.districts if d.character == "suburban" and d.center), None)
    if station is None or suburb is None:
        return {"available": False, "reason": "station/suburban district centers unavailable"}
    # Equal, nonoverlapping disks wholly inside the map remove area and edge-clipping bias.
    centers = [station.center, suburb.center]
    radius = min(
        32.0,
        city.size / 6,
        Point(centers[0]).distance(Point(centers[1])) / 2,
        *(min(x, z, city.size - x, city.size - z) for x, z in centers),
    )
    if radius < 4:
        return {"available": False, "reason": "insufficient room for comparable catchments"}
    a, b = [_catchment(city, roofs, center, radius) for center in centers]
    return {
        "available": True,
        "radius_blocks": radius,
        "station": a,
        "suburban": b,
        "roof_coverage_difference": a["roof_coverage"] - b["roof_coverage"],
        "floor_area_ratio_difference": a["gross_floor_area_ratio_estimate"]
        - b["gross_floor_area_ratio_estimate"],
        "method": "equal-radius, nonoverlapping disks inside the city; roof area is clipped",
    }


def _geometry_checks(city, footprints, streets, water, rail):
    """Count collision events independently of the legacy validator's truncated message."""
    boundary = box(0, 0, city.size, city.size)
    envelopes = [
        p.buffer(1.4 if city.layout == "organic" else 1, join_style="mitre") for p in footprints
    ]
    index = STRtree(envelopes)
    sites = [Polygon(s.polygon) for s in city.sites]
    hazards = unary_union([streets, water, rail])
    road_lines = unary_union([LineString(r) for r in city.roads])
    collisions = []
    access_errors = []
    valid_access = 0
    for i, (building, shape, envelope) in enumerate(zip(city.buildings, footprints, envelopes)):
        if not boundary.covers(envelope) or envelope.intersects(hazards):
            collisions.append(f"building {building.id}: road/river/rail/boundary collision")
        for j in index.query(envelope):
            if j > i and envelope.intersects(envelopes[j]):
                collisions.append(f"buildings {building.id}/{city.buildings[j].id}: overlap")
        for j, site in enumerate(sites):
            if envelope.intersects(site.buffer(1, join_style="mitre")):
                collisions.append(f"building {building.id}/site {j}: overlap")
        if not building.entrance or len(building.access_path) < 2:
            access_errors.append(f"building {building.id}: missing explicit entrance/access path")
            continue
        path = LineString(building.access_path)
        entrance = Point(building.entrance)
        attached = (
            entrance.distance(shape.boundary) <= 0.1
            and Point(path.coords[0]).distance(entrance) <= 0.01
            and Point(path.coords[-1]).distance(road_lines) <= 0.1
        )
        blocked = path.intersects(water) or path.intersects(rail)
        blocked |= any(
            j != i and path.buffer(0.7).intersects(envelopes[j])
            for j in index.query(path.buffer(0.7))
        )
        blocked |= any(path.buffer(0.7).intersects(site) for site in sites)
        if attached and not blocked:
            valid_access += 1
        else:
            access_errors.append(f"building {building.id}: disconnected or blocked entrance path")
    for i, site in enumerate(sites):
        envelope = site.buffer(1, join_style="mitre")
        if not boundary.covers(envelope) or envelope.intersects(hazards):
            collisions.append(f"site {i}: road/river/rail/boundary collision")
        for j, other in enumerate(sites):
            if j > i and envelope.intersects(other.buffer(1, join_style="mitre")):
                collisions.append(f"sites {i}/{j}: overlap")
    if city.layout == "organic":
        crossings = unary_union(
            [box(0, z - 19, city.size, z + 19) for z in city.landscape.bridge_z]
        )
        underpasses = unary_union(
            [box(x - 19, 0, x + 19, city.size) for x in city.landscape.rail_crossings_x]
        )
        if streets.intersection(water).difference(crossings).area > 0.01:
            collisions.append("road crosses river outside designated bridges")
        if streets.intersection(rail).difference(underpasses).area > 0.01:
            collisions.append("road crosses railway outside underpasses")
    if not boundary.covers(streets):
        collisions.append("roads exceed city boundary")
    return collisions, access_errors, valid_access


def measure_urban(city):
    """Return JSON-serializable quality metrics, or explicit incomplete-geometry errors.

    Fractions use polygon unions, so overlapping input cannot inflate coverage.
    Reported roof projections exclude eaves and rasterization; floor area/capacity are
    scenario estimates, not counts of actual residents or usable interior blocks.
    """
    plan = getattr(city, "urban_plan", None)
    parcels = _field(plan, "parcels", []) or []
    blocks = _field(plan, "blocks", []) or []
    preflight = _preflight(city, parcels, blocks)
    report = {
        "schema_version": 1,
        "input_metric_provenance": metric_provenance(city),
        "validation": {"valid": False, "errors": preflight, "diagnostics_complete": not preflight},
    }
    if preflight:
        report.update(actual_roof_coverage=None, collision_count=None)
        return report
    boundary = box(0, 0, city.size, city.size)
    area = boundary.area
    roofs = [roof_footprint(b).intersection(boundary) for b in city.buildings]
    footprints = [building_area(b) for b in city.buildings]
    roof_union = unary_union(roofs)
    streets = road_area(city)
    water = river_shape(city)
    rail = (
        box(0, city.railway_z - 4, city.size, city.railway_z + 4)
        if city.railway_z is not None
        else Polygon()
    )
    sites = [Polygon(site.polygon) for site in city.sites]
    collisions, access_errors, valid_access = _geometry_checks(
        city, footprints, streets, water, rail
    )
    errors = collisions + access_errors
    try:
        validate_city(city)
    except ValueError as error:
        errors.append(str(error))
    report["validation"] = {"valid": not errors, "errors": errors, "diagnostics_complete": True}
    exterior = measure_quality(city)
    report.update(exterior)
    report["actual_roof_coverage"] = roof_union.area / area
    report["roof_projection_area_blocks2"] = roof_union.area
    report["registered_open_space_fraction"] = unary_union(sites).intersection(boundary).area / area
    report["collision_count"] = len(collisions)
    report["collision_events"] = collisions
    report["frontage_access_fraction"] = valid_access / max(1, len(city.buildings))
    report["frontage_validated"] = valid_access
    report["districts"] = {}
    for district in city.districts:
        region = Polygon(district.polygon).intersection(boundary)
        clipped = [shape.intersection(region) for shape in roofs]
        count = sum(region.covers(shape.representative_point()) for shape in roofs)
        report["districts"][str(district.id)] = {
            "character": district.character,
            "use": district.kind,
            "area_blocks2": region.area,
            "building_count": count,
            "buildings_per_hectare": count * 10000 / max(1, region.area),
            "roof_coverage": unary_union(clipped).area / max(1, region.area),
            "gross_floor_area_ratio_estimate": sum(
                shape.area * b.floors for shape, b in zip(clipped, city.buildings)
            )
            / max(1, region.area),
        }
    lines = [LineString(r) for r in city.roads]
    network, _, graph = measure_network(lines)
    junction_count = sum(degree >= 3 for _, degree in graph.degree())
    hierarchy = Counter()
    for i, line in enumerate(lines):
        kind = city.road_specs[i].kind if i < len(city.road_specs) else "unclassified_legacy"
        hierarchy[kind] += line.length
    total_length = sum(hierarchy.values())
    report.update(
        road_components=nx.number_connected_components(graph),
        road_length_blocks=total_length,
        road_hierarchy={
            kind: {
                "length_blocks": hierarchy[kind],
                "length_share": hierarchy[kind] / max(1e-9, total_length),
            }
            for kind in sorted(set(hierarchy) | {"arterial", "collector", "local", "alley"})
        },
        junctions={
            name: round(network[f"{key}_junction_share"] * junction_count)
            for name, key in [("t", "t"), ("cross", "cross"), ("irregular", "irregular")]
        },
        dead_ends=sum(degree == 1 for _, degree in graph.degree()),
        road_spacing_cv=network["road_spacing_cv"],
        block_area_cv=network["block_area_cv"],
        block_count=len(blocks),
    )
    parcel_polygons = [_polygon(parcel).intersection(boundary) for parcel in parcels]
    parcel_union = unary_union(parcel_polygons)
    parcel_area = parcel_union.area
    report["parcels"] = {
        "available": plan is not None,
        "count": len(parcels),
        "area_blocks2": parcel_area,
        "disposition_counts": dict(Counter(_field(p, "disposition", "unknown") for p in parcels)),
        "roof_area_inside_parcels_blocks2": roof_union.intersection(parcel_union).area,
        "utilization": roof_union.intersection(parcel_union).area / parcel_area
        if parcel_area
        else None,
        "definition": "union of roof projections inside all recorded parcels / union of parcel areas; holes excluded",
    }
    report["parcel_utilization"] = report["parcels"]["utilization"]
    occupied = unary_union([streets, water, rail, *footprints, *sites, parcel_union])
    report["open_land"] = {
        "park_area_blocks2": unary_union(
            [Polygon(s.polygon) for s in city.sites if s.kind == "park"]
        )
        .intersection(boundary)
        .area,
        "registered_area_blocks2": unary_union(sites).intersection(boundary).area,
        "by_use_area_blocks2": {
            kind: unary_union([Polygon(s.polygon) for s in city.sites if s.kind == kind])
            .intersection(boundary)
            .area
            for kind in ["park", "parking", "plaza", "vacant"]
        },
        "unallocated_area_blocks2": boundary.difference(occupied).area,
        "unallocated_definition": "city minus road+sidewalk, river, rail corridor, reserved building sites, registered sites and planned parcels",
    }
    residential_floor_area = 0.0
    for building, roof in zip(city.buildings, roofs):
        category = building.architecture.category if building.architecture else building.kind
        share = (
            1
            if category in {"house", "apartment", "rental_apartment"}
            else 0.5
            if category == "mixed_use"
            else 0
        )
        residential_floor_area += roof.area * building.floors * share
    report["population_capacity_estimate"] = {
        "persons": residential_floor_area * 0.75 / 35,
        "persons_per_hectare": residential_floor_area * 0.75 / 35 * 10000 / area,
        "residential_gross_floor_area_blocks2": residential_floor_area,
        "assumptions": {
            "metres_per_block": 1,
            "usable_floor_fraction": 0.75,
            "usable_m2_per_person": 35,
            "mixed_use_residential_fraction": 0.5,
        },
        "status": "scenario_capacity_not_observed_population",
        "limitations": "Roof projection times floors is an envelope estimate; stepped masses, stairs and interior geometry are not resolved.",
    }
    report["station_vs_suburban"] = _station_comparison(city, roofs)
    report["notes"] += [
        "Coverage uses clipped polygon unions; boundaries and parcel holes are respected.",
        "Building counts use footprint representative points; floor area is geometrically clipped.",
        "Collision events count incompatible object pairs/categories, not collided voxel counts.",
        "Runtime and process memory belong in execution reports, not deterministic city state.",
    ]
    return report
