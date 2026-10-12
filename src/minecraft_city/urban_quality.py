"""Observable exterior/land-use metrics and bounded offline city improvement."""

from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from shapely import affinity
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from .architecture_models import ArchitectureRequest, FacadeRecipe
from .edit_geometry import compute_delta
from .edit_models import EditDelta, EditIntent, Selection, digest
from .editing import materialize
from .exporting import export_city
from .generation import building_area, road_area
from .models import save_json
from .project import city_digest, save_project
from .quality import evaluate_city


def roof_footprint(b):
    if b.architecture is None:
        return building_area(b)
    spec = b.architecture
    shape = unary_union([box(m.x, m.z, m.x + m.width, m.z + m.depth) for m in spec.masses])
    q = {"north": 0, "east": 1, "south": 2, "west": 3}[b.facing]
    shape = affinity.rotate(shape, q * 90, origin=(0, 0))
    ox, oz = [
        (0, 0),
        (spec.site_depth, 0),
        (spec.site_width, spec.site_depth),
        (0, spec.site_width),
    ][q]
    return affinity.translate(shape, b.x + ox, b.z + oz)


def measure_quality(city):
    roofs = [roof_footprint(b) for b in city.buildings]
    recipes = [b.facade for b in city.buildings if b.facade is not None]
    roof_styles = Counter(
        b.architecture.roof
        if b.architecture
        else b.facade.roof
        if b.facade
        else "gable"
        if b.palette == "traditional" or b.kind == "house"
        else "flat"
        for b in city.buildings
    )
    shapes = Counter(
        b.architecture.shape if b.architecture else "rectangle" for b in city.buildings
    )
    signatures = {
        (
            round(p.area, 1),
            round(p.length**2 / max(p.area, 1), 2),
            b.floors,
            b.architecture.roof
            if b.architecture
            else b.facade.roof
            if b.facade
            else "gable"
            if b.kind == "house" or b.palette == "traditional"
            else "flat",
        )
        for p, b in zip(roofs, city.buildings)
    }
    area = sum(p.area for p in roofs)
    streets = road_area(city).area
    site_counts = Counter(s.kind for s in city.sites)
    return {
        "actual_roof_coverage": area / city.size**2,
        "reserved_building_parcel_coverage": sum(building_area(b).area for b in city.buildings)
        / city.size**2,
        "building_count": len(roofs),
        "shape_types": dict(shapes),
        "shape_type_count": len(shapes),
        "roof_types": dict(roof_styles),
        "roof_type_count": len(roof_styles),
        "form_signature_count": len(signatures),
        "facade_recipe_fraction": len(recipes) / max(1, len(city.buildings)),
        "explicit_facade_lighting_fraction": sum(r.lighting for r in recipes)
        / max(1, len(city.buildings)),
        "window_pattern_count": len(
            {
                (
                    b.architecture.windows.spacing,
                    b.architecture.windows.width,
                    b.architecture.windows.height,
                )
                if b.architecture
                else (b.facade.window_spacing, b.facade.window_width, 2)
                if b.facade
                else (3, 2, 2)
                for b in city.buildings
            }
        ),
        "road_surface_fraction": streets / city.size**2,
        "site_counts": dict(site_counts),
        "registered_open_space_fraction": sum(Polygon(s.polygon).area for s in city.sites)
        / city.size**2,
        "footprint_area_cv": float(
            np.std([p.area for p in roofs]) / np.mean([p.area for p in roofs])
        )
        if roofs
        else 0,
        "district_roof_coverage": {
            d.character: sum(
                p.intersection(Polygon(d.polygon)).area for p, b in zip(roofs, city.buildings)
            )
            / max(1, Polygon(d.polygon).area)
            for d in city.districts
        },
        "notes": [
            "Roof coverage measures mass projection, not the entire reserved yard.",
            "Signatures are diagnostics, not a human realism score.",
        ],
    }


def improve(project, seed=42, target_coverage=0.09, max_additions=24):
    if project.city.layout != "organic":
        raise ValueError("quality improvement requires organic city")
    if (
        not 0 <= seed <= 2**63 - 1
        or not 0.03 <= target_coverage <= 0.2
        or not 0 <= max_additions <= 48
    ):
        raise ValueError("seed/coverage/addition limit invalid")
    working = project.model_copy(deep=True)
    rng = np.random.default_rng(seed)
    before = measure_quality(project.city)
    additions = []
    errors = []
    districts = [
        d
        for d in working.districts
        if d.city_district_id is not None
        and next(p for p in working.city.districts if p.id == d.city_district_id).kind != "park"
    ]
    # A connected L-shaped public/commercial infill adds actual footprint diversity.
    for district in districts:
        if len(additions) >= min(2, max_additions):
            break
        req = ArchitectureRequest(category="public", width=18, depth=22, mode="local")
        intent = EditIntent(
            action="add_architecture", selection=Selection(district=district.id), architecture=req
        )
        try:
            delta = compute_delta(working, intent, seed)
            working.city.buildings += delta.added_buildings
            additions += delta.added_buildings
        except ValueError as error:
            errors.append(type(error).__name__)
    attempts = 0
    while (
        len(additions) < max_additions
        and measure_quality(working.city)["actual_roof_coverage"] < target_coverage
        and attempts < max(10, len(districts) * 4)
    ):
        district = districts[attempts % len(districts)]
        physical = next(d for d in working.city.districts if d.id == district.city_district_id)
        kind = "apartment" if physical.kind in {"commercial", "industrial"} else "house"
        intent = EditIntent(
            action="add_buildings", building_type=kind, selection=Selection(district=district.id)
        )
        try:
            delta = compute_delta(working, intent, seed + attempts)
            for b in delta.added_buildings:
                if physical.kind == "industrial":
                    b.kind = "factory"
                    b.floors = 2
            working.city.buildings += delta.added_buildings
            additions += delta.added_buildings
        except ValueError as error:
            errors.append(type(error).__name__)
        attempts += 1
    result = materialize(project, EditDelta(added_buildings=additions))
    # All old IDs, footprints, road connections and registered open parcels are retained.
    for b in result.city.buildings:
        if b.architecture is not None:
            continue
        character = next(d.character for d in result.city.districts if d.id == b.district_id)
        choices = ["gable", "flat"] if b.kind == "house" else ["flat", "terrace"]
        if character == "old_town" and b.kind == "house":
            choices = ["gable", "gable", "flat"]
        b.facade = FacadeRecipe(
            roof=str(rng.choice(choices)),
            window_spacing=int(rng.integers(3, 6)),
            window_width=int(rng.integers(1, 3)),
            balconies=b.kind in {"apartment", "mixed_use"},
            canopy=b.kind in {"shop", "school", "station"},
            signage=b.kind in {"shop", "mixed_use", "station", "factory"},
            lighting=True,
        )
        b.variant = int(rng.integers(0, 8))
    result.city.quality = evaluate_city(result.city)
    result.city.quality["exterior_quality"] = measure_quality(result.city)
    result.city_sha256 = city_digest(result.city)
    result = type(project).model_validate(result.model_dump())
    after = measure_quality(result.city)
    return result, {
        "seed": seed,
        "before": before,
        "after": after,
        "added_buildings": len(additions),
        "target_roof_coverage": target_coverage,
        "target_reached": after["actual_roof_coverage"] >= target_coverage,
        "attempts": attempts,
        "rejected_candidates": len(errors),
        "roads_unchanged": project.city.roads == result.city.roads,
        "sites_unchanged": project.city.sites == result.city.sites,
        "existing_ids_preserved": all(
            any(
                b.id == old.id and building_area(b).equals(building_area(old))
                for b in result.city.buildings
            )
            for old in project.city.buildings
        ),
        "source_digest": digest(project),
        "result_digest": digest(result),
    }


def export_improvement(project, output, seed=42, target_coverage=0.09, max_additions=24):
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    improved, report = improve(project, seed, target_coverage, max_additions)
    output.mkdir(parents=True, exist_ok=False)
    export_city(project.city.model_copy(deep=True), output / "before")
    export_city(improved.city.model_copy(deep=True), output / "after")
    save_project(output / "after/project.json", improved)
    save_json(output / "quality-comparison.json", report)
    with (
        Image.open(output / "before/preview.png") as a,
        Image.open(output / "after/preview.png") as b,
    ):
        canvas = Image.new("RGB", (a.width + b.width, a.height + 32), "#f3f1e9")
        canvas.paste(a, (0, 32))
        canvas.paste(b, (a.width, 32))
        draw = ImageDraw.Draw(canvas)
        draw.text((20, 10), "BEFORE", fill="#294644")
        draw.text((a.width + 20, 10), "v0.5 / QUALITY IMPROVEMENT", fill="#294644")
        canvas.save(output / "comparison.png")
    return report
