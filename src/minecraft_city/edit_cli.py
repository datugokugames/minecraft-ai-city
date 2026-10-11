"""Thin CLI adapter; core editing functions are reusable without argparse."""

import json
from pathlib import Path

from .edit_models import EditError, Selection
from .editing import apply_edit, load_plan, prepare_edit, undo_edit, write_preview
from .models import save_json
from .project import load_project


def add_edit_parser(subparsers):
    root = subparsers.add_parser("edit", help="Plan, review, approve and undo Japanese city edits")
    actions = root.add_subparsers(dest="edit_action", required=True)
    plan = actions.add_parser(
        "plan", help="Create a proposal and before/after PNGs; no construction yet"
    )
    plan.add_argument("--project", type=Path, required=True)
    plan.add_argument("--instruction", required=True)
    plan.add_argument("--city-name")
    plan.add_argument("--ward")
    plan.add_argument("--district")
    plan.add_argument("--building-id", type=int)
    plan.add_argument("--at", type=float, nargs=2, metavar=("X", "Z"))
    plan.add_argument("--bounds", type=float, nargs=4, metavar=("XMIN", "ZMIN", "XMAX", "ZMAX"))
    plan.add_argument("--seed", type=int, default=42)
    plan.add_argument("--parser", choices=["rules", "auto", "ai"], default="auto")
    plan.add_argument("--output", type=Path, required=True)
    for action in ("apply", "undo"):
        cmd = actions.add_parser(
            action,
            help="Write a new approved snapshot"
            if action == "apply"
            else "Restore a matching previous snapshot",
        )
        cmd.add_argument("--project", type=Path, required=True)
        cmd.add_argument("--plan" if action == "apply" else "--history", type=Path, required=True)
        cmd.add_argument(
            "--approve", help="Full plan_id; absent or mismatched approval is rejected"
        )
        cmd.add_argument("--output", type=Path, required=True)


def run_edit(args):
    if args.output.exists():
        raise FileExistsError(f"Output already exists: {args.output}")
    project = load_project(args.project)
    if args.edit_action == "plan":
        try:
            selection = Selection(
                city_name=args.city_name,
                ward=args.ward,
                district=args.district,
                building_id=args.building_id,
                point=args.at,
                bounds=args.bounds,
            )
            plan, _ = prepare_edit(project, args.instruction, selection, args.seed, args.parser)
            write_preview(project, plan, args.output)
        except ValueError as exc:
            # Invalid instructions/collisions are explicit reviewable outcomes, not substitutions.
            if not args.output.exists():
                args.output.mkdir(parents=True, exist_ok=False)
                save_json(
                    args.output / "validation.json",
                    {
                        "valid": False,
                        "status": getattr(exc, "code", "invalid_edit"),
                        "errors": [str(exc)],
                        "instruction": args.instruction,
                    },
                )
            raise
        print(
            json.dumps(
                {
                    "status": "awaiting_approval",
                    "plan_id": plan.plan_id,
                    "output": str(args.output),
                    "changes": plan.validation,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.edit_action == "apply":
        apply_edit(project, load_plan(args.plan), args.approve, args.output)
    elif args.edit_action == "undo":
        undo_edit(project, args.history, args.approve, args.output)
    else:
        raise EditError("Unknown edit command")
