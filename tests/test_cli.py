import json
import subprocess
import sys

from PIL import Image


def cli(*args):
    result = subprocess.run(
        [sys.executable, "-m", "minecraft_city", *map(str, args)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result


def test_offline_workflow(tmp_path):
    demo = tmp_path / "demo"
    cli("demo", "--seed", 42, "--output", demo)
    for name in ("city.json", "plan.json", "report.json", "preview.png", "city.schem"):
        assert (demo / name).is_file()
    report = json.loads((demo / "report.json").read_text())
    assert report["validation"]["valid"]
    assert report["planning_source"] == "rules"
    with Image.open(demo / "preview.png") as image:
        image.verify()
    cli(
        "compare",
        demo / "profiles/tokyo.json",
        demo / "profiles/kyoto.json",
        "--output",
        tmp_path / "compare.json",
    )
    cli("preview", "--city", demo / "city.json", "--output", tmp_path / "preview.png", "--scale", 2)
    replay = tmp_path / "replay"
    cli("build", "--city", demo / "city.json", "--output", replay)
    assert (replay / "city.schem").read_bytes() == (demo / "city.schem").read_bytes()
    cli(
        "generate",
        "--profile",
        demo / "profiles/tokyo.json",
        "--profile",
        demo / "profiles/kyoto.json",
        "--weights",
        0.6,
        0.4,
        "--seed",
        42,
        "--plan-file",
        demo / "plan.json",
        "--output",
        tmp_path / "planned",
    )
    assert (tmp_path / "planned/city.schem").read_bytes() == (demo / "city.schem").read_bytes()


def test_invalid_cli_inputs(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "minecraft_city", "demo", "--size", "10", "--output", str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "128..512" in result.stderr
