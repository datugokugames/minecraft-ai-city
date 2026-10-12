"""Conservative Japanese grammar and optional schema-constrained LLM interpretation."""

from __future__ import annotations

import copy
import json
import os
import re
import unicodedata
import warnings

import requests

from .edit_models import ClarificationRequired, EditIntent, Selection
from .models import StrictModel


def _merge(selection, **updates):
    data = selection.model_dump()
    for key, value in updates.items():
        if value is None:
            continue
        if data[key] is not None and data[key] != value:
            raise ClarificationRequired(f"指示と指定場所の {key} が矛盾しています。")
        data[key] = value
    return Selection.model_validate(data)


def _location(text, project, selection):
    result = selection.model_copy(deep=True)
    # Resolve the longest labels first; a district name may contain its city's name.
    labels = [(project.city_name, "city_name")]
    labels += [(w.name, "ward") for w in project.wards]
    labels += [(d.name, "district") for d in project.districts]
    for name, key in sorted(set(labels), key=lambda item: (-len(item[0]), item[0], item[1])):
        if name in text:
            if len({kind for label, kind in labels if label == name}) > 1:
                raise ClarificationRequired(
                    "都市・区・地区で同じ名前があります。場所はCLIのID指定を使用してください。"
                )
            records = (
                project.wards if key == "ward" else project.districts if key == "district" else []
            )
            current = getattr(result, key)
            if not any(r.id == current and r.name == name for r in records):
                result = _merge(result, **{key: name})
            text = text.replace(name, "")
    if "駅の東側" in text:
        result = _merge(result, station_side="east")
        text = text.replace("駅の東側", "")
    match = re.search(
        r"X\s*=\s*(-?\d+(?:\.\d+)?)\s*[,、]?\s*Z\s*=\s*(-?\d+(?:\.\d+)?)", text, re.IGNORECASE
    )
    if match:
        result = _merge(result, point=tuple(float(v) for v in match.groups()))
        text = text[: match.start()] + text[match.end() :]
    match = re.search(r"建物\s*(\d+)", text)
    if match:
        result = _merge(result, building_id=int(match[1]))
        text = text[: match.start()] + text[match.end() :]
    if any(word in text for word in ("ここ", "この範囲", "指定範囲")) and not (
        result.point or result.bounds
    ):
        raise ClarificationRequired(
            "場所を --at X Z または --bounds XMIN ZMIN XMAX ZMAX で指定してください。"
        )
    if "この地区" in text and result.district is None:
        raise ClarificationRequired("対象地区を --district で指定してください。")
    if (
        any(word in text for word in ("指定した建物", "この建物"))
        and result.building_id is None
        and not (result.point or result.bounds)
    ):
        raise ClarificationRequired("対象建物を --building-id または座標・範囲で指定してください。")
    if not re.fullmatch(
        r"(?:の|に|内|空き地|未使用地|この範囲|指定範囲|この地区|ここ|指定した建物|この建物|沿い|\s)*",
        text,
    ):
        raise ClarificationRequired(f"場所または条件を解釈できません: {text!r}")
    return result


def parse_rules(instruction, project, selection=None):
    text = (
        unicodedata.normalize("NFKC", instruction).strip().rstrip("。！!").replace("この辺", "ここ")
    )
    selection = selection or Selection()
    if not text or len(text) > 2000:
        raise ClarificationRequired("1〜2000文字の建築指示を指定してください。")
    match = re.fullmatch(
        r"(.*?)(コンビニ|マンション|住宅|戸建て)(?:を)?(?:(\d+)棟)?(?:を)?(?:作って|建てて|建設して|追加して)",
        text,
    )
    if match:
        location, kind, count = match.groups()
        return EditIntent(
            action="add_buildings",
            building_type={
                "コンビニ": "convenience_store",
                "マンション": "apartment",
                "住宅": "house",
                "戸建て": "house",
            }[kind],
            count=int(count or 1),
            selection=_location(location, project, selection),
        )
    match = re.fullmatch(r"(.*?)公園を(?:作って|追加して|配置して)", text)
    if match:
        return EditIntent(action="add_park", selection=_location(match[1], project, selection))
    match = re.fullmatch(
        r"(.*?)の外観を(?:もっと)?(現代的|モダン|和風|暖かい感じ)に(?:して|変更して)", text
    )
    if match:
        return EditIntent(
            action="restyle",
            palette={
                "現代的": "modern",
                "モダン": "modern",
                "和風": "traditional",
                "暖かい感じ": "warm",
            }[match[2]],
            selection=_location(match[1], project, selection),
        )
    raise ClarificationRequired(
        "未対応の指示です。建物追加、公園追加、建物の外観変更を1操作ずつ指定してください。"
    )


class AIInterpretation(StrictModel):
    intent: EditIntent | None
    clarification: str | None


def _schema():
    schema = copy.deepcopy(AIInterpretation.model_json_schema())

    def visit(item):
        if isinstance(item, dict):
            item.pop("default", None)
            if "prefixItems" in item:
                # OpenAI's strict subset uses homogeneous arrays, not tuple prefixItems.
                items = item.pop("prefixItems")
                item["items"] = items[0]
            if item.get("type") == "object":
                item.setdefault("properties", {})
                item["required"] = list(item["properties"])
                item["additionalProperties"] = False
            for value in item.values():
                visit(value)
        elif isinstance(item, list):
            for value in item:
                visit(value)

    visit(schema)
    return schema


def interpret(instruction, project, selection=None, mode="auto", *, session=None):
    selection = selection or Selection()
    if mode not in {"auto", "rules", "ai"}:
        raise ValueError("unknown editing parser mode")
    if not instruction.strip() or len(instruction) > 2000:
        raise ClarificationRequired("1〜2000文字の建築指示を指定してください。")
    if re.search(
        r"削除|撤去|取り壊|解体|移動|移設|作らない|建てない|追加しない|変更しない|しないで|作るな|建てるな",
        instruction,
    ):
        raise ClarificationRequired(
            "削除・移動・否定を含む指示はPhase 3Aでは実行しません。追加または外観変更を明示してください。"
        )
    try:
        rules = parse_rules(instruction, project, selection)
    except ValueError:
        rules = None
    key = os.environ.get("OPENAI_API_KEY")
    if mode == "rules" or not key:
        if mode == "ai" and not key:
            warnings.warn("OPENAI_API_KEY is absent; using limited Japanese rules.", stacklevel=2)
        return parse_rules(instruction, project, selection), "rules"
    # Deictic references are never resolved by invented model coordinates.
    if any(w in instruction for w in ("ここ", "この範囲", "指定範囲")) and not (
        selection.point or selection.bounds
    ):
        raise ClarificationRequired("曖昧な場所には --at または --bounds が必要です。")
    try:
        response = (session or requests).post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"),
                "messages": [
                    {
                        "role": "system",
                        "content": "Convert Japanese to exactly one bounded city edit. Only add house/apartment/convenience_store, add park, or restyle an existing building. Never delete, move roads, invent coordinates/IDs, ignore quantities, negation, or compound requests. Unknown/ambiguous requests must return intent=null and clarification. Explicit selection must be preserved. No location means clarification, not citywide editing.",
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "instruction": instruction,
                                "selection": selection.model_dump(mode="json"),
                                "city_name": project.city_name,
                                "wards": [w.model_dump() for w in project.wards],
                                "districts": [d.model_dump() for d in project.districts],
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "city_edit_intent",
                        "strict": True,
                        "schema": _schema(),
                    },
                },
                "max_completion_tokens": 1400,
            },
            timeout=(10, 45),
        )
        response.raise_for_status()
        message = response.json()["choices"][0]["message"]
        if message.get("refusal"):
            raise ValueError("model refusal")
        proposal = AIInterpretation.model_validate_json(message["content"])
        if proposal.intent is None or proposal.clarification:
            raise ClarificationRequired(proposal.clarification or "指示を解釈できませんでした。")
        result = proposal.intent
        result.selection = _merge(result.selection, **selection.model_dump(exclude_none=True))
        if result.selection.bounds is not None and selection.bounds is None:
            raise ValueError("AI invented a bounding rectangle; use --bounds")
        if result.selection.point is not None and selection.point is None:
            coordinate_match = re.search(
                r"X\s*=\s*(-?\d+(?:\.\d+)?)\s*[,、]?\s*Z\s*=\s*(-?\d+(?:\.\d+)?)",
                unicodedata.normalize("NFKC", instruction),
                re.IGNORECASE,
            )
            if coordinate_match is None or result.selection.point != tuple(
                float(v) for v in coordinate_match.groups()
            ):
                raise ValueError("AI invented coordinates")
        if result.selection.building_id is not None and selection.building_id is None:
            building_match = re.search(r"建物\s*(\d+)", unicodedata.normalize("NFKC", instruction))
            if building_match is None or result.selection.building_id != int(building_match[1]):
                raise ValueError("AI invented a building ID")
        # Known grammar is authoritative, even when an LLM is explicitly selected.
        if rules is not None and result != rules:
            raise ValueError("AI interpretation contradicts supported explicit instruction")
        if rules is None:
            # An LLM may paraphrase the operation, but cannot choose a target area
            # on behalf of a user who supplied no location.
            for field, records in (
                ("city_name", [(project.city_name, project.city_name)]),
                ("ward", [(w.id, w.name) for w in project.wards]),
                ("district", [(d.id, d.name) for d in project.districts]),
            ):
                value = getattr(result.selection, field)
                if (
                    value is not None
                    and getattr(selection, field) is None
                    and not any(
                        value in (identifier, name) and name in instruction
                        for identifier, name in records
                    )
                ):
                    raise ValueError("AI invented a named selection")
            if (
                result.selection.station_side is not None
                and selection.station_side is None
                and "駅の東側" not in instruction
            ):
                raise ValueError("AI invented a station-relative selection")
            for key_name, names in (
                ("city_name", [project.city_name]),
                ("ward", [w.name for w in project.wards]),
                ("district", [d.name for d in project.districts]),
            ):
                for name in names:
                    if name in instruction and getattr(result.selection, key_name) != name:
                        raise ValueError("AI omitted an explicit named selection")
            requested_count = re.search(r"(\d+)棟", unicodedata.normalize("NFKC", instruction))
            if requested_count and result.count != int(requested_count[1]):
                raise ValueError("AI changed an explicit quantity")
        return result, "openai"
    except ClarificationRequired:
        raise
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
        warnings.warn(
            f"AI edit unavailable or invalid ({type(exc).__name__}); trying exact Japanese rules.",
            stacklevel=2,
        )
        return parse_rules(instruction, project, selection), "rules_fallback"
