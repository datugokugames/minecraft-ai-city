import json
from pathlib import Path

import nbtlib
import numpy as np
import pytest

from minecraft_city.architecture import (
    design,
    local_design,
    parse_architecture,
    templates,
    validate_request_design,
)
from minecraft_city.architecture_geometry import render_architecture
from minecraft_city.architecture_models import ArchitectureRequest, ArchitectureSpec
from minecraft_city.edit_models import ClarificationRequired, Selection, digest
from minecraft_city.editing import prepare_edit, verify_plan
from minecraft_city.project import load_project
from minecraft_city.schematic import write_schematic

SOURCE = Path(__file__).parents[1] / "examples/managed-city/project.json"


@pytest.mark.parametrize(
    "text,category,w,d,h",
    [
        ("ここに20×30のコンビニを作って", "convenience_store", 20, 30, None),
        ("ここに30×40のマンションを建てて", "apartment", 30, 40, None),
        ("ここに40×50、高さ60ブロックのオフィスビルを作って", "office", 40, 50, 60),
        ("この土地に25×35の住宅を建てて", "house", 25, 35, None),
        ("ここに幅20、奥行き30の商業施設を作って", "commercial", 20, 30, None),
        ("この場所に周囲と調和する建物を作って", "office", 32, 40, None),
    ],
)
def test_block_dimensions_japanese(text, category, w, d, h):
    req, _ = parse_architecture(text, load_project(SOURCE), Selection(point=(70, 70)))
    assert (req.category, req.width, req.depth, req.height) == (category, w, d, h)


@pytest.mark.parametrize(
    "text",
    [
        "ここに幅20の住宅を作って",
        "ここに20×30と30×40の住宅を作って",
        "ここに3×4の住宅を作って",
        "ここに20×30、高さ200のオフィスを作って",
        "ここに20×30の住宅を建てないで",
        "ここに20×30の住宅と駅を作って",
    ],
)
def test_unsafe_or_ambiguous_sizes_fail(text):
    with pytest.raises(ValueError):
        parse_architecture(text, load_project(SOURCE), Selection(point=(70, 70)))


def test_deictic_location_and_explicit_conflicts():
    with pytest.raises(ClarificationRequired):
        parse_architecture("ここに20×30のコンビニを作って", load_project(SOURCE))
    with pytest.raises(ClarificationRequired):
        parse_architecture(
            "ここに20×30のコンビニを作って",
            load_project(SOURCE),
            Selection(point=(70, 70)),
            ArchitectureRequest(category="house", width=20, depth=30),
        )


@pytest.mark.parametrize("category", list(templates()))
@pytest.mark.parametrize("size", [(18, 22), (30, 40)])
def test_categories_hollow_exterior_and_bounds(category, size, tmp_path):
    request = ArchitectureRequest(category=category, width=size[0], depth=size[1])
    spec = local_design(request, 42)
    validate_request_design(spec, request, 42, "north")
    v = render_architecture(spec)
    assert v.blocks.shape == (spec.height + 1, size[1], size[0])
    assert np.any(v.blocks[spec.height])  # explicit height includes the highest roof/parapet
    assert np.count_nonzero(v.blocks) < 50000
    mass = spec.masses[0]
    assert v.blocks[2, mass.z + 2, mass.x + 2] == 0
    states = set(v.palette)
    assert any("oak_door" in name and "half=lower" in name for name in states)
    assert not any("crafting_table" in name or "chest" in name for name in states)
    door = v.blocks[2, min(m.z for m in spec.masses), spec.entrance_x]
    assert "oak_door" in list(v.palette)[int(door)]
    out = tmp_path / "building.schem"
    write_schematic(v, out)
    schematic = nbtlib.load(out)
    assert int(schematic["Version"]) == 2 and int(schematic["Width"]) == size[0]


def test_repeatability_and_seed_variation():
    req = ArchitectureRequest(category="apartment", width=30, depth=40)
    first = render_architecture(local_design(req, 42))
    repeated = render_architecture(local_design(req, 42))
    assert first.palette == repeated.palette and np.array_equal(first.blocks, repeated.blocks)
    variants = {digest(local_design(req, seed)) for seed in range(6)}
    assert len(variants) == 6


def test_malicious_design_and_count_limit():
    req = ArchitectureRequest(category="office", width=30, depth=40)
    spec = local_design(req, 42)
    for patch in [
        {"wall": "command_block"},
        {"command": "op @a"},
        {"height": 400},
        {"entrance_x": 63},
        {"site_width": 500},
    ]:
        with pytest.raises(ValueError):
            ArchitectureSpec.model_validate({**spec.model_dump(), **patch})
    changed = spec.model_copy(update={"seed": 99})
    with pytest.raises(ValueError):
        validate_request_design(changed, req, 42, "north")
    with pytest.raises(ValueError, match="上限"):
        render_architecture(spec, max_blocks=10)


def test_llm_validation_fallback_and_usage(monkeypatch):
    monkeypatch.setenv("ARCHITECTURE_API_KEY", "test-not-real")
    req = ArchitectureRequest(category="office", width=30, depth=40, mode="llm")
    local = local_design(req, 42)

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "choices": [{"message": {"content": json.dumps(self.content)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
            }

    class FakeSession:
        def post(self, *args, **kwargs):
            assert kwargs["timeout"] == (10, 45)
            response = FakeResponse()
            response.content = self.content
            return response

    session = FakeSession()
    session.content = local.model_dump()
    got, usage = design(req, 42, session=session)
    assert got == local and usage["usage"]["total_tokens"] == 300 and usage["source"] == "llm"
    session.content = {**local.model_dump(), "wall": "command_block"}
    got, usage = design(req, 42, session=session)
    assert got == local and usage["source"] == "local_fallback"
    session.content = {**local.model_dump(), "site_width": 50}
    assert design(req, 42, session=session)[1]["source"] == "local_fallback"


def test_old_project_digest_unchanged():
    raw = json.loads(SOURCE.read_text())
    assert digest(load_project(SOURCE)) == digest(raw)


def test_template_edit_validation(tmp_path):
    raw = {"schema_version": 1, "templates": [templates()["house"].model_dump(mode="json")]}
    target = tmp_path / "templates.json"
    target.write_text(json.dumps(raw))
    assert templates(target)["house"].label == "戸建住宅"
    raw["templates"][0]["walls"] = ["command_block"]
    target.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        templates(target)


def test_city_architecture_plan_approval_collision_and_preservation(tmp_path):
    from minecraft_city.building import build_city
    from minecraft_city.editing import apply_edit, undo_edit, write_preview

    project = load_project(SOURCE)
    plan, after = prepare_edit(project, "青葉市に30×40のマンションを建てて", mode="rules")
    repeated, _ = prepare_edit(project, "青葉市に30×40のマンションを建てて", mode="rules")
    assert plan == repeated and plan.engine_version == "phase5-1"
    assert project.city.buildings == after.city.buildings[:-1]
    assert project.city.roads == after.city.roads
    building = after.city.buildings[-1]
    point = (building.x + building.width / 2, building.z + building.depth / 2)
    with pytest.raises(ValueError):
        prepare_edit(after, "ここに30×40のマンションを建てて", Selection(point=point), mode="rules")
    with pytest.raises(ValueError):
        apply_edit(project, plan, None, tmp_path / "not-approved")
    assert not (tmp_path / "not-approved").exists()
    write_preview(project, plan, tmp_path / "review")
    assert (tmp_path / "review/architecture.png").exists()
    saved = apply_edit(project, plan, plan.plan_id, tmp_path / "applied")
    assert len(saved.city.buildings) == 52 and (tmp_path / "applied/city.schem").exists()
    restored = undo_edit(
        saved, tmp_path / "applied/history.json", plan.plan_id, tmp_path / "undone"
    )
    assert digest(restored) == digest(project)
    assert np.array_equal(build_city(restored.city).blocks, build_city(project.city).blocks)


def test_frozen_design_never_calls_external_service(monkeypatch):
    project = load_project(SOURCE)
    plan, _ = prepare_edit(project, "青葉市に18×22の駅舎を建てて", mode="rules")

    def fail(*args, **kwargs):
        raise AssertionError("approval must not design again")

    monkeypatch.setattr("minecraft_city.architecture_edit.design", fail)
    assert len(verify_plan(project, plan).city.buildings) == 52


def test_exact_height_and_optional_stairs():
    req = ArchitectureRequest(category="office", width=40, depth=50, height=60)
    spec = local_design(req, 42)
    volume = render_architecture(spec)
    assert np.any(volume.blocks[60]) and volume.blocks.shape[0] == 61
    spec = spec.model_copy(update={"stairs": True})
    stairs = render_architecture(spec)
    assert any("oak_stairs" in key for key in stairs.palette)
    assert not np.array_equal(stairs.blocks, volume.blocks)


def test_disconnected_ai_masses_fail():
    data = local_design(ArchitectureRequest(category="office", width=40, depth=50), 42).model_dump()
    data["masses"] = [
        {"x": 2, "z": 4, "width": 5, "depth": 5, "floors": data["floors"]},
        {"x": 20, "z": 20, "width": 5, "depth": 5, "floors": 1},
    ]
    data["entrance_x"] = 4
    with pytest.raises(ValueError, match="接続"):
        ArchitectureSpec.model_validate(data)


def test_restyle_new_architecture_preserves_shape():
    project = load_project(SOURCE)
    _plan, after = prepare_edit(project, "青葉市に18×22の駅舎を建てて", mode="rules")
    b = after.city.buildings[-1]
    restyle, restyled = prepare_edit(after, f"建物{b.id}の外観を和風にして", mode="rules")
    changed = restyled.city.buildings[-1]
    assert changed.architecture.wall == "white_terracotta"
    assert changed.architecture.masses == b.architecture.masses
    assert changed.footprint == b.footprint and changed.id == b.id
    assert len(verify_plan(after, restyle).city.buildings) == 52
