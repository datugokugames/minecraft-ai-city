import json

import pytest
import requests
from pydantic import ValidationError

from minecraft_city.models import CityPlan
from minecraft_city.planning import make_plan, rule_plan


def test_no_key_fallback(monkeypatch, blended):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.warns(UserWarning, match="absent"):
        plan, source = make_plan(blended[0], 42, "ai")
    assert source == "rules"
    assert isinstance(plan, CityPlan)


def test_sparse_landuse_tags_do_not_dominate(blended):
    m = blended[0].model_copy(
        update={"landuse_tag_coverage": 0.01, "park_share": 1.0, "commercial_share": 0.0}
    )
    assert rule_plan(m, 42).park_ratio < 0.12


def test_rules_never_contact_api(monkeypatch, blended):
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder")
    _plan, source = make_plan(blended[0], 42, "rules", session=object())
    assert source == "rules"


@pytest.mark.parametrize(
    "change",
    [
        {"residential_ratio": 0.79},
        {"max_floors": 1000},
        {"style": "../../file"},
        {"building_coverage": float("nan")},
        {"arbitrary": "forbidden"},
    ],
)
def test_reject_unsafe_plan(blended, change):
    payload = rule_plan(blended[0], 1).model_dump()
    payload.update(change)
    with pytest.raises(ValidationError):
        CityPlan.model_validate(payload)


@pytest.mark.parametrize("response_type", ["valid", "invalid", "timeout", "refusal"])
def test_ai_boundary(monkeypatch, blended, response_type):
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder")
    payload = rule_plan(blended[0], 42).model_dump()
    if response_type == "invalid":
        payload["max_floors"] = 900

    class Fake:
        def post(self, url, **kwargs):
            assert url.startswith("https://api.openai.com/")
            assert kwargs["json"]["response_format"]["type"] == "json_schema"
            if response_type == "timeout":
                raise requests.Timeout("must not leak test-placeholder")
            return self

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": None if response_type == "refusal" else json.dumps(payload)
                        }
                    }
                ]
            }

    if response_type == "valid":
        plan, source = make_plan(blended[0], 42, "ai", session=Fake())
        assert source == "openai"
    else:
        with pytest.warns(UserWarning, match="using validated rules") as warnings:
            plan, source = make_plan(blended[0], 42, "ai", session=Fake())
        assert "test-placeholder" not in str(warnings[0].message)
        assert source == "rules_fallback"
    assert plan.max_floors <= 8
