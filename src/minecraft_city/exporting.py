"""Shared artifact export for generation, CLI and approved editing."""

import json
from pathlib import Path

from .building import build_city
from .generation import validate_city
from .models import save_json
from .preview import render_preview
from .schematic import write_schematic


def export_city(city, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    city.validation = validate_city(city)
    volume = build_city(city)
    metadata = write_schematic(
        volume,
        output / "city.schem",
        city.plan.name,
        "; ".join(sorted({source["attribution"] for source in city.sources})),
    )
    render_preview(city, output / "preview.png")
    save_json(output / "city.json", city)
    save_json(output / "plan.json", city.plan)
    save_json(
        output / "report.json",
        {
            "validation": city.validation,
            "schematic": metadata,
            "sources": city.sources,
            "planning_source": city.planning_source,
            "layout": city.layout,
            "quality": city.quality,
        },
    )
    print(
        json.dumps(
            {"output": str(output.resolve()), **city.validation, "schematic": metadata},
            ensure_ascii=False,
            indent=2,
        )
    )
