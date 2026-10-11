import copy
import json

import pytest
from pydantic import ValidationError

from minecraft_city.cli import main
from minecraft_city.generation import generate_city
from minecraft_city.models import save_json
from minecraft_city.planning import rule_plan
from minecraft_city.project import (
    CityProject,
    ProjectSettings,
    city_digest,
    configure_project,
    create_project,
    load_project,
    project_summary,
    save_project,
)


@pytest.fixture(scope="module", params=["legacy", "organic"])
def managed(request, blended):
    metrics, sources = blended
    city = generate_city(metrics, sources, rule_plan(metrics, 42), 42, layout=request.param)
    return create_project(city, "青葉市")


def test_roundtrip_complete_hierarchy_and_geometry(managed, tmp_path):
    before = managed.city.model_dump_json()
    path = tmp_path / "project.json"
    save_project(path, managed)
    restored = load_project(path)
    # JSON object keys are strings (quality.degree_counts uses integer keys in memory).
    assert restored.model_dump(mode="json") == managed.model_dump(mode="json")
    assert restored.city.model_dump_json() == before
    assert len(restored.parcels) == len(restored.city.buildings) + len(restored.city.sites)
    assert {p.building_id for p in restored.parcels if p.building_id is not None} == {
        b.id for b in restored.city.buildings
    }
    assert project_summary(restored)["city_name"] == "青葉市"
    # A saved snapshot is never silently replaced, even by the same project.
    saved = path.read_bytes()
    with pytest.raises(FileExistsError):
        save_project(path, restored)
    assert path.read_bytes() == saved


def test_explicit_names_membership_preferences_and_unbuilt_reservations(managed):
    original = managed.model_dump_json()
    first = managed.districts[0]
    result = configure_project(
        managed,
        ProjectSettings.model_validate(
            {
                "city_name": " 若葉市 ",
                "wards": [{"id": "north", "name": "北区"}],
                "districts": [
                    {
                        "id": first.id,
                        "name": "駅前商業地区",
                        "ward_id": "north",
                        "preferred_style": "modern",
                    },
                    {
                        "id": "future",
                        "name": "北の新地区",
                        "ward_id": "north",
                        "preferred_use": "residential",
                    },
                ],
            }
        ),
    )
    assert managed.model_dump_json() == original  # Input must not be mutated.
    assert result.city == managed.city
    assert result.city_sha256 == managed.city_sha256
    assert result.parcels == managed.parcels
    assert result.city_name == "若葉市"
    assert result.districts[0].preferred_use == first.preferred_use
    assert result.districts[0].preferred_style == "modern"
    assert result.districts[-1].city_district_id is None
    assert result.districts[1:-1] == managed.districts[1:]
    north = project_summary(result)["wards"][1]
    assert north["districts"][-1]["developed"] is False
    assert north["districts"][-1]["parcel_count"] == 0
    assert sorted(north["districts"][0]["building_ids"]) == sorted(
        b.id for b in managed.city.buildings if b.district_id == first.city_district_id
    )
    cleared = configure_project(
        result,
        ProjectSettings.model_validate(
            {
                "districts": [
                    {"id": first.id, "name": "駅前", "ward_id": "north", "preferred_style": None}
                ],
            }
        ),
    )
    assert cleared.districts[0].preferred_style is None
    assert cleared.districts[0].preferred_use == first.preferred_use


@pytest.mark.parametrize(
    "defect",
    [
        "ward_reference",
        "duplicate_ward",
        "duplicate_district",
        "duplicate_parcel",
        "missing_parcel",
        "duplicate_building",
        "missing_city_district",
        "wrong_parcel_district",
        "bad_boundary",
        "empty_boundary",
        "missing_building",
        "digest",
        "orphan_building",
    ],
)
def test_invalid_snapshots_are_rejected(managed, defect):
    data = copy.deepcopy(managed.model_dump(mode="json"))
    if defect == "ward_reference":
        data["districts"][0]["ward_id"] = "missing"
    elif defect == "duplicate_ward":
        data["wards"].append(data["wards"][0])
    elif defect == "duplicate_district":
        data["districts"].append(data["districts"][0])
    elif defect == "duplicate_parcel":
        data["parcels"].append(data["parcels"][0])
    elif defect == "missing_parcel":
        data["parcels"].pop()
    elif defect == "duplicate_building":
        data["parcels"][1]["building_id"] = data["parcels"][0]["building_id"]
    elif defect == "missing_city_district":
        data["districts"].pop()
    elif defect == "wrong_parcel_district":
        data["parcels"][0]["district_id"] = "missing"
    elif defect == "bad_boundary":
        data["parcels"][0]["polygon"] = [[0, 0], [1, 0], [1, 1], [0, 0]]
    elif defect == "empty_boundary":
        data["parcels"][0]["polygon"] = [[0, 0]] * 4
    elif defect == "missing_building":
        data["parcels"][0]["building_id"] = 999999
    elif defect == "digest":
        data["city"]["buildings"][0]["floors"] = 8
    elif defect == "orphan_building":
        altered = managed.city.model_copy(deep=True)
        altered.buildings[0].district_id = 99999
        data["city"] = altered.model_dump(mode="json")
        data["city_sha256"] = city_digest(altered)
    with pytest.raises(ValidationError):
        CityProject.model_validate(data)


@pytest.mark.parametrize(
    "settings",
    [
        {"city_name": "  "},
        {"wards": [{"id": "bad/id", "name": "北区"}]},
        {"wards": [{"id": "north", "name": "中央区"}]},
        {"wards": [{"id": "north", "name": "北区"}] * 2},
        {"districts": [{"id": "new", "name": "新地区", "ward_id": "absent"}]},
        {
            "districts": [
                {"id": "new", "name": "新地区", "ward_id": "ward-1", "city_district_id": 0}
            ]
        },
        {"delete_buildings": [0]},
        {"roads": []},
    ],
)
def test_invalid_settings_do_not_change_input(managed, settings):
    before = managed.model_dump_json()
    with pytest.raises(ValueError):
        configure_project(managed, ProjectSettings.model_validate(settings))
    assert managed.model_dump_json() == before


def test_cli_project_export_uses_existing_engine(managed, tmp_path, capsys):
    source = tmp_path / "city.json"
    initial = tmp_path / "initial.json"
    save_json(source, managed.city)
    original = source.read_bytes()
    main(["project", "init", "--city", str(source), "--name", "青葉市", "--output", str(initial)])
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"wards": [{"id": "north", "name": "北区"}]}), encoding="utf-8")
    configured = tmp_path / "configured.json"
    main(
        [
            "project",
            "configure",
            "--project",
            str(initial),
            "--settings",
            str(settings),
            "--output",
            str(configured),
        ]
    )
    capsys.readouterr()
    main(["project", "inspect", "--project", str(configured)])
    summary = json.loads(capsys.readouterr().out)
    assert summary["wards"][1]["name"] == "北区"
    assert summary["wards"][1]["districts"] == []
    output = tmp_path / "export"
    args = ["project", "export", "--project", str(configured), "--output", str(output)]
    main(args)
    main(["build", "--city", str(source), "--output", str(tmp_path / "plain")])
    assert (output / "city.schem").read_bytes() == (tmp_path / "plain/city.schem").read_bytes()
    assert (output / "preview.png").read_bytes() == (tmp_path / "plain/preview.png").read_bytes()
    assert load_project(output / "project.json") == load_project(configured)
    assert source.read_bytes() == original
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2
    assert "Error:" in capsys.readouterr().err


def test_cli_invalid_settings_leave_no_output(managed, tmp_path):
    initial = tmp_path / "project.json"
    save_project(initial, managed)
    settings = tmp_path / "invalid.json"
    settings.write_text('{"delete_buildings": [0]}', encoding="utf-8")
    output = tmp_path / "unchanged.json"
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "project",
                "configure",
                "--project",
                str(initial),
                "--settings",
                str(settings),
                "--output",
                str(output),
            ]
        )
    assert exc.value.code == 2
    assert not output.exists()
    assert load_project(initial).model_dump(mode="json") == managed.model_dump(mode="json")
