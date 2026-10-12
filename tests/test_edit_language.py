import json

import pytest
import requests

from minecraft_city.edit_language import _schema, interpret, parse_rules
from minecraft_city.edit_models import ClarificationRequired, Selection
from minecraft_city.generation import generate_city
from minecraft_city.planning import rule_plan
from minecraft_city.project import create_project


@pytest.fixture(scope="module")
def project(blended):
    metrics, sources = blended
    return create_project(
        generate_city(metrics, sources, rule_plan(metrics, 42), 42, layout="organic"), "青葉市"
    )


@pytest.mark.parametrize(
    "text,selection,action,kind,count",
    [
        ("中央区の空き地にコンビニを作って", Selection(), "add_buildings", "convenience_store", 1),
        ("この範囲に公園を作って", Selection(bounds=(10, 10, 30, 30)), "add_park", None, 1),
        ("駅の東側にマンションを建てて", Selection(), "add_buildings", "apartment", 1),
        (
            "この地区に住宅を5棟追加して",
            Selection(district="district-2"),
            "add_buildings",
            "house",
            5,
        ),
        ("指定した建物の外観をもっと現代的にして", Selection(building_id=1), "restyle", None, 1),
        ("建物1の外観を和風にして", Selection(), "restyle", None, 1),
        ("ここに戸建てを建てて", Selection(point=(25, 30)), "add_buildings", "house", 1),
        ("この辺に公園を作って", Selection(point=(25, 30)), "add_park", None, 1),
        ("青葉市に住宅を５棟追加して。", Selection(), "add_buildings", "house", 5),
        ("X=20,Z=30にコンビニを作って", Selection(), "add_buildings", "convenience_store", 1),
    ],
)
def test_supported_japanese(project, text, selection, action, kind, count):
    result = parse_rules(text, project, selection)
    assert (result.action, result.building_type, result.count) == (action, kind, count)


@pytest.mark.parametrize(
    "text",
    [
        "ここにコンビニを作って",
        "この辺に公園を作って",
        "この範囲に公園を作って",
        "この地区に住宅を5棟追加して",
        "指定した建物の外観をもっと現代的にして",
        "北極区にコンビニを作って",
        "中央区に大きなコンビニを作って",
        "中央区にコンビニと公園を作って",
        "川と橋を作って",
        "中央区の住宅を削除して",
        "中央区に住宅を建てないで",
        "中央区に住宅を0棟追加して",
        "中央区に住宅を11棟追加して",
        "",
        "中央区にコンビニを作って、建物1も撤去して",
    ],
)
def test_unsupported_ambiguous_or_compound_requests_fail(project, text):
    with pytest.raises(ValueError):
        interpret(text, project, mode="rules")


def test_selector_conflicts_and_equivalent_ids(project):
    result = parse_rules("中央区の空き地にコンビニを作って", project, Selection(ward="ward-1"))
    assert result.selection.ward == "ward-1"
    with pytest.raises(ClarificationRequired, match="矛盾"):
        parse_rules("中央区にコンビニを作って", project, Selection(ward="北区"))


def test_no_key_and_rules_never_send_network(project, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.warns(UserWarning, match="absent"):
        intent, source = interpret("中央区にコンビニを作って", project, mode="ai", session=object())
    assert source == "rules"
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder")
    assert (
        interpret("中央区にコンビニを作って", project, mode="rules", session=object())[0] == intent
    )


def test_llm_schema_is_strict():
    schema = _schema()

    def visit(item):
        if isinstance(item, dict):
            assert "default" not in item
            assert "prefixItems" not in item
            if item.get("type") == "object":
                assert set(item["required"]) == set(item["properties"])
                assert item["additionalProperties"] is False
            for value in item.values():
                visit(value)
        elif isinstance(item, list):
            for value in item:
                visit(value)

    visit(schema)


@pytest.mark.parametrize(
    "behavior", ["valid", "timeout", "refusal", "invalid", "contradiction", "clarify"]
)
def test_ai_boundary(project, monkeypatch, behavior):
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder")
    text = "中央区にコンビニを作って"
    expected = parse_rules(text, project)
    value = expected.model_dump(mode="json")
    if behavior == "contradiction":
        value["count"] = 2
    if behavior == "invalid":
        value["delete_buildings"] = [0]

    class Fake:
        def post(self, url, **kwargs):
            assert url == "https://api.openai.com/v1/chat/completions"
            assert kwargs["json"]["response_format"]["json_schema"]["strict"]
            assert "test-placeholder" not in json.dumps(kwargs["json"])
            if behavior == "timeout":
                raise requests.Timeout("do not expose test-placeholder")
            return self

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "refusal": "refused" if behavior == "refusal" else None,
                            "content": json.dumps(
                                {
                                    "intent": None if behavior == "clarify" else value,
                                    "clarification": "場所を指定してください"
                                    if behavior == "clarify"
                                    else None,
                                }
                            ),
                        }
                    }
                ]
            }

    if behavior == "clarify":
        with pytest.raises(ClarificationRequired):
            interpret(text, project, mode="ai", session=Fake())
    elif behavior == "valid":
        result, source = interpret(text, project, mode="ai", session=Fake())
        assert result == expected and source == "openai"
    else:
        with pytest.warns(UserWarning) as caught:
            result, source = interpret(text, project, mode="ai", session=Fake())
        assert result == expected and source == "rules_fallback"
        assert "test-placeholder" not in str(caught[0].message)


def test_ai_cannot_invent_here_coordinates(project, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder")
    with pytest.raises(ClarificationRequired):
        interpret("ここにコンビニを建てて", project, mode="ai", session=object())


def test_same_label_across_administrative_levels_requires_id(project):
    renamed = project.model_copy(deep=True)
    renamed.city_name = renamed.wards[0].name
    with pytest.raises(ClarificationRequired, match="同じ名前"):
        parse_rules(f"{renamed.city_name}にコンビニを作って", renamed)
    result = parse_rules("コンビニを作って", renamed, Selection(ward=renamed.wards[0].id))
    assert result.selection.ward == renamed.wards[0].id


@pytest.mark.parametrize("grounded", [True, False])
def test_ai_paraphrase_requires_grounded_location(project, monkeypatch, grounded):
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder")
    intent = parse_rules("中央区にコンビニを作って", project)

    class Fake:
        def post(self, *args, **kwargs):
            return self

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"intent": intent.model_dump(mode="json"), "clarification": None}
                            )
                        }
                    }
                ]
            }

    if grounded:
        result, source = interpret(
            "中央区にコンビニを一軒お願い", project, mode="ai", session=Fake()
        )
        assert result == intent and source == "openai"
    else:
        with pytest.warns(UserWarning), pytest.raises(ClarificationRequired):
            interpret("コンビニを一軒お願い", project, mode="ai", session=Fake())
