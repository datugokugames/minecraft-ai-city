import pytest
from shapely.geometry import LineString, box

from minecraft_city.analysis import UrbanData, extract_metrics, parse_osm
from minecraft_city.morphology import measure_network, road_width, source_morphology


@pytest.mark.parametrize(
    "lines,kind",
    [
        ([[(0, 0), (100, 0)], [(50, 0), (50, 50)]], "t_junction_share"),
        ([[(0, 0), (100, 0)], [(50, -50), (50, 50)]], "cross_junction_share"),
        ([[(0, 0), (100, 0)], [(50, 0), (90, 30)]], "t_junction_share"),
        (
            [[(0, 0), (50, 0)], [(0, 0), (-25, 43)], [(0, 0), (-25, -43)]],
            "irregular_junction_share",
        ),
    ],
)
def test_junction_shapes(lines, kind):
    metrics, _, _ = measure_network([LineString(line) for line in lines])
    assert metrics[kind] == 1


def test_curvature_distributions_and_spacing():
    lines = [LineString([(0, 0), (50, 0), (50, 50)])]
    metrics, distributions, _ = measure_network(lines)
    assert metrics["road_sinuosity"] == pytest.approx(2**0.5)
    assert metrics["turn_degrees_per_km"] == pytest.approx(900)
    assert sum(distributions["direction_15deg_share"]) == pytest.approx(1)
    assert distributions["junction_spacing_m"] == [100]


def test_widths_barriers_and_observation_coverage():
    assert road_width({"width": "8 m"}) == (8, True)
    assert road_width({"lanes": "2"}) == (6, False)
    assert road_width({"highway": "footway"}) == (2, False)
    data = UrbanData(box(0, 0, 100, 100), [LineString([(0, 50), (100, 50)])])
    data.road_tags = [{"highway": "primary", "width": "8"}]
    data.rivers = [LineString([(50, 0), (50, 100)])]
    data.railways = [LineString([(80, 0), (80, 100)])]
    data.elevations = [10, 24]
    metrics = extract_metrics(data)
    assert metrics.mean_road_width_m == 8
    assert metrics.width_tag_coverage == 1
    assert metrics.arterial_share == 1
    assert metrics.barrier_crossings_km2 == 200
    assert metrics.terrain_relief_m == 14
    assert metrics.elevation_sample_count == 2
    assert source_morphology(data)[1]["road_width_m"] == [8]


def test_osm_constraint_tags_are_parsed():
    def geometry(coords):
        return [{"lon": x, "lat": y} for x, y in coords]

    data = parse_osm(
        {
            "elements": [
                {
                    "type": "way",
                    "id": 1,
                    "tags": {"highway": "service", "width": "3"},
                    "geometry": geometry([(135, 35.003), (135.006, 35.003)]),
                },
                {
                    "type": "way",
                    "id": 2,
                    "tags": {"waterway": "river"},
                    "geometry": geometry([(135.003, 35), (135.003, 35.006)]),
                },
                {
                    "type": "way",
                    "id": 3,
                    "tags": {"railway": "rail"},
                    "geometry": geometry([(135.004, 35), (135.004, 35.006)]),
                },
                {"type": "node", "id": 4, "tags": {"ele": "12"}},
            ]
        },
        (35, 135, 35.006, 135.006),
    )
    assert len(data.rivers) == len(data.railways) == 1
    assert data.elevations == [12]
    assert data.road_tags[0]["width"] == "3"
