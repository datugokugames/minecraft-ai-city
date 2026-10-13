"""Quality definitions must not reward collisions or present estimates as observations."""

import json
from pathlib import Path

import pytest
from shapely.geometry import box

from minecraft_city.edit_models import digest
from minecraft_city.models import Building
from minecraft_city.project import load_project
from minecraft_city.urban_metrics import measure_urban, metric_provenance
from minecraft_city.urban_models import UrbanBlock, UrbanParcel, UrbanPlan

ROOT = Path(__file__).parents[1]


@pytest.fixture
def quality_city():
    return load_project(ROOT / "examples/quality-v05/seed-42/project.json").city


def _building(identifier, x, z, floors=2):
    return Building(
        id=identifier,
        district_id=0,
        kind="house",
        x=x,
        z=z,
        width=10,
        depth=10,
        floors=floors,
        facing="north",
        palette="modern",
    )


def test_old_city_measurement_is_repeatable_read_only_and_geometrically_valid(quality_city):
    before = digest(quality_city)
    first = measure_urban(quality_city)
    assert first == measure_urban(quality_city)
    assert digest(quality_city) == before
    json.dumps(first, allow_nan=False)
    assert first["validation"] == {"valid": True, "errors": [], "diagnostics_complete": True}
    assert first["collision_count"] == 0
    assert first["road_components"] == 1
    assert first["frontage_access_fraction"] == 1
    assert first["actual_roof_coverage"] == pytest.approx(0.060043334960937514)
    assert first["parcels"]["available"] is False
    assert first["parcel_utilization"] is None
    assert sum(h["length_share"] for h in first["road_hierarchy"].values()) == pytest.approx(1)
    assert set(first["road_hierarchy"]) >= {"arterial", "collector", "local", "alley"}
    assert sum(first["junctions"].values()) > 0
    assert len(first["districts"]) == len(quality_city.districts)
    assert "runtime" not in first and "memory" not in first


def test_overlap_cannot_inflate_roof_coverage_and_is_reported(quality_city):
    before = measure_urban(quality_city)
    duplicate = quality_city.buildings[0].model_copy(deep=True)
    duplicate.id = max(b.id for b in quality_city.buildings) + 1
    quality_city.buildings.append(duplicate)
    after = measure_urban(quality_city)
    assert after["actual_roof_coverage"] == pytest.approx(before["actual_roof_coverage"])
    assert after["building_count"] == before["building_count"] + 1
    assert after["collision_count"] >= 1
    assert not after["validation"]["valid"]
    assert any("overlap" in error for error in after["collision_events"])


def test_parcel_utilization_respects_holes_and_uses_union(quality_city):
    quality_city.buildings = [_building(123, 50, 50)]
    outer = list(box(45, 45, 65, 65).exterior.coords)
    hole = list(box(52, 52, 58, 58).exterior.coords)
    block = UrbanBlock(id="block", polygon=outer)
    parcel = UrbanParcel(
        id="one",
        block_id="block",
        district_id=0,
        polygon=outer,
        holes=[hole],
        use="residential",
        disposition="built",
        building_id=123,
    )
    # Metric correctness must not depend on a valid generator avoiding overlap.
    duplicate = parcel.model_copy(update={"id": "two"})
    quality_city.urban_plan = UrbanPlan(blocks=[block], parcels=[parcel, duplicate])
    result = measure_urban(quality_city)
    assert result["parcels"]["count"] == 2
    assert result["parcels"]["area_blocks2"] == pytest.approx(400 - 36)
    assert result["parcels"]["roof_area_inside_parcels_blocks2"] == pytest.approx(100 - 36)
    assert result["parcel_utilization"] == pytest.approx(64 / 364)


def test_invalid_geometry_reports_incomplete_diagnostics_instead_of_zero_conflicts(quality_city):
    quality_city.buildings[0].footprint = [(50, 50), (60, 60), (60, 50), (50, 60), (50, 50)]
    result = measure_urban(quality_city)
    assert result["collision_count"] is None
    assert result["actual_roof_coverage"] is None
    assert result["validation"]["diagnostics_complete"] is False
    assert any("Self-intersection" in message for message in result["validation"]["errors"])


def test_frontage_means_attached_unblocked_path_not_just_path_presence(quality_city):
    building = quality_city.buildings[0]
    building.access_path = [(0, 0), (1, 1)]
    result = measure_urban(quality_city)
    assert result["frontage_validated"] == len(quality_city.buildings) - 1
    assert result["frontage_access_fraction"] < 1
    assert any("disconnected" in error for error in result["validation"]["errors"])


def test_metric_provenance_distinguishes_tags_estimates_and_missing_observation(quality_city):
    quality_city.blended_metrics.width_tag_coverage = 0
    quality_city.blended_metrics.height_tag_coverage = 0.1
    quality_city.blended_metrics.landuse_tag_coverage = 0.02
    quality_city.blended_metrics.elevation_sample_count = 0
    result = metric_provenance(quality_city)["metrics"]
    assert result["road_sinuosity"]["status"] == "derived_from_osm_geometry"
    assert result["mean_road_width_m"]["status"] == "estimated_default"
    assert result["mean_height_m"]["status"] == "partial_tags_with_estimation"
    assert result["park_share"]["status"] == "partial_tags_with_estimation"
    assert result["terrain_relief_m"]["status"] == "unobserved"
    quality_city.sources[1]["source"] = "synthetic_sample"
    mixed = metric_provenance(quality_city)["metrics"]
    assert mixed["road_sinuosity"]["status"] == "mixed_osm_and_synthetic"
    assert mixed["mean_road_width_m"]["status"] == "mixed_osm_and_synthetic"
    assert mixed["mean_road_width_m"]["osm_component_status"] == "estimated_default"
    assert mixed["road_sinuosity"]["osm_source_weight"] == pytest.approx(0.6)
    quality_city.sources[0]["source"] = "synthetic_sample"
    assert (
        metric_provenance(quality_city)["metrics"]["mean_height_m"]["status"] == "synthetic_sample"
    )


def test_station_comparison_uses_equal_areas_and_reports_unavailable_catchments(quality_city):
    station = next(d for d in quality_city.districts if d.character == "station")
    suburban = next(d for d in quality_city.districts if d.character == "suburban")
    station.center = (20, 20)
    suburban.center = (236, 236)
    quality_city.buildings = [_building(1, 15, 15, floors=4), _building(2, 231, 231, floors=1)]
    result = measure_urban(quality_city)["station_vs_suburban"]
    assert result["available"] and result["radius_blocks"] == 20
    assert result["station"]["area_blocks2"] == pytest.approx(result["suburban"]["area_blocks2"])
    assert result["station"]["building_count"] == result["suburban"]["building_count"] == 1
    assert result["roof_coverage_difference"] == pytest.approx(0, abs=1e-10)
    assert result["floor_area_ratio_difference"] > 0
    station.center = (1, 1)
    assert not measure_urban(quality_city)["station_vs_suburban"]["available"]
    quality_city.districts.remove(station)
    assert not measure_urban(quality_city)["station_vs_suburban"]["available"]


def test_population_is_capacity_with_declared_scale_not_actual_residents(quality_city):
    quality_city.buildings = [_building(1, 50, 50, floors=2)]
    result = measure_urban(quality_city)["population_capacity_estimate"]
    assert result["status"] == "scenario_capacity_not_observed_population"
    assert result["assumptions"]["metres_per_block"] == 1
    assert result["residential_gross_floor_area_blocks2"] == 200
    assert result["persons"] == pytest.approx(200 * 0.75 / 35)
    assert result["persons_per_hectare"] == pytest.approx(result["persons"] * 10000 / 256**2)
