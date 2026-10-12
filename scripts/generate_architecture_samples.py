"""Reproduce five categories in two sizes without network or API keys."""

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw

from minecraft_city.architecture import design
from minecraft_city.architecture_cli import export_design
from minecraft_city.architecture_models import ArchitectureRequest


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    examples = [
        ("house", 18, 22, None),
        ("house", 25, 35, None),
        ("apartment", 18, 22, None),
        ("apartment", 30, 40, None),
        ("convenience_store", 18, 22, None),
        ("convenience_store", 20, 30, None),
        ("office", 24, 30, None),
        ("office", 40, 50, 60),
        ("station", 18, 22, None),
        ("station", 28, 34, None),
    ]
    images = []
    reports = []
    for index, (category, w, d, h) in enumerate(examples):
        request = ArchitectureRequest(category=category, width=w, depth=d, height=h, mode="local")
        spec, execution = design(request, 42 + index)
        directory = output / f"{category}-{w}x{d}"
        export_design(spec, execution, directory)
        report = json.loads((directory / "verification.json").read_text())
        reports.append(report)
        with Image.open(directory / "isometric.png") as source:
            tile = Image.new("RGB", (360, 360), "#edf1ef")
            image = source.copy()
            image.thumbnail((340, 310))
            tile.paste(image, ((360 - image.width) // 2, 25))
            ImageDraw.Draw(tile).text(
                (12, 335), f"{category} / {w}x{d} / H {spec.height}", fill="#294644"
            )
            images.append(tile)
    sheet = Image.new("RGB", (360 * 5, 360 * 2), "#edf1ef")
    for index, tile in enumerate(images):
        sheet.paste(tile, ((index % 5) * 360, (index // 5) * 360))
    sheet.save(output / "comparison.png")
    (output / "samples.json").write_text(json.dumps(reports, indent=2) + "\n")
    print(f"{len(reports)} exterior samples exported to {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.output)
