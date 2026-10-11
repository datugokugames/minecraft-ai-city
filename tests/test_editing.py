import json
from pathlib import Path

import nbtlib
import numpy as np
import pytest
from PIL import Image
from shapely.geometry import LineString, Polygon, box

from minecraft_city.building import build_city
from minecraft_city.cli import main
from minecraft_city.edit_geometry import validate_placement
from minecraft_city.edit_models import EditError, EditPlan, Selection, digest
from minecraft_city.editing import (
    apply_edit,
    load_plan,
    prepare_edit,
    undo_edit,
    verify_plan,
    write_preview,
)
from minecraft_city.generation import building_area, generate_city
from minecraft_city.planning import rule_plan
from minecraft_city.project import create_project, load_project, save_project


@pytest.fixture(scope="module")
def project(blended):
    metrics, sources = blended
    return create_project(
        generate_city(metrics, sources, rule_plan(metrics, 42), 42, layout="organic"), "青葉市"
    )


@pytest.fixture(scope="module")
def proposal(project):
    return prepare_edit(project, "中央区の空き地にコンビニを作って", mode="rules")


def test_preview_only_and_reproducibility(project, proposal, tmp_path):
    plan, after = proposal
    original = digest(project)
    assert prepare_edit(project, plan.instruction, mode="rules")[0] == plan
    write_preview(project, plan, tmp_path / "review")
    assert digest(project) == original
    assert not (tmp_path / "review/project.json").exists()
    assert not (tmp_path / "review/city.schem").exists()
    assert load_plan(tmp_path / "review/plan.json") == plan
    for name in ("before.png", "after.png", "comparison.png"):
        with Image.open(tmp_path / "review" / name) as image:
            image.verify()
    assert (tmp_path / "review/before.png").read_bytes() != (
        tmp_path / "review/after.png"
    ).read_bytes()
    assert len(after.city.buildings) == len(project.city.buildings) + 1
    assert after.city.buildings[:-1] == project.city.buildings
    assert after.city.sites == project.city.sites
    assert after.city.roads == project.city.roads
    assert after.city.railway_z == project.city.railway_z
    assert after.city.landscape == project.city.landscape
    assert after.wards == project.wards and after.districts == project.districts
    b = after.city.buildings[-1]
    assert b.id == max(b.id for b in project.city.buildings) + 1
    assert b.kind == "shop" and b.detail == "convenience_store"
    assert plan.validation["frontage_validated"] == len(after.city.buildings)
    assert plan.validation["building_overlaps"] == 0
    with pytest.raises(FileExistsError):
        write_preview(project, plan, tmp_path / "review")


@pytest.mark.parametrize("approval", [None, "", "yes", "0" * 64])
def test_unapproved_apply_never_writes(project, proposal, tmp_path, approval):
    with pytest.raises(EditError, match="明示承認"):
        apply_edit(project, proposal[0], approval, tmp_path / "blocked")
    assert not (tmp_path / "blocked").exists()


def test_approved_export_and_exact_undo(project, proposal, tmp_path):
    plan, after = proposal
    result = apply_edit(project, plan, plan.plan_id, tmp_path / "applied")
    assert digest(result) == digest(after)
    assert digest(load_project(tmp_path / "applied/before.project.json")) == digest(project)
    assert digest(load_project(tmp_path / "applied/project.json")) == digest(after)
    assert (
        json.loads((tmp_path / "applied/history.json").read_text())["approved_plan_id"]
        == plan.plan_id
    )
    volume = build_city(after.city)
    schematic = nbtlib.load(tmp_path / "applied/city.schem")
    assert int(schematic["Width"]) == 256 and int(schematic["Length"]) == 256
    assert np.array_equal(
        np.asarray(schematic["BlockData"], dtype=np.uint8).reshape(volume.blocks.shape),
        volume.blocks,
    )
    for state in ("minecraft:lime_concrete", "minecraft:blue_concrete"):
        assert np.count_nonzero(volume.blocks == volume.palette[state]) > 0
    restored = undo_edit(after, tmp_path / "applied/history.json", plan.plan_id, tmp_path / "undo")
    assert digest(restored) == digest(project)
    assert np.array_equal(build_city(restored.city).blocks, build_city(project.city).blocks)
    assert json.loads((tmp_path / "undo/history.json").read_text())["action"] == "undo"
    assert (
        json.loads((tmp_path / "undo/diff.json").read_text())["removed_buildings"][0]["id"]
        == plan.changes.added_buildings[0].id
    )
    with pytest.raises(FileExistsError):
        apply_edit(project, plan, plan.plan_id, tmp_path / "applied")


def test_stale_project_and_tampered_plan_are_rejected(project, proposal, tmp_path):
    plan = proposal[0]
    renamed = project.model_copy(update={"city_name": "別の市"}, deep=True)
    with pytest.raises(EditError, match="元プロジェクト"):
        apply_edit(renamed, plan, plan.plan_id, tmp_path / "stale")
    data = plan.model_dump(mode="json")
    data["changes"]["added_buildings"][0]["floors"] = 8
    with pytest.raises(ValueError, match="digest"):
        EditPlan.model_validate(data)
    data["plan_id"] = digest({k: v for k, v in data.items() if k != "plan_id"})
    tampered = EditPlan.model_validate(data)
    with pytest.raises(EditError, match="再検証"):
        apply_edit(project, tampered, tampered.plan_id, tmp_path / "tampered")
    assert not (tmp_path / "stale").exists() and not (tmp_path / "tampered").exists()


def test_multiple_edits_undo_in_order(project, proposal, tmp_path):
    first, state1 = proposal
    apply_edit(project, first, first.plan_id, tmp_path / "one")
    second, state2 = prepare_edit(state1, "青葉市に住宅を追加して", seed=7, mode="rules")
    apply_edit(state1, second, second.plan_id, tmp_path / "two")
    with pytest.raises(EditError, match="最新"):
        undo_edit(state2, tmp_path / "one/history.json", first.plan_id, tmp_path / "wrong")
    restored1 = undo_edit(
        state2, tmp_path / "two/history.json", second.plan_id, tmp_path / "back-one"
    )
    restored0 = undo_edit(
        restored1, tmp_path / "one/history.json", first.plan_id, tmp_path / "back-zero"
    )
    assert digest(restored0) == digest(project)


def test_modified_or_incomplete_history_cannot_restore(project, proposal, tmp_path):
    plan, after = proposal
    apply_edit(project, plan, plan.plan_id, tmp_path / "applied")
    snapshot = tmp_path / "applied/before.project.json"
    data = json.loads(snapshot.read_text())
    data["city_name"] = "改変"
    snapshot.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(EditError, match="スナップショット"):
        undo_edit(after, tmp_path / "applied/history.json", plan.plan_id, tmp_path / "undo")
    (tmp_path / "applied/complete.json").unlink()
    with pytest.raises(OSError):
        undo_edit(after, tmp_path / "applied/history.json", plan.plan_id, tmp_path / "undo")
    assert not (tmp_path / "undo").exists()


@pytest.mark.parametrize("seed", [7, 42, 2026])
def test_five_houses_and_station_east(project, seed):
    plan, after = prepare_edit(project, "青葉市に住宅を5棟追加して", seed=seed, mode="rules")
    assert len(plan.changes.added_buildings) == 5
    assert len({b.id for b in after.city.buildings}) == len(after.city.buildings)
    assert after.city.buildings[:-5] == project.city.buildings
    plan, _ = prepare_edit(project, "駅の東側にマンションを建てて", seed=seed, mode="rules")
    east = max(building_area(b).bounds[2] for b in project.city.buildings if b.kind == "station")
    assert building_area(plan.changes.added_buildings[0]).bounds[0] > east


def test_named_district_and_buildable_bounds(project, proposal):
    added = proposal[0].changes.added_buildings[0]
    district = next(d for d in project.districts if d.city_district_id == added.district_id)
    plan, _ = prepare_edit(
        project, "この地区に住宅を追加して", Selection(district=district.id), mode="rules"
    )
    assert plan.changes.added_buildings[0].district_id == added.district_id
    bounds = building_area(added).buffer(4, join_style="mitre").bounds
    plan, _ = prepare_edit(
        project, "この範囲にコンビニを作って", Selection(bounds=bounds), mode="rules"
    )
    assert box(*bounds).covers(
        building_area(plan.changes.added_buildings[0]).buffer(1.5, join_style="mitre")
    )


@pytest.mark.parametrize(
    "selection",
    [
        Selection(point=(-10, 40)),
        Selection(bounds=(0, 0, 999, 999)),
        Selection(ward="不明区"),
        Selection(district="missing"),
        Selection(city_name="他市"),
    ],
)
def test_unknown_or_outside_selection_fails(project, selection):
    with pytest.raises(ValueError):
        prepare_edit(project, "コンビニを作って", selection, mode="rules")


def test_collisions_and_insufficient_space_never_partially_apply(project):
    before = digest(project)
    b = project.city.buildings[0]
    center = building_area(b).centroid
    with pytest.raises(EditError, match="衝突"):
        prepare_edit(
            project, "ここにコンビニを作って", Selection(point=(center.x, center.y)), mode="rules"
        )
    with pytest.raises(EditError):
        prepare_edit(
            project, "この範囲に住宅を5棟追加して", Selection(bounds=(0, 0, 10, 10)), mode="rules"
        )
    with pytest.raises(EditError, match="衝突"):
        validate_placement(
            project.city, building_area(b), LineString(b.access_path), box(0, 0, 256, 256)
        )
    assert digest(project) == before


def test_restyle_keeps_identity_geometry_and_neighbors(project, tmp_path):
    target = next(b for b in project.city.buildings if b.kind == "house" and b.palette != "modern")
    plan, after = prepare_edit(
        project,
        "指定した建物の外観をもっと現代的にして",
        Selection(building_id=target.id),
        mode="rules",
    )
    changed = next(b for b in after.city.buildings if b.id == target.id)
    assert changed.palette == "modern"
    for b in project.city.buildings:
        actual = next(a for a in after.city.buildings if a.id == b.id)
        if b.id != target.id:
            assert b == actual
        else:
            assert b.model_dump(exclude={"palette", "variant", "detail"}) == actual.model_dump(
                exclude={"palette", "variant", "detail"}
            )
    assert after.parcels == project.parcels
    apply_edit(project, plan, plan.plan_id, tmp_path / "modern")
    assert (tmp_path / "modern/city.schem").exists()
    with pytest.raises(EditError, match="変更はありません"):
        prepare_edit(
            after,
            "指定した建物の外観をもっと現代的にして",
            Selection(building_id=target.id),
            mode="rules",
        )


def test_park_is_real_grass_paths_and_safe_frontage(project, tmp_path):
    plan, after = prepare_edit(project, "青葉市に公園を作って", mode="rules")
    assert after.city.buildings == project.city.buildings
    park = after.city.sites[-1]
    assert park.kind == "park" and park.access_path
    volume = build_city(after.city)
    from minecraft_city.preview import mask

    pixels = mask(Polygon(park.polygon), after.city.size)
    states = set(volume.blocks[3, pixels].tolist())
    assert volume.palette["minecraft:grass_block[snowy=false]"] in states
    assert volume.palette["minecraft:gravel"] in states
    # A range-bound park must remain inside the explicitly requested rectangle.
    bounds = Polygon(park.polygon).buffer(2, join_style="mitre").bounds
    ranged, _ = prepare_edit(
        project, "この範囲に公園を作って", Selection(bounds=bounds), mode="rules"
    )
    assert box(*bounds).covers(Polygon(ranged.changes.added_sites[0].polygon))
    apply_edit(project, plan, plan.plan_id, tmp_path / "park")


def test_legacy_project_remains_usable(city):
    project = create_project(city, "旧市")
    b = next(b for b in city.buildings if b.kind == "house")
    palette = "和風" if b.palette != "traditional" else "現代的"
    plan, after = prepare_edit(project, f"建物{b.id}の外観を{palette}にして", mode="rules")
    assert verify_plan(project, plan) == after
    with pytest.raises(EditError, match="organic"):
        prepare_edit(project, "旧市に住宅を追加して", mode="rules")


def test_cli_complete_flow_and_error_report(project, tmp_path, capsys):
    original = tmp_path / "original.json"
    save_project(original, project)
    data = original.read_bytes()
    args = [
        "edit",
        "plan",
        "--project",
        str(original),
        "--instruction",
        "中央区の空き地にコンビニを作って",
        "--parser",
        "rules",
        "--output",
        str(tmp_path / "review"),
    ]
    main(args)
    plan = load_plan(tmp_path / "review/plan.json")
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "edit",
                "apply",
                "--project",
                str(original),
                "--plan",
                str(tmp_path / "review/plan.json"),
                "--output",
                str(tmp_path / "denied"),
            ]
        )
    assert exc.value.code == 2 and not (tmp_path / "denied").exists()
    main(
        [
            "edit",
            "apply",
            "--project",
            str(original),
            "--plan",
            str(tmp_path / "review/plan.json"),
            "--approve",
            plan.plan_id,
            "--output",
            str(tmp_path / "applied"),
        ]
    )
    main(
        [
            "edit",
            "undo",
            "--project",
            str(tmp_path / "applied/project.json"),
            "--history",
            str(tmp_path / "applied/history.json"),
            "--approve",
            plan.plan_id,
            "--output",
            str(tmp_path / "undo"),
        ]
    )
    assert original.read_bytes() == data
    assert digest(load_project(tmp_path / "undo/project.json")) == digest(project)
    with pytest.raises(SystemExit):
        main(
            [
                "edit",
                "plan",
                "--project",
                str(original),
                "--instruction",
                "ここに公園を作って",
                "--parser",
                "rules",
                "--output",
                str(tmp_path / "clarify"),
            ]
        )
    report = json.loads((tmp_path / "clarify/validation.json").read_text())
    assert report["status"] == "clarification_required" and not report["valid"]
    assert not (tmp_path / "clarify/plan.json").exists()


def test_v021_sample_loads_without_digest_migration():
    path = Path(__file__).parents[1] / "examples/managed-city/project.json"
    p = load_project(path)
    assert all(b.detail is None for b in p.city.buildings)
    assert all("detail" not in b for b in p.city.model_dump(mode="json")["buildings"])
