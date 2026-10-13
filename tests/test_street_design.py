"""Street appearance must never trade away the existing route envelope."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

from minecraft_city.building import build_city
from minecraft_city.generation import building_area, road_area
from minecraft_city.models import City, Landscape, RoadSpec
from minecraft_city.organic import river_shape
from minecraft_city.schematic import BlockVolume
from minecraft_city.street_design import (
    access_exclusion,
    render_street_furniture,
    render_street_surface,
    street_furniture,
    street_layout,
    walkability_report,
)
from minecraft_city.urban_models import UrbanPlan

ROOT = Path(__file__).parents[1]


def sample(seed=42):
    return City.model_validate_json(
        (ROOT / f"examples/natural-comparison/seed-{seed}/city.json").read_text()
    )


def junction_city():
    city = sample()
    return city.model_copy(
        update={
            "size": 128,
            "roads": [[(8, 64), (119, 64)], [(64, 64), (64, 116)], [(30, 64), (30, 108)]],
            "road_specs": [
                RoadSpec(kind="arterial", width=9),
                RoadSpec(kind="collector", width=6),
                RoadSpec(kind="alley", width=2.5),
            ],
            "buildings": [],
            "sites": [],
            "railway_z": None,
            "landscape": Landscape(),
            "urban_plan": UrbanPlan(),
        }
    )


def test_hierarchy_crossings_and_two_block_sidewalks_stay_inside_reservation():
    city = junction_city()
    unchanged = city.model_dump_json()
    layout = street_layout(city)
    assert city.model_dump_json() == unchanged
    assert layout.profiles[0].carriageway_width == 8
    assert layout.profiles[0].sidewalk_width == 2
    assert layout.profiles[-1].shared_surface
    assert np.all(layout.sidewalk[[58, 59, 68, 69], 14])
    assert np.all(layout.carriageway[60:68, 14])
    assert layout.junction_count == 2
    assert layout.marked_approaches >= 3
    assert layout.crosswalk.any() and layout.stopline.any() and layout.centerline.any()
    for marking in (layout.crosswalk, layout.stopline, layout.centerline, layout.shoulder):
        assert not np.any(marking & ~layout.carriageway)
    volume = BlockVolume(128, 12, 128)
    volume.fill(0, 3, 0, 128, 4, 128, "grass_block[snowy=false]")
    original = volume.blocks.copy()
    render_street_surface(volume, city, layout)
    assert not volume.blocks[4:].any()
    assert np.array_equal(volume.blocks[3, ~layout.reservation], original[3, ~layout.reservation])
    assert np.all(volume.blocks[3, layout.crosswalk] == volume.palette["minecraft:white_concrete"])


def test_furniture_uses_clear_verges_and_never_overwrites_existing_voxels():
    city = junction_city()
    features = street_furniture(city)
    assert features and features == street_furniture(city)
    streets = road_area(city)
    for feature in features:
        assert all(not box(x, z, x + 1, z + 1).intersects(streets) for x, _, z, _ in feature.blocks)
    volume = BlockVolume(128, 12, 128)
    volume.fill(0, 3, 0, 128, 4, 128, "grass_block[snowy=false]")
    feature = features[0]
    volume.put(feature.x, 4, feature.z, "gold_block")
    render_street_furniture(volume, city)
    assert volume.blocks[4, feature.z, feature.x] == volume.palette["minecraft:gold_block"]


@pytest.mark.parametrize("seed", [42, 7, 19])
def test_seeded_streets_have_clear_headroom_and_openable_building_approaches(seed):
    city = sample(seed)
    city.urban_plan = UrbanPlan(seed=seed)
    forbidden = unary_union(
        [
            road_area(city),
            access_exclusion(city),
            river_shape(city),
            *[building_area(b).buffer(1.4) for b in city.buildings],
            *[Polygon(s.polygon) for s in city.sites],
            box(0, city.railway_z - 4, city.size, city.railway_z + 4),
        ]
    )
    for feature in street_furniture(city):
        assert all(
            not box(x, z, x + 1, z + 1).intersects(forbidden) for x, _, z, _ in feature.blocks
        )
    before = city.model_dump_json()
    volume = build_city(city)
    assert city.model_dump_json() == before
    report = walkability_report(city, volume)
    assert report["street_clearance_obstructed_pixels"] == 0
    assert report["street_unsupported_pixels"] == 0
    assert report["building_access_obstructed_ids"] == []
    assert report["building_access_unsupported_ids"] == []
    # The audit must detect an actual obstruction instead of trusting metadata.
    point = LineString(city.buildings[0].access_path).interpolate(1)
    volume.put(int(point.x), 4, int(point.y), "stone")
    assert (
        city.buildings[0].id in walkability_report(city, volume)["building_access_obstructed_ids"]
    )
    volume.put(int(point.x), 3, int(point.y), "air")
    assert (
        city.buildings[0].id in walkability_report(city, volume)["building_access_unsupported_ids"]
    )


def test_unplanned_city_keeps_exact_v05_voxels_and_palette():
    city = sample()
    assert city.urban_plan is None
    volume = build_city(city)
    # Recorded from the unmodified v0.5 renderer on this committed city fixture.
    signature = hashlib.sha256(
        volume.blocks.tobytes() + json.dumps(volume.palette, sort_keys=True).encode()
    ).hexdigest()
    assert signature == "af23834fd734e810eac305208c0eeb6c4b62126980404ec39a474f975f3d412c"
