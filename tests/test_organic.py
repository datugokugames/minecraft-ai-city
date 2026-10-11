import math

import nbtlib
import numpy as np
import pytest
from shapely.geometry import LineString

from minecraft_city.building import build_city
from minecraft_city.comparison import compare_seeds
from minecraft_city.generation import generate_city, validate_city
from minecraft_city.models import City
from minecraft_city.planning import rule_plan
from minecraft_city.quality import evaluate_city
from minecraft_city.schematic import write_schematic


@pytest.fixture(scope="module")
def organic(blended):
    m, sources = blended
    return generate_city(m, sources, rule_plan(m, 42), 42, layout="organic")


@pytest.mark.parametrize("seed", [0, 7, 42, 2026])
@pytest.mark.parametrize("size", [128, 256, 512])
def test_natural_layout_invariants(blended, seed, size):
    m, sources = blended
    city = generate_city(m, sources, rule_plan(m, seed), seed, size, layout="organic")
    assert city.validation["roads_connected"]
    assert city.quality["frontage_access_fraction"] == 1
    assert city.quality["unclassified_road_endings"] == 0
    assert not city.quality["grid_monotony_detected"]
    assert city.quality["metrics"]["block_area_cv"] > 0.25
    assert {d.character for d in city.districts} == {
        "station",
        "old_town",
        "suburban",
        "planned",
        "industrial",
        "riverside",
    }
    if size >= 256:
        assert city.quality["station_taller_than_suburbs"]
        assert (
            city.quality["districts"]["old_town"]["mean_road_width"]
            < city.quality["districts"]["industrial"]["mean_road_width"]
        )


def test_seed_replay_and_source_features(blended, organic):
    m, sources = blended
    replay = generate_city(m, sources, rule_plan(m, 42), 42, layout="organic")
    assert organic == replay
    altered = m.model_copy(update={"mean_road_width_m": 11, "road_spacing_cv": 2})
    other = generate_city(altered, sources, rule_plan(altered, 42), 42, layout="organic")
    assert organic.road_specs != other.road_specs
    assert organic.roads != other.roads


def test_uniform_grid_is_reported(city):
    grid = city.model_copy(deep=True)
    grid.roads = [[(x, 20), (x, 220)] for x in range(20, 221, 40)] + [
        [(20, z), (220, z)] for z in range(20, 221, 40)
    ]
    report = evaluate_city(grid)
    assert report["grid_monotony_detected"]
    assert report["warnings"]


def test_broken_frontage_and_barrier_crossings_are_rejected(organic):
    broken = organic.model_copy(deep=True)
    broken.buildings[0].access_path[-1] = (0, 0)
    with pytest.raises(ValueError, match="access"):
        validate_city(broken)
    broken = organic.model_copy(deep=True)
    broken.roads.append([(20, 30), (230, 30)])
    broken.road_specs.append(broken.road_specs[0])
    with pytest.raises(ValueError, match="river"):
        validate_city(broken)


def test_rotated_buildings_have_doors_and_real_rail_clearance(organic, tmp_path):
    volume = build_city(organic)
    assert sum(abs(b.angle % 90) > 5 for b in organic.buildings) > 5
    for b in organic.buildings:
        radians = math.radians(b.angle)
        inward = np.array([-math.sin(radians), math.cos(radians)])
        x, z = np.floor(np.array(b.entrance) + inward * 0.65).astype(int)
        states = list(volume.palette)
        assert "oak_door" in states[volume.blocks[4, z, x]]
        assert "half=lower" in states[volume.blocks[4, z, x]]
        assert "half=upper" in states[volume.blocks[5, z, x]]
    path = tmp_path / "organic.schem"
    write_schematic(volume, path)
    root = nbtlib.load(path)
    # Current city palettes are under 128, so the independently decoded VarInts are 1 byte each.
    assert len(volume.palette) < 128
    assert np.array_equal(
        np.asarray(root["BlockData"], dtype=np.uint8).reshape(volume.blocks.shape), volume.blocks
    )
    rail_z = organic.railway_z
    for road in organic.roads:
        crossing = LineString(road).intersection(LineString([(0, rail_z), (organic.size, rail_z)]))
        if crossing.geom_type == "Point":
            x = int(crossing.x)
            assert volume.blocks[4:8, rail_z, x].sum() == 0
            assert volume.blocks[12, rail_z, x] != 0


def test_comparison_keeps_all_seeds(blended, tmp_path):
    m, sources = blended
    results = compare_seeds(m, sources, [7, 42], 256, tmp_path)
    assert len(results) == 2 and all(r["valid"] for r in results)
    assert (tmp_path / "comparison.png").exists()
    for seed in (7, 42):
        city = City.model_validate_json((tmp_path / f"seed-{seed}/city.json").read_text())
        assert city.layout == "organic"
