"""Standalone exterior design export, using the same engine as city editing."""

from pathlib import Path

import numpy as np
from PIL import Image

from .architecture import design, parse_architecture, templates
from .architecture_geometry import render_architecture, render_isometric
from .architecture_models import ArchitectureRequest
from .models import save_json
from .schematic import write_schematic


def add_architecture_parser(sub):
    cmd = sub.add_parser(
        "architecture", help="Design an original hollow exterior and export JSON/PNG/schem"
    )
    cmd.add_argument("--instruction", required=True)
    cmd.add_argument("--category", choices=list(templates()))
    cmd.add_argument("--width", type=int)
    cmd.add_argument("--depth", type=int)
    cmd.add_argument("--height", type=int)
    cmd.add_argument("--mode", choices=["local", "llm", "auto"], default="local")
    cmd.add_argument("--seed", type=int, default=42)
    cmd.add_argument("--output", type=Path, required=True)


def export_design(spec, execution, output):
    output = Path(output)
    volume = render_architecture(spec)
    output.mkdir(parents=True, exist_ok=False)
    save_json(output / "design.json", spec)
    save_json(output / "execution.json", execution)
    write_schematic(volume, output / "building.schem", spec.category)
    render_isometric(volume, output / "isometric.png")
    top = volume.blocks.shape[0] - 1 - np.argmax(volume.blocks[::-1] != 0, axis=0)
    z, x = np.indices(top.shape)
    ids = volume.blocks[top, z, x]
    colours = []
    for name in volume.palette:
        colours.append(
            (117, 157, 100)
            if "grass" in name or "leaves" in name
            else (100, 174, 197)
            if "glass" in name
            else (165, 128, 80)
            if "oak" in name
            else (180, 116, 97)
            if "bricks" in name
            else (195, 196, 187)
        )
    pixels = np.array(colours, dtype=np.uint8)[ids]
    Image.fromarray(pixels).resize(
        (spec.site_width * 10, spec.site_depth * 10), Image.Resampling.NEAREST
    ).save(output / "top.png")
    save_json(
        output / "verification.json",
        {
            "category": spec.category,
            "site": [spec.site_width, spec.site_depth],
            "height": spec.height,
            "floors": spec.floors,
            "solid_blocks": int(np.count_nonzero(volume.blocks)),
            "mode": execution["source"],
            "approved_world_changes": 0,
            "hollow_interior": True,
        },
    )


def run_architecture(args):
    explicit = None
    if args.category:
        template = templates()[args.category]
        explicit = ArchitectureRequest(
            category=args.category,
            width=args.width if args.width is not None else template.width,
            depth=args.depth if args.depth is not None else template.depth,
            height=args.height,
            mode=args.mode,
        )
    elif any(v is not None for v in (args.width, args.depth, args.height)):
        raise ValueError(
            "数値のCLI引数には --category を指定するか、日本語に寸法を含めてください。"
        )
    request, _ = parse_architecture(args.instruction, explicit=explicit)
    request.mode = args.mode
    spec, execution = design(request, args.seed)
    export_design(spec, execution, args.output)
    print(
        f"外装設計保存: {args.output} / {spec.category} / 敷地{spec.site_width}×{spec.site_depth} / 高さ{spec.height} / {execution['source']}"
    )
