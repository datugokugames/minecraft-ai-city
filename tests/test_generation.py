import pytest

from minecraft_city.generation import generate_city, validate_city
from minecraft_city.models import Building
from minecraft_city.planning import rule_plan


@pytest.mark.parametrize("size", [128, 256, 512])
@pytest.mark.parametrize("seed", [0, 1, 42, 71, 2026])
def test_seeded_connected_city(blended, size, seed):
    m, sources = blended
    city = generate_city(m, sources, rule_plan(m, seed), seed, size)
    assert city.validation["roads_connected"]
    assert city.validation["building_overlaps"] == 0
    assert len(city.validation["building_types"]) == 5
    assert {d.kind for d in city.districts} == {"residential", "commercial", "park"}


def test_reproducible_and_original(blended):
    m, sources = blended
    plan = rule_plan(m, 42)
    a = generate_city(m, sources, plan, 42)
    b = generate_city(m, sources, plan, 42)
    c = generate_city(m, sources, plan, 43)
    assert a.model_dump() == b.model_dump()
    assert a.roads != c.roads
    assert a.buildings != c.buildings


def test_features_change_geometry(blended):
    m, sources = blended
    sparse = m.model_copy(
        update={
            "road_density_km_km2": 10,
            "intersection_density_km2": 30,
            "median_block_area_m2": 18000,
        }
    )
    dense = m.model_copy(
        update={
            "road_density_km_km2": 60,
            "intersection_density_km2": 900,
            "median_block_area_m2": 1000,
        }
    )
    a = generate_city(sparse, sources, rule_plan(sparse, 1), 1)
    b = generate_city(dense, sources, rule_plan(dense, 1), 1)
    assert len(b.roads) > len(a.roads)


def test_collision_is_rejected(city):
    broken = city.model_copy(deep=True)
    broken.buildings.append(Building(**{**broken.buildings[0].model_dump(), "id": 999}))
    with pytest.raises(ValueError, match="overlap"):
        validate_city(broken)


def test_disconnected_roads_rejected(city):
    broken = city.model_copy(deep=True)
    broken.roads.append([(5.0, 230.0), (5.0, 240.0)])
    with pytest.raises(ValueError, match="disconnected"):
        validate_city(broken)


def test_building_on_road_rejected(city):
    broken = city.model_copy(deep=True)
    broken.buildings[0].x = 18
    broken.buildings[0].z = 18
    with pytest.raises(ValueError, match="intersects road"):
        validate_city(broken)


@pytest.mark.parametrize("size", [128, 256])
@pytest.mark.parametrize("spacing", [32, 64])
@pytest.mark.parametrize("seed", range(10))
def test_extreme_plan_retains_buildable_lots(blended, size, spacing, seed):
    m, sources = blended
    plan = rule_plan(m, seed).model_copy(
        update={"road_spacing": spacing, "road_width": 9, "irregularity": 0.3}
    )
    city = generate_city(m, sources, plan, seed, size)
    assert len(city.validation["building_types"]) == 5
