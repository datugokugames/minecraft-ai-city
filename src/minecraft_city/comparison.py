"""Repeatable multi-seed visual comparison, including failures rather than cherry-picking."""

from pathlib import Path

from PIL import Image, ImageDraw

from .generation import generate_city
from .models import save_json
from .planning import rule_plan
from .preview import _font, render_preview


def compare_seeds(metrics, sources, seeds, size, output):
    if not seeds or len(seeds) > 24 or len(set(seeds)) != len(seeds):
        raise ValueError("Specify 1..24 distinct seeds")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    results, tiles = [], []
    for seed in seeds:
        try:
            city = generate_city(
                metrics, sources, rule_plan(metrics, seed), seed, size, layout="organic"
            )
            target = output / f"seed-{seed}"
            save_json(target / "city.json", city)
            save_json(target / "quality.json", city.quality)
            path = render_preview(city, target / "preview.png")
            with Image.open(path) as image:
                tiles.append(image.resize((540, 620), Image.Resampling.LANCZOS))
            results.append(
                {
                    "seed": seed,
                    "valid": True,
                    "buildings": len(city.buildings),
                    "quality": city.quality,
                }
            )
            print(
                f"seed={seed}: {len(city.buildings)} buildings; quality warnings={len(city.quality['warnings'])}",
                flush=True,
            )
        except ValueError as exc:
            results.append({"seed": seed, "valid": False, "error": str(exc)})
            tile = Image.new("RGB", (540, 620), "#efdad5")
            ImageDraw.Draw(tile).text(
                (20, 20),
                f"seed={seed}: GENERATION FAILED\nSee comparison.json",
                fill="#773322",
                font=_font(18),
            )
            tiles.append(tile)
    columns = min(3, len(tiles))
    sheet = Image.new(
        "RGB", (columns * 540, ((len(tiles) + columns - 1) // columns) * 620), "#f4f1e8"
    )
    for i, tile in enumerate(tiles):
        sheet.paste(tile, ((i % columns) * 540, (i // columns) * 620))
    sheet.save(output / "comparison.png")
    save_json(
        output / "comparison.json",
        {
            "sources": sources,
            "size": size,
            "results": results,
            "all_valid": all(r["valid"] for r in results),
        },
    )
    if not all(r["valid"] for r in results):
        raise ValueError("Some seeds failed; all outcomes were saved in comparison.json")
    return results
