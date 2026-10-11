"""Deterministic planning, explicit approval, immutable snapshots and guarded undo."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

from .edit_geometry import compute_delta
from .edit_language import interpret
from .edit_models import EditError, EditPlan, EditRecord, digest
from .exporting import export_city
from .generation import building_area, validate_city
from .models import save_json
from .preview import _font, render_preview
from .project import CityProject, Parcel, city_digest, load_project, save_project
from .quality import evaluate_city


def _parcel_id(project, prefix):
    existing = {p.id for p in project.parcels}
    candidate, suffix = prefix, 1
    while candidate in existing:
        candidate = f"{prefix}-{suffix}"
        suffix += 1
    return candidate


def materialize(project, changes):
    """Apply a validated delta in memory, preserving all unrelated data and IDs."""
    result = project.model_copy(deep=True)
    city = result.city
    managed = {d.city_district_id: d.id for d in result.districts if d.city_district_id is not None}
    for change in changes.changed_buildings:
        index = next((i for i, b in enumerate(city.buildings) if b.id == change.before.id), None)
        if index is None or city.buildings[index] != change.before:
            raise EditError("変更前の建物が一致しません。")
        city.buildings[index] = change.after.model_copy(deep=True)
    for b in changes.added_buildings:
        city.buildings.append(b.model_copy(deep=True))
        result.parcels.append(
            Parcel(
                id=_parcel_id(result, f"edit-building-{b.id}"),
                district_id=managed[b.district_id],
                building_id=b.id,
                polygon=list(building_area(b).exterior.coords),
            )
        )
    if len(changes.added_sites) != len(changes.site_district_ids):
        raise EditError("公園と地区の対応が不正です。")
    for site, district_id in zip(changes.added_sites, changes.site_district_ids):
        index = len(city.sites)
        city.sites.append(site.model_copy(deep=True))
        result.parcels.append(
            Parcel(
                id=_parcel_id(result, f"edit-site-{index}"),
                district_id=managed[district_id],
                site_index=index,
                polygon=site.polygon,
            )
        )
    city.validation = validate_city(city)
    if city.layout == "organic":
        city.quality = evaluate_city(city)
    result.city_sha256 = city_digest(city)
    return CityProject.model_validate(result.model_dump())


def _make_plan(project, instruction, source, intent, seed):
    if not isinstance(seed, int) or not 0 <= seed <= 2**63 - 1:
        raise EditError("seed must be 0..2^63-1")
    # Validate even when callers pass mutable Python model instances.
    project = CityProject.model_validate(project.model_dump())
    changes = compute_delta(project, intent, seed)
    after = materialize(project, changes)
    validation = {
        **after.city.validation,
        "roads_unchanged": after.city.roads == project.city.roads,
        "landscape_unchanged": after.city.landscape == project.city.landscape,
        "existing_buildings_preserved": len(project.city.buildings),
        "added_building_ids": [b.id for b in changes.added_buildings],
        "changed_building_ids": [c.before.id for c in changes.changed_buildings],
        "added_site_indices": list(range(len(project.city.sites), len(after.city.sites))),
        "errors": [],
    }
    assumptions = [
        "座標は都市ローカルX/Z。Xの増加は東、Zの増加は南。",
        "空き地は既存の建物・登録敷地・交通施設・通路に占有されていない土地。既存登録敷地は置換しない。",
        "建物本体と屋根余白を範囲内に配置。新しい接道通路は既存道路へ接続するため範囲外に出る場合がある。",
    ]
    if intent.action == "add_buildings":
        assumptions.append(
            "コンビニは1階、住宅は2階、マンションは都市計画上限内の最大4階。座標指定の中心はブロックへ最大0.5丸める。"
        )
    if intent.action == "add_park":
        assumptions.append(
            "矩形指定の公園は周囲2ブロックを確保して内側を使用。地区指定は10×10の公園を1つ配置。"
        )
    payload = {
        "schema_version": 1,
        "engine_version": "phase3a-1",
        "base_project_sha256": digest(project),
        "instruction": instruction,
        "parser_source": source,
        "seed": seed,
        "intent": intent.model_dump(mode="json"),
        "changes": changes.model_dump(mode="json"),
        "after_project_sha256": digest(after),
        "validation": validation,
        "assumptions": assumptions,
    }
    return EditPlan(plan_id=digest(payload), **payload), after


def prepare_edit(project, instruction, selection=None, seed=42, mode="auto", *, session=None):
    intent, source = interpret(instruction, project, selection, mode, session=session)
    return _make_plan(project, instruction, source, intent, seed)


def verify_plan(project, plan):
    plan = EditPlan.model_validate(plan.model_dump())
    if digest(project) != plan.base_project_sha256:
        raise EditError(
            "元プロジェクトが変更されています。新しい計画・プレビューで承認し直してください。"
        )
    expected, after = _make_plan(
        project, plan.instruction, plan.parser_source, plan.intent, plan.seed
    )
    if expected != plan:
        raise EditError("計画の再検証が一致しません。変更された計画を再利用できません。")
    return after


def _comparison(before, after, changes, output):
    with Image.open(before) as left, Image.open(after) as right:
        left, right = left.convert("RGB"), right.convert("RGB")
    for img, is_after in ((left, False), (right, True)):
        draw = ImageDraw.Draw(img)
        for b in [
            *changes.added_buildings,
            *[c.after if is_after else c.before for c in changes.changed_buildings],
        ]:
            x0, z0, x1, z1 = building_area(b).bounds
            draw.rectangle(
                (32 + x0 * 4 - 4, 90 + z0 * 4 - 4, 32 + x1 * 4 + 4, 90 + z1 * 4 + 4),
                outline="#d62762",
                width=4,
            )
            draw.text((32 + x0 * 4, 90 + z0 * 4 - 21), f"ID {b.id}", font=_font(14), fill="#a11944")
        for site in changes.added_sites:
            draw.line([(32 + x * 4, 90 + z * 4) for x, z in site.polygon], fill="#d62762", width=4)
    canvas = Image.new(
        "RGB", (left.width + right.width, max(left.height, right.height) + 36), "#f4f1e8"
    )
    draw = ImageDraw.Draw(canvas)
    draw.text((32, 8), "BEFORE / REVIEW TARGETS", font=_font(18), fill="#263e41")
    draw.text((left.width + 32, 8), "AFTER / PROPOSED CHANGES", font=_font(18), fill="#263e41")
    canvas.paste(left, (0, 36))
    canvas.paste(right, (left.width, 36))
    canvas.save(output)


def write_preview(project, plan, output):
    after = verify_plan(project, plan)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    save_json(output / "plan.json", plan)
    save_json(output / "diff.json", plan.changes)
    save_json(output / "validation.json", {"status": "awaiting_approval", **plan.validation})
    render_preview(project.city, output / "before.png")
    render_preview(after.city, output / "after.png")
    _comparison(
        output / "before.png", output / "after.png", plan.changes, output / "comparison.png"
    )
    # This is a review snapshot only; there is no new project.json or schematic here.
    save_project(output / "before.project.json", project)
    save_json(output / "complete.json", {"plan_id": plan.plan_id, "status": "awaiting_approval"})


def load_plan(path):
    return EditPlan.model_validate_json(Path(path).read_text(encoding="utf-8"))


def _approved(plan_id, approval):
    if not approval or approval != plan_id:
        raise EditError(
            "明示承認が必要です。プレビュー確認後、--approve に完全なplan_idを指定してください。"
        )


def _write_result(before, after, plan, output, action):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    # Completion marker is written last. Failed/interrupted output never overwrites a snapshot.
    save_project(output / "before.project.json", before)
    save_json(output / "edit-plan.json", plan)
    export_city(after.city.model_copy(deep=True), output)
    save_project(output / "project.json", after)
    render_preview(before.city, output / "before.png")
    save_json(
        output / "diff.json",
        plan.changes
        if action == "apply"
        else {
            "removed_buildings": [b.model_dump(mode="json") for b in plan.changes.added_buildings],
            "changed_buildings": [
                {
                    "before": c.after.model_dump(mode="json"),
                    "after": c.before.model_dump(mode="json"),
                }
                for c in plan.changes.changed_buildings
            ],
            "removed_sites": [s.model_dump(mode="json") for s in plan.changes.added_sites],
        },
    )
    record = EditRecord(
        action=action,
        plan_id=plan.plan_id,
        approved_plan_id=plan.plan_id,
        before_project_sha256=digest(before),
        after_project_sha256=digest(after),
    )
    save_json(output / "history.json", record)
    save_json(
        output / "complete.json",
        {"history_sha256": digest(record), "project_sha256": digest(after)},
    )
    return after


def apply_edit(project, plan, approval, output):
    _approved(plan.plan_id, approval)
    after = verify_plan(project, plan)
    return _write_result(project, after, plan, output, "apply")


def undo_edit(project, history_path, approval, output):
    """Restore only an intact apply transaction whose result is the current project."""
    history_path = Path(history_path)
    record = EditRecord.model_validate_json(history_path.read_text(encoding="utf-8"))
    _approved(record.plan_id, approval)
    if record.action != "apply" or record.approved_plan_id != record.plan_id:
        raise EditError("Undoの対象は承認済みapply履歴のみです。")
    marker = json.loads((history_path.parent / "complete.json").read_text(encoding="utf-8"))
    if (
        marker.get("history_sha256") != digest(record)
        or marker.get("project_sha256") != record.after_project_sha256
    ):
        raise EditError("未完了または変更された編集履歴です。")
    if digest(project) != record.after_project_sha256:
        raise EditError(
            "現在のプロジェクトはこの履歴の編集結果と一致しません。最新の変更からUndoしてください。"
        )
    before = load_project(history_path.parent / "before.project.json")
    plan = load_plan(history_path.parent / "edit-plan.json")
    if plan.plan_id != record.plan_id or digest(before) != record.before_project_sha256:
        raise EditError("履歴と変更前スナップショットが一致しません。")
    expected = verify_plan(before, plan)
    if digest(expected) != digest(project):
        raise EditError("履歴の再検証が一致しません。")
    return _write_result(project, before, plan, output, "undo")
