"""CLI: offline demo, real analysis, comparison, generation, and preview replay."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

from .analysis import PRESETS, analyze_osm, sample_profile
from .building import build_city
from .generation import generate_city, validate_city
from .models import City, CityPlan, Metrics, load_profile, save_json
from .planning import blend_profiles, make_plan
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


def parser():
    root = argparse.ArgumentParser(
        description="Minecraft AI City Generator — original Japanese cities"
    )
    sub = root.add_subparsers(dest="command", required=True)
    analyze = sub.add_parser(
        "analyze", help="Analyze synthetic samples or small OSM bounding boxes"
    )
    choice = analyze.add_mutually_exclusive_group(required=True)
    choice.add_argument("--sample", choices=PRESETS)
    choice.add_argument(
        "--region", choices=PRESETS, help="Fetch measured OSM data for a preset area"
    )
    choice.add_argument("--bbox", help="south,west,north,east in WGS84; max 4 km²")
    analyze.add_argument("--name", default="Custom region")
    analyze.add_argument("--output", type=Path, required=True)
    analyze.add_argument("--cache-dir", type=Path, default=Path(".cache/osm"))
    analyze.add_argument(
        "--no-fallback", action="store_true", help="Fail instead of using synthetic data"
    )
    compare = sub.add_parser("compare", help="Save a side-by-side metric comparison")
    compare.add_argument("profiles", type=Path, nargs="+")
    compare.add_argument("--output", type=Path, required=True)
    for command in ("generate", "demo"):
        generate = sub.add_parser(
            command,
            help="Generate with saved profiles"
            if command == "generate"
            else "Offline end-to-end demo",
        )
        generate.add_argument("--seed", type=int, default=42)
        generate.add_argument("--size", type=int, default=256)
        generate.add_argument("--layout", choices=["organic", "legacy"], default="organic")
        generate.add_argument("--output", type=Path, default=Path("output"))
        if command == "generate":
            generate.add_argument("--profile", type=Path, action="append", required=True)
            generate.add_argument(
                "--weights",
                type=float,
                nargs="+",
                help="One weight per --profile (normalized automatically)",
            )
            generate.add_argument("--planner", choices=["auto", "rules", "ai"], default="auto")
            generate.add_argument(
                "--plan-file", type=Path, help="Use a previously saved and validated plan"
            )
    preview = sub.add_parser("preview", help="Render an existing city JSON without network access")
    preview.add_argument("--city", type=Path, required=True)
    preview.add_argument("--output", type=Path, required=True)
    preview.add_argument("--scale", type=int, choices=range(1, 9), default=4)
    rebuild = sub.add_parser(
        "build", help="Rebuild a saved city JSON, including schematic and preview"
    )
    rebuild.add_argument("--city", type=Path, required=True)
    rebuild.add_argument("--output", type=Path, required=True)
    audit = sub.add_parser("evaluate", help="Evaluate saved road morphology and frontage")
    audit.add_argument("--city", type=Path, required=True)
    audit.add_argument("--output", type=Path, required=True)
    compare_seeds = sub.add_parser(
        "compare-seeds", help="Generate preview/quality comparisons without Minecraft export"
    )
    compare_seeds.add_argument("--profile", type=Path, action="append")
    compare_seeds.add_argument("--weights", type=float, nargs="+")
    compare_seeds.add_argument("--seeds", type=int, nargs="+", default=[42, 2026, 7])
    compare_seeds.add_argument("--size", type=int, default=256)
    compare_seeds.add_argument("--output", type=Path, required=True)
    project = sub.add_parser("project", help="Manage named wards, districts and occupied parcels")
    actions = project.add_subparsers(dest="project_action", required=True)
    initialize = actions.add_parser("init", help="Wrap an existing city without rebuilding it")
    initialize.add_argument("--city", type=Path, required=True)
    initialize.add_argument("--name", help="Administrative city name")
    initialize.add_argument("--ward-name", default="中央区")
    initialize.add_argument("--output", type=Path, required=True)
    configure = actions.add_parser(
        "configure", help="Save explicit metadata changes as a new snapshot"
    )
    configure.add_argument("--project", type=Path, required=True)
    configure.add_argument("--settings", type=Path, required=True)
    configure.add_argument("--output", type=Path, required=True)
    inspect = actions.add_parser("inspect", help="Print the validated administrative hierarchy")
    inspect.add_argument("--project", type=Path, required=True)
    export = actions.add_parser("export", help="Build the unchanged city into a new directory")
    export.add_argument("--project", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    return root


def run(args):
    if args.command == "project":
        from .project import (
            ProjectSettings,
            configure_project,
            create_project,
            load_project,
            project_summary,
            save_project,
        )

        if args.project_action == "init":
            city = City.model_validate_json(args.city.read_text(encoding="utf-8"))
            project = create_project(city, args.name, args.ward_name)
            save_project(args.output, project)
        else:
            project = load_project(args.project)
            if args.project_action == "configure":
                settings = ProjectSettings.model_validate_json(
                    args.settings.read_text(encoding="utf-8")
                )
                project = configure_project(project, settings)
                save_project(args.output, project)
            elif args.project_action == "export":
                args.output.mkdir(parents=True, exist_ok=False)
                export_city(project.city.model_copy(deep=True), args.output)
                save_project(args.output / "project.json", project)
                save_json(args.output / "hierarchy.json", project_summary(project))
                return
        print(json.dumps(project_summary(project), ensure_ascii=False, indent=2))
    elif args.command == "analyze":
        if args.sample:
            profile = sample_profile(args.sample)
        else:
            bounds = (
                PRESETS[args.region]
                if args.region
                else tuple(float(v) for v in args.bbox.split(","))
            )
            profile = analyze_osm(
                args.region or args.name,
                bounds,
                args.cache_dir,
                None if args.no_fallback else args.region or "tokyo",
            )
        save_json(args.output, profile)
        print(f"Saved {args.output} (source={profile.source})")
    elif args.command == "compare":
        profiles = [load_profile(path) for path in args.profiles]
        result = {
            "profiles": [
                {"name": p.name, "source": p.source, "attribution": p.attribution} for p in profiles
            ],
            "metrics": {
                key: [p.metrics.model_dump()[key] for p in profiles] for key in Metrics.model_fields
            },
        }
        save_json(args.output, result)
        print(f"Saved comparison to {args.output}")
    elif args.command in ("generate", "demo"):
        if args.command == "demo":
            profiles = [sample_profile("tokyo"), sample_profile("kyoto")]
            for name, profile in zip(("tokyo", "kyoto"), profiles):
                save_json(args.output / "profiles" / f"{name}.json", profile)
            weights, mode, plan_file = [0.6, 0.4], "rules", None
        else:
            profiles = [load_profile(path) for path in args.profile]
            weights, mode, plan_file = args.weights, args.planner, args.plan_file
        metrics, sources = blend_profiles(profiles, weights)
        if plan_file:
            plan = CityPlan.model_validate_json(plan_file.read_text(encoding="utf-8"))
            source = "saved_plan"
        else:
            plan, source = make_plan(metrics, args.seed, mode)
        city = generate_city(
            metrics, sources, plan, args.seed, args.size, source, layout=args.layout
        )
        export_city(city, args.output)
    elif args.command == "evaluate":
        from .quality import evaluate_city

        city = City.model_validate_json(args.city.read_text(encoding="utf-8"))
        validate_city(city)
        save_json(args.output, evaluate_city(city))
        print(f"Saved evaluation to {args.output}")
    elif args.command == "compare-seeds":
        from .comparison import compare_seeds

        profiles = (
            [load_profile(p) for p in args.profile]
            if args.profile
            else [sample_profile("tokyo"), sample_profile("kyoto")]
        )
        metrics, sources = blend_profiles(profiles, args.weights)
        compare_seeds(metrics, sources, args.seeds, args.size, args.output)
    elif args.command in ("preview", "build"):
        city = City.model_validate_json(args.city.read_text(encoding="utf-8"))
        validate_city(city)
        if args.command == "preview":
            render_preview(city, args.output, args.scale)
            print(f"Saved {args.output}")
        else:
            export_city(city, args.output)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        run(args)
    except (ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    except requests.RequestException as exc:
        print(
            f"Network error ({type(exc).__name__}); retry later or use --sample.", file=sys.stderr
        )
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
