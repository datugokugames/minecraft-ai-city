"""Feature blending, bounded rule plans, and optional structured OpenAI proposals."""

from __future__ import annotations

import copy
import json
import math
import os
import warnings

import numpy as np
import requests
from pydantic import ValidationError

from .models import CityPlan, Metrics, Profile


def blend_profiles(profiles: list[Profile], weights: list[float] | None = None):
    if not profiles:
        raise ValueError("at least one profile is required")
    weights = [1.0] * len(profiles) if weights is None else weights
    if len(weights) != len(profiles) or any(not math.isfinite(w) or w < 0 for w in weights):
        raise ValueError("one finite nonnegative weight is required for each profile")
    if sum(weights) <= 0:
        raise ValueError("weights must have a positive sum")
    weights = [w / sum(weights) for w in weights]
    values = {
        key: sum(p.metrics.model_dump()[key] * w for p, w in zip(profiles, weights))
        for key in Metrics.model_fields
    }
    for key in ("building_count", "intersection_count", "elevation_sample_count"):
        values[key] = round(values[key])
    sources = [
        {
            "name": p.name,
            "source": p.source,
            "weight": w,
            "attribution": p.attribution,
            "retrieved_at": p.retrieved_at,
        }
        for p, w in zip(profiles, weights)
    ]
    return Metrics(**values), sources


def rule_plan(metrics: Metrics, seed: int) -> CityPlan:
    # Approximate real-world metre statistics at a deliberately compressed city scale.
    density_spacing = 2000 / max(metrics.road_density_km_km2, 1) * 0.65
    block_spacing = math.sqrt(metrics.median_block_area_m2 or 6400) * 0.65
    junction_spacing = math.sqrt(1_000_000 / max(metrics.intersection_density_km2, 1)) * 0.65
    spacing = int(np.clip(np.mean([density_spacing, block_spacing, junction_spacing]), 32, 64))
    # In sparsely tagged areas, park-only OSM polygons must not imply a park-only city.
    confidence = metrics.landuse_tag_coverage
    commercial = round(
        float(np.clip(metrics.commercial_share * confidence + 0.25 * (1 - confidence), 0.15, 0.42)),
        3,
    )
    park = round(
        float(np.clip(metrics.park_share * confidence + 0.10 * (1 - confidence), 0.08, 0.25)), 3
    )
    return CityPlan(
        name=f"Aobamachi {seed}",
        description="A compact original Japanese town synthesized from aggregate urban metrics.",
        style="mixed",
        road_spacing=spacing,
        road_width=7,
        irregularity=float(
            np.clip(
                0.06
                + (1 - metrics.orthogonal_road_share) * 0.2
                + (0.8 - metrics.mean_block_compactness) * 0.08,
                0.04,
                0.25,
            )
        ),
        residential_ratio=round(1 - commercial - park, 3),
        commercial_ratio=commercial,
        park_ratio=park,
        building_coverage=float(np.clip(metrics.building_coverage, 0.18, 0.55)),
        max_floors=int(np.clip(math.ceil(metrics.mean_height_m / 3) + 1, 3, 8)),
        railway=True,
    )


def _strict_schema():
    schema = copy.deepcopy(CityPlan.model_json_schema())
    # OpenAI strict schemas require every property, including local default fields.
    schema["required"] = list(schema["properties"])
    for prop in schema["properties"].values():
        prop.pop("default", None)
    return schema


def make_plan(metrics: Metrics, seed: int, mode="auto", *, session=None):
    fallback = rule_plan(metrics, seed)
    if mode not in ("auto", "rules", "ai"):
        raise ValueError("unknown planning mode")
    key = os.environ.get("OPENAI_API_KEY")
    if mode == "rules" or not key:
        if mode == "ai" and not key:
            warnings.warn("OPENAI_API_KEY is absent; using rule-based planning.", stacklevel=2)
        return fallback, "rules"
    prompt = {
        "instruction": "Design an original 256-block Japanese Minecraft town. Use aggregate metrics only; never reproduce any real map. Return the schema. Ratios must sum to 1. Preserve practical small blocks; architectural style must be one of the allowed choices.",
        "seed": seed,
        "metrics": metrics.model_dump(),
        "baseline": fallback.model_dump(),
    }
    try:
        response = (session or requests).post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"),
                "messages": [
                    {"role": "system", "content": "You are a city planner. Output validated JSON."},
                    {"role": "user", "content": json.dumps(prompt)},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "city_plan",
                        "strict": True,
                        "schema": _strict_schema(),
                    },
                },
                "max_completion_tokens": 1200,
            },
            timeout=(10, 45),
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        plan = CityPlan.model_validate_json(content)
        return plan, "openai"
    except (
        requests.RequestException,
        ValidationError,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
    ) as exc:
        # Do not log request headers, API responses, or potentially credential-bearing exceptions.
        warnings.warn(
            f"AI plan unavailable or invalid ({type(exc).__name__}); using validated rules.",
            stacklevel=2,
        )
        return fallback, "rules_fallback"
