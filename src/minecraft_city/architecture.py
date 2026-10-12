"""Shared Japanese architecture planning, local design and optional external design."""

from __future__ import annotations

import json
import math
import os
import re
import unicodedata
from importlib.resources import files

import numpy as np
import requests
from pydantic import Field, model_validator

from .architecture_models import (
    ArchitectureRequest,
    ArchitectureSpec,
    Category,
    Contract,
    Mass,
    Material,
    Windows,
)


class Template(Contract):
    category: Category
    label: str = Field(min_length=1, max_length=40)
    width: int = Field(ge=12, le=64)
    depth: int = Field(ge=14, le=64)
    floors: tuple[int, int]
    story_height: int = Field(ge=4, le=6)
    shapes: list[str]
    roofs: list[str]
    walls: list[Material]
    balconies: bool
    canopy: bool
    signage: bool
    parking: bool

    @model_validator(mode="after")
    def rules(self):
        if not 1 <= self.floors[0] <= self.floors[1] <= 23:
            raise ValueError("invalid template floor range")
        if not self.shapes or not set(self.shapes) <= {
            "rectangle",
            "l_shape",
            "courtyard",
            "stepped",
        }:
            raise ValueError("invalid shape rules")
        if not self.roofs or not set(self.roofs) <= {"flat", "gable", "terrace"} or not self.walls:
            raise ValueError("invalid roof/material rules")
        return self


def templates(path=None):
    raw = json.loads(
        (path or files("minecraft_city").joinpath("architecture_data/templates.json")).read_text(
            encoding="utf-8"
        )
    )
    if set(raw) != {"schema_version", "templates"} or raw["schema_version"] != 1:
        raise ValueError("invalid template collection")
    values = [Template.model_validate(item) for item in raw["templates"]]
    if len({t.category for t in values}) != len(values):
        raise ValueError("duplicate architecture template")
    return {t.category: t for t in values}


ALIASES = {
    "戸建住宅": "house",
    "戸建て": "house",
    "住宅": "house",
    "アパート": "rental_apartment",
    "マンション": "apartment",
    "オフィスビル": "office",
    "オフィス": "office",
    "商業ビル": "commercial",
    "商業施設": "commercial",
    "コンビニ": "convenience_store",
    "スーパーマーケット": "supermarket",
    "スーパー": "supermarket",
    "小学校": "school",
    "学校": "school",
    "駅舎": "station",
    "駅": "station",
    "工場": "factory",
    "倉庫": "warehouse",
    "公共施設": "public",
}


def is_architecture(instruction):
    text = unicodedata.normalize("NFKC", instruction)
    return bool(
        re.search(
            r"\d+\s*[×xX*]\s*\d+|幅\s*\d+|奥行き\s*\d+|高さ\s*\d+|周囲と調和|自由建築|駅舎|オフィス|商業施設|商業ビル|スーパーマーケット|スーパー|学校|工場|倉庫|公共施設|アパート",
            text,
        )
    )


def parse_architecture(instruction, project=None, selection=None, explicit=None):
    from .edit_language import _location
    from .edit_models import ClarificationRequired, Selection

    text = unicodedata.normalize("NFKC", instruction).strip().rstrip("。!！")
    text = (
        text.replace("この辺", "ここ")
        .replace("この土地", "この範囲")
        .replace("この場所", "ここ")
        .replace("自由建築", "")
    )
    if (
        not text
        or len(text) > 2000
        or re.search(r"削除|撤去|解体|移動|作らない|建てない|しない|棟", text)
    ):
        raise ClarificationRequired(
            "自由建築は1棟の追加のみです。否定・削除・複数操作は扱いません。"
        )
    explicit = (
        ArchitectureRequest.model_validate(explicit) if isinstance(explicit, dict) else explicit
    )
    dimensions = {}
    pairs = list(re.finditer(r"(\d+)\s*[×xX*]\s*(\d+)", text))
    if len(pairs) > 1:
        raise ClarificationRequired("幅×奥行きは1組だけ指定してください。")
    if pairs:
        dimensions.update(width=int(pairs[0][1]), depth=int(pairs[0][2]))
        text = text[: pairs[0].start()] + text[pairs[0].end() :]
    for label, field in (("幅", "width"), ("奥行き", "depth"), ("高さ", "height")):
        matches = list(re.finditer(label + r"\s*(\d+)(?:ブロック)?", text))
        if len(matches) > 1 or (matches and field in dimensions):
            raise ClarificationRequired(f"{label}を重複指定しています。")
        if matches:
            dimensions[field] = int(matches[0][1])
            text = text[: matches[0].start()] + text[matches[0].end() :]
    if ("width" in dimensions) != ("depth" in dimensions):
        raise ClarificationRequired("幅と奥行きを両方指定してください。")
    text = text.replace("、", "").replace(",", "").replace("周囲と調和する", "")
    match = re.fullmatch(
        r"(.*?)("
        + "|".join(sorted(ALIASES, key=len, reverse=True))
        + r"|建物)(?:を)?(?:作って|建てて|建設して|追加して)",
        text,
    )
    if not match:
        raise ClarificationRequired("カテゴリ・幅×奥行き・追加指示を1操作で指定してください。")
    location, label = match.groups()
    if label == "建物":
        category = explicit.category if explicit else "house"
        if not explicit and project and selection and selection.point:
            nearest = min(
                project.city.buildings,
                key=lambda b: (b.x - selection.point[0]) ** 2 + (b.z - selection.point[1]) ** 2,
                default=None,
            )
            category = {"mixed_use": "office", "shop": "commercial", "apartment": "apartment"}.get(
                nearest.kind if nearest else "", "house"
            )
    else:
        category = ALIASES[label]
    if explicit and category != explicit.category:
        raise ClarificationRequired("日本語のカテゴリと選択したカテゴリが矛盾しています。")
    template = templates()[category]
    values = {
        "category": category,
        "width": template.width,
        "depth": template.depth,
        "mode": os.environ.get("ARCHITECTURE_DEFAULT_MODE", "local"),
    }
    if explicit:
        values.update(explicit.model_dump(exclude={"design", "execution", "brief"}))
        for name, value in dimensions.items():
            if values.get(name) is not None and values[name] != value:
                raise ClarificationRequired(f"日本語と入力欄の{name}が矛盾しています。")
    values.update(dimensions)
    values["brief"] = instruction
    try:
        request = ArchitectureRequest.model_validate(values)
    except ValueError as error:
        raise ClarificationRequired(
            "敷地は幅12〜64・奥行き14〜64、高さ7〜96ブロックです。代替案：幅18×奥行き22、高さ10を指定してください。"
        ) from error
    selected = selection or Selection()
    if project:
        selected = _location(location, project, selected)
    elif location and not re.fullmatch(r"(?:ここ|に|の|この範囲|\s)*", location):
        raise ClarificationRequired(
            "単体生成では地名指定は扱いません。都市編集を使用してください。"
        )
    return request, selected


def context_for(project, x, z, district_id):
    nearby = sorted(project.city.buildings, key=lambda b: (b.x - x) ** 2 + (b.z - z) ** 2)[:8]
    district = next(d for d in project.city.districts if d.id == district_id)
    return {
        "district": district.character,
        "landuse": district.kind,
        "mean_floors": float(np.mean([b.floors for b in nearby])) if nearby else 2,
        "materials": [b.palette for b in nearby],
        "nearby_types": [b.kind for b in nearby],
    }


def local_design(request, seed, context=None, facing="north"):
    t = templates()[request.category]
    rng = np.random.default_rng(seed)
    context = context or {"mean_floors": 2, "landuse": "residential"}
    # Site dimensions are maxima. Setbacks, yards and connected wings vary independently.
    w, d = request.width, request.depth
    mw = max(5, w - int(rng.integers(4, max(5, w // 3 + 1))))
    mz = int(rng.integers(4, min(8, d - 7))) if d >= 16 else 3
    md = d - mz - int(rng.integers(2, min(5, d - mz - 4)))
    shape = (
        str(rng.choice(t.shapes))
        if w >= 22 and d >= 24
        else ("rectangle" if "rectangle" in t.shapes else "l_shape")
    )
    if request.height is None:
        floors = max(
            t.floors[0],
            min(t.floors[1], round(context.get("mean_floors", 2)) + int(rng.integers(0, 2))),
        )
        if request.category == "convenience_store":
            floors = 1
        height = floors * t.story_height + 3
    else:
        height = request.height
        floors = min(23, (height - 2) // t.story_height)
        if floors < t.floors[0]:
            raise ValueError("階高・屋根の高さが不足。高さ10ブロック以上を指定してください。")
        if (
            request.category
            in {"house", "convenience_store", "station", "supermarket", "factory", "warehouse"}
            and floors > t.floors[1]
        ):
            raise ValueError(
                f"{t.label}の階高・用途に不自然な高さです。高さ{t.floors[1] * t.story_height + 3}以下、またはオフィスを指定してください。"
            )
    masses = [Mass(x=2, z=mz, width=mw, depth=md, floors=floors)]
    if shape == "l_shape":
        masses = [
            Mass(x=2, z=mz, width=mw, depth=max(5, md // 2), floors=floors),
            Mass(x=2, z=mz, width=max(5, mw // 2), depth=md, floors=floors),
        ]
    elif shape == "courtyard" and mw >= 17 and md >= 13:
        wing = max(5, mw // 3)
        masses = [
            Mass(x=2, z=mz, width=mw, depth=5, floors=floors),
            Mass(x=2, z=mz, width=wing, depth=md, floors=floors),
            Mass(x=2 + mw - wing, z=mz, width=wing, depth=md, floors=floors),
        ]
    elif shape == "stepped" and floors >= 3 and mw >= 12:
        masses[0].floors = max(1, floors // 2)
        masses.append(Mass(x=4, z=mz + 2, width=mw - 4, depth=max(5, md - 4), floors=floors))
    else:
        shape = "rectangle" if shape == "stepped" or shape == "courtyard" else shape
    front_mass = masses[0]
    wall = str(rng.choice(t.walls))
    if "traditional" in context.get("materials", []) and "smooth_sandstone" in t.walls:
        wall = "smooth_sandstone"
    return ArchitectureSpec(
        category=request.category,
        site_width=w,
        site_depth=d,
        height=height,
        floors=floors,
        story_height=t.story_height,
        shape=shape,
        masses=masses,
        wall=wall,
        accent=str(rng.choice(["stone_bricks", "dark_oak_planks", "light_gray_concrete"])),
        roof_material="deepslate_tiles"
        if request.category in {"house", "warehouse", "station"}
        else "gray_concrete",
        roof=str(rng.choice(t.roofs)),
        windows=Windows(spacing=int(rng.integers(3, 6)), width=2, height=2),
        entrance_x=front_mass.x + front_mass.width // 2,
        balconies=t.balconies,
        canopy=t.canopy,
        signage=t.signage,
        parking=t.parking,
        planting=True,
        road_facing=facing,
        seed=seed,
    )


def validate_request_design(spec, request, seed, facing):
    spec = ArchitectureSpec.model_validate(spec.model_dump())
    if (spec.category, spec.site_width, spec.site_depth, spec.seed, spec.road_facing) != (
        request.category,
        request.width,
        request.depth,
        seed,
        facing,
    ) or (request.height is not None and spec.height != request.height):
        raise ValueError("AIが明示カテゴリ・寸法・向き・seedを変更しました。")
    t = templates()[request.category]
    if (
        spec.story_height != t.story_height
        or spec.roof not in t.roofs
        or spec.shape not in t.shapes
    ):
        raise ValueError("テンプレートの建築条件に違反しています。")
    if (
        request.category
        in {"house", "convenience_store", "station", "supermarket", "factory", "warehouse"}
        and spec.floors > t.floors[1]
    ):
        raise ValueError("用途の許容階数を超えています。")
    return spec


def design(request, seed, context=None, facing="north", *, session=None):
    local = local_design(request, seed, context, facing)
    key = os.environ.get("ARCHITECTURE_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if request.mode == "local" or not key:
        return local, {
            "source": "local" if request.mode == "local" else "local_no_key",
            "external_contacted": False,
            "usage": None,
            "estimated_cost_usd": None,
        }
    model = os.environ.get("ARCHITECTURE_MODEL", "gpt-4.1-mini")
    base = os.environ.get("ARCHITECTURE_API_BASE", "https://api.openai.com/v1").rstrip("/")
    if not base.startswith("https://") or "@" in base:
        raise ValueError("LLM endpoint must be configured HTTPS without URL credentials")
    try:
        response = (session or requests).post(
            base + "/chat/completions",
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": "Design one original Minecraft exterior as JSON. Use bounded rectangular masses, hollow interior, no block enumeration, commands, paths or NBT. Preserve all explicit dimensions/category/seed/road facing. Follow template constraints.",
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "request": request.model_dump(exclude={"design", "execution"}),
                                "context": context,
                                "template": templates()[request.category].model_dump(),
                                "local_reference": local.model_dump(),
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "architecture",
                        "schema": ArchitectureSpec.model_json_schema(),
                        "strict": False,
                    },
                },
                "max_completion_tokens": 2600,
            },
            timeout=(10, 45),
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        if len(content) > 20000:
            raise ValueError("AI response too large")
        spec = validate_request_design(
            ArchitectureSpec.model_validate_json(content), request, seed, facing
        )
        from .architecture_geometry import render_architecture

        render_architecture(spec)
        usage = data.get("usage", {})
        usage = {
            k: max(0, min(100000, int(usage.get(k, 0))))
            for k in ("prompt_tokens", "completion_tokens", "total_tokens")
        }
        cost = None
        rates = (
            os.environ.get("ARCHITECTURE_INPUT_USD_PER_MILLION"),
            os.environ.get("ARCHITECTURE_OUTPUT_USD_PER_MILLION"),
        )
        if all(rates):
            rates = [float(v) for v in rates]
            if not all(math.isfinite(v) and v >= 0 for v in rates):
                raise ValueError("invalid cost rates")
            cost = (usage["prompt_tokens"] * rates[0] + usage["completion_tokens"] * rates[1]) / 1e6
        return spec, {
            "source": "llm",
            "external_contacted": True,
            "model": model,
            "usage": usage,
            "estimated_cost_usd": cost,
        }
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as error:
        return local, {
            "source": "local_fallback",
            "external_contacted": True,
            "model": model,
            "failure": type(error).__name__,
            "usage": None,
            "estimated_cost_usd": None,
        }
