import json

import pytest
import requests
from shapely.geometry import LineString, box

from minecraft_city.analysis import (
    PRESETS,
    UrbanData,
    analyze_osm,
    extract_metrics,
    fetch_osm,
    height_from_tags,
    parse_osm,
    validate_bbox,
)
from minecraft_city.models import load_profile, save_json
from minecraft_city.planning import blend_profiles


def test_measured_geometry():
    data = UrbanData(
        box(0, 0, 100, 100),
        [LineString([(0, 50), (100, 50)]), LineString([(50, 0), (50, 100)])],
        [(box(10, 10, 20, 20), 6, True), (box(70, 70, 90, 90), 9, False)],
    )
    m = extract_metrics(data)
    assert m.road_density_km_km2 == pytest.approx(20)
    assert m.intersection_count == 1
    assert m.building_density_km2 == 200
    assert m.building_coverage == 0.05
    assert m.mean_height_m == 7.5
    assert m.height_tag_coverage == 0.5
    assert m.orthogonal_road_share == 1


def test_blocks_and_duplicate_roads():
    outline = LineString([(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)])
    m = extract_metrics(UrbanData(box(0, 0, 100, 100), [outline, outline]))
    assert m.road_density_km_km2 == 40
    assert m.median_block_area_m2 == 10000
    assert m.mean_block_compactness == pytest.approx(0.785398163)


@pytest.mark.parametrize(
    "tags,expected",
    [
        ({}, (9, False)),
        ({"height": "12 m"}, (12, True)),
        ({"building:levels": "4"}, (12, True)),
        ({"height": "30 ft"}, (9.144, True)),
        ({"height": "-9", "building:levels": "3"}, (9, True)),
    ],
)
def test_height_tags(tags, expected):
    actual = height_from_tags(tags)
    assert actual[0] == pytest.approx(expected[0])
    assert actual[1] == expected[1]


def test_profiles_and_blending(profiles, tmp_path):
    p = tmp_path / "profile.json"
    save_json(p, profiles[0])
    assert load_profile(p) == profiles[0]
    m, sources = blend_profiles(profiles, [3, 1])
    assert sources[0]["weight"] == 0.75
    assert m.mean_height_m == pytest.approx(
        0.75 * profiles[0].metrics.mean_height_m + 0.25 * profiles[1].metrics.mean_height_m
    )
    assert all(p.source == "synthetic_sample" for p in profiles)


@pytest.mark.parametrize("weights", [[0, 0], [-1, 2], [1], [float("nan"), 1], [float("inf"), 2]])
def test_invalid_weights(profiles, weights):
    with pytest.raises(ValueError):
        blend_profiles(profiles, weights)


@pytest.mark.parametrize(
    "bbox", [(0, 0, 1, 1), (36, 140, 35, 139), (0, 0, 0.00001, 0.00001), (float("nan"), 1, 2, 3)]
)
def test_bbox_limits(bbox):
    with pytest.raises(ValueError):
        validate_bbox(bbox)


def test_labeled_fallback(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise requests.Timeout("not logged")

    monkeypatch.setattr("minecraft_city.analysis.fetch_osm", fail)
    with pytest.warns(UserWarning, match="synthetic"):
        p = analyze_osm("test", PRESETS["tokyo"], tmp_path)
    assert p.source == "synthetic_sample"
    with pytest.raises(requests.Timeout):
        analyze_osm("test", PRESETS["tokyo"], tmp_path, fallback=None)


def test_overpass_cache_and_incomplete_response(tmp_path):
    class Client:
        calls = 0

        def post(self, url, **kwargs):
            self.calls += 1
            assert kwargs["timeout"] == (10, 65)
            assert "User-Agent" in kwargs["headers"]
            return self

        def raise_for_status(self):
            pass

        def json(self):
            return {"elements": [{"type": "way", "id": 1}]}

    client = Client()
    first = fetch_osm(PRESETS["tokyo"], tmp_path, session=client)
    second = fetch_osm(PRESETS["tokyo"], tmp_path, session=client)
    assert first == second
    assert client.calls == 1
    client.json = lambda: {"elements": [{"id": 1}], "remark": "runtime timeout"}
    with pytest.raises(ValueError, match="incomplete"):
        fetch_osm(PRESETS["kyoto"], tmp_path, session=client)


def test_projected_osm_and_multipolygon():
    bounds = (35.0, 135.0, 35.006, 135.006)

    def geom(coords):
        return [{"lon": x, "lat": y} for x, y in coords]

    outer = [
        (135.001, 35.001),
        (135.003, 35.001),
        (135.003, 35.003),
        (135.001, 35.003),
        (135.001, 35.001),
    ]
    inner = [
        (135.0015, 35.0015),
        (135.0025, 35.0015),
        (135.0025, 35.0025),
        (135.0015, 35.0025),
        (135.0015, 35.0015),
    ]
    payload = {
        "elements": [
            {
                "type": "way",
                "id": 1,
                "tags": {"highway": "residential"},
                "geometry": geom([(135, 35.004), (135.006, 35.004)]),
            },
            {"type": "way", "id": 2, "tags": {"building": "yes"}, "geometry": geom(outer)},
            {
                "type": "relation",
                "id": 3,
                "tags": {"building": "yes", "building:levels": "4"},
                "members": [
                    {"type": "way", "ref": 2, "role": "outer", "geometry": geom(outer)},
                    {"type": "way", "ref": 4, "role": "inner", "geometry": geom(inner)},
                ],
            },
        ]
    }
    data = parse_osm(json.loads(json.dumps(payload)), bounds)
    assert len(data.buildings) == 1
    assert len(data.buildings[0][0].interiors) == 1
    assert data.buildings[0][1] == 12
    assert 0.3 < extract_metrics(data).area_km2 < 0.5
