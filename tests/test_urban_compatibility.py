"""Stored pre-v0.6 approvals must remain valid after optional urban planning is added."""

import json
from pathlib import Path

import pytest

from minecraft_city.building import build_city
from minecraft_city.edit_models import digest
from minecraft_city.editing import load_plan, verify_plan
from minecraft_city.models import City
from minecraft_city.project import city_digest, load_project
from minecraft_city.schematic import write_schematic

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize(
    "relative",
    [
        "examples/managed-city/project.json",
        "examples/quality-v05/seed-42/project.json",
        "examples/phase5/city-edit/edited-project.json",
    ],
)
def test_stored_projects_keep_original_snapshot_and_revision(relative):
    path = ROOT / relative
    raw = json.loads(path.read_text(encoding="utf-8"))
    project = load_project(path)
    assert project.city.urban_plan is None
    assert "urban_plan" not in project.city.model_dump(mode="json")
    assert city_digest(project.city) == raw["city_sha256"]
    assert digest(project) == digest(raw)


@pytest.mark.parametrize(
    "relative",
    ["examples/natural-city/city.json", "examples/managed-city/city.json"],
)
def test_stored_city_contract_does_not_gain_default_planning_fields(relative):
    raw = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    city = City.model_validate(raw)
    assert city.model_dump(mode="json") == raw


def test_pre_v06_saved_plan_still_validates_against_its_original_snapshot():
    review = ROOT / "examples/phase3a/convenience/review"
    plan = load_plan(review / "plan.json")
    before = load_project(review / "before.project.json")
    after = verify_plan(before, plan)
    assert digest(before) == plan.base_project_sha256
    assert digest(after) == plan.after_project_sha256
    assert after.city.urban_plan is None


def test_old_city_rendering_is_byte_identical_to_saved_schematic(tmp_path):
    source = ROOT / "examples/quality-v05/seed-42"
    city = load_project(source / "project.json").city
    target = tmp_path / "city.schem"
    write_schematic(
        build_city(city),
        target,
        city.plan.name,
        "; ".join(sorted({s["attribution"] for s in city.sources})),
    )
    assert target.read_bytes() == (source / "city.schem").read_bytes()
