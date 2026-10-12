"""Compare all requested seeds without selecting only successful improvements."""

import argparse
import json
from pathlib import Path

from minecraft_city.models import City
from minecraft_city.project import create_project
from minecraft_city.urban_quality import export_improvement


def run(output, seeds):
    output.mkdir(parents=True, exist_ok=False)
    repo = Path(__file__).resolve().parents[1]
    reports = []
    for seed in seeds:
        try:
            source = repo / f"examples/natural-comparison/seed-{seed}/city.json"
            city = City.model_validate_json(source.read_text())
            report = export_improvement(
                create_project(city), output / f"seed-{seed}", seed=seed, max_additions=24
            )
            reports.append({"seed": seed, "success": True, **report})
        except (ValueError, OSError) as error:
            reports.append({"seed": seed, "success": False, "error": str(error)})
    (output / "comparison.json").write_text(json.dumps(reports, indent=2) + "\n")
    print(
        json.dumps(
            [
                {k: r.get(k) for k in ["seed", "success", "added_buildings", "target_reached"]}
                for r in reports
            ]
        )
    )
    if any(not r["success"] for r in reports):
        raise SystemExit(2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seeds", type=int, nargs="+", default=[42, 7, 19])
    args = parser.parse_args()
    run(args.output, args.seeds)
