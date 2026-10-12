from pathlib import Path

import nbtlib
import pytest

from minecraft_city.building import build_city
from minecraft_city.edit_models import digest
from minecraft_city.models import City
from minecraft_city.project import create_project, load_project
from minecraft_city.urban_quality import export_improvement, improve, measure_quality

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize("seed", [42, 7, 19])
def test_multi_seed_density_shape_and_geometry(seed):
    city = City.model_validate_json(
        (ROOT / f"examples/natural-comparison/seed-{seed}/city.json").read_text()
    )
    project = create_project(city)
    before = digest(project)
    improved, report = improve(project, seed=seed, max_additions=12)
    assert digest(project) == before
    assert report["after"]["actual_roof_coverage"] > report["before"]["actual_roof_coverage"]
    assert report["after"]["roof_type_count"] >= report["before"]["roof_type_count"]
    assert report["after"]["window_pattern_count"] >= 3
    assert (
        report["roads_unchanged"] and report["sites_unchanged"] and report["existing_ids_preserved"]
    )
    assert improved.city.validation["building_overlaps"] == 0
    assert measure_quality(improved.city)["facade_recipe_fraction"] > 0.8
    volume = build_city(improved.city)
    assert volume.blocks.shape[1:] == (256, 256)
    assert not any("command_block" in state or "barrel" in state for state in volume.palette)


def test_repeatable_recipes_preserve_source_and_output(tmp_path):
    project = load_project(ROOT / "examples/managed-city/project.json")
    first, _report = improve(project, seed=42, max_additions=8)
    again, _ = improve(project, seed=42, max_additions=8)
    assert digest(first) == digest(again)
    different, _ = improve(project, seed=7, max_additions=8)
    assert digest(first) != digest(different)
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "keep").write_text("original")
    with pytest.raises(FileExistsError):
        export_improvement(project, existing)
    assert (existing / "keep").read_text() == "original"
    output = tmp_path / "new"
    export_improvement(project, output, max_additions=2)
    assert (output / "comparison.png").exists()
    assert nbtlib.load(output / "after/city.schem")["Version"] == 2
    assert load_project(output / "after/project.json").city.buildings[:51] != project.city.buildings
    assert project.city.buildings[0].facade is None


def test_metrics_separate_reserved_yard_from_building_mass():
    from minecraft_city.editing import prepare_edit

    project = load_project(ROOT / "examples/managed-city/project.json")
    _, after = prepare_edit(project, "青葉市に30×40のマンションを建てて", mode="rules")
    quality = measure_quality(after.city)
    assert quality["actual_roof_coverage"] < quality["reserved_building_parcel_coverage"]
