"""Prepare a new local Paper folder without replacing any existing server."""

from __future__ import annotations

import argparse
import json
import secrets
import shutil
import sys
import uuid
from pathlib import Path

from check_paper_server import server_files

from minecraft_city.project import load_project


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def prepare(root, cache, project, players, accept_eula=False):
    repo = Path(__file__).resolve().parents[1]
    plugin = repo / "paper/target/minecraft-ai-city-paper-0.4.0.jar"
    if not plugin.is_file():
        raise ValueError("先に mvn -f paper/pom.xml package を実行してください。")
    load_project(project)  # Validate before creating or downloading anything.
    players = [str(uuid.UUID(player)) for player in players]
    root.mkdir(parents=True, exist_ok=False)
    (root / "plugins/MinecraftAICity").mkdir(parents=True)
    server_files(root, cache)
    shutil.copy2(plugin, root / "plugins/MinecraftAICity.jar")
    token = secrets.token_urlsafe(40)
    private = root / "paper-private.json"
    private.write_text(json.dumps({"token": token, "players": players}, indent=2), encoding="utf-8")
    private.chmod(0o600)
    plugin_config = root / "plugins/MinecraftAICity/config.yml"
    plugin_config.write_text(
        f"backend-url: http://127.0.0.1:8765\nbackend-token: '{token}'\n"
        f"server-id: '{uuid.uuid4()}'\nblocks-per-tick: 32\nmillis-per-tick: 3\n"
        "max-changed-blocks: 5000\nmax-patch-volume: 262144\n"
        "protected-materials: [BEDROCK, CHEST, TRAPPED_CHEST, BARREL, HOPPER, SPAWNER, COMMAND_BLOCK]\n"
        "anchor:\n  configured: false\n",
        encoding="utf-8",
    )
    plugin_config.chmod(0o600)
    (root / "eula.txt").write_text("eula=" + str(accept_eula).lower() + "\n", encoding="utf-8")
    (root / "server.properties").write_text(
        "server-ip=127.0.0.1\nserver-port=25565\nonline-mode=true\nenable-rcon=false\n"
        "max-players=1\nview-distance=4\nsimulation-distance=3\nallow-nether=false\n"
        "level-type=minecraft:flat\ngenerate-structures=false\n"
        'generator-settings={"layers":[{"block":"minecraft:bedrock","height":1},'
        '{"block":"minecraft:dirt","height":2},{"block":"minecraft:grass_block","height":1}],'
        '"biome":"minecraft:plains","features":false,"lakes":false,"structure_overrides":[]}\n',
        encoding="utf-8",
    )
    (root / "bukkit.yml").write_text("settings:\n  allow-end: false\n", encoding="utf-8")
    # Launch scripts contain paths, not credentials. PowerShell quoting handles spaces/apostrophes.
    python = ps_quote(Path(sys.executable).resolve())
    args = (
        " -m minecraft_city web @ProjectArgs"
        f" --state-dir {ps_quote(root / 'backend')} --parser rules"
        f" --paper-config {ps_quote(private)}"
    )
    (root / "start-backend.ps1").write_text(
        "$ErrorActionPreference = 'Stop'\n$ProjectArgs = @()\n"
        + f"if (!(Test-Path {ps_quote(root / 'backend/session.json')})) {{ "
        + f"$ProjectArgs = @('--project', {ps_quote(project)}) }}\n& "
        + python
        + args
        + "\nexit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    (root / "start-paper.ps1").write_text(
        "$ErrorActionPreference = 'Stop'\nSet-Location $PSScriptRoot\n"
        "& java '-Xms512M' '-Xmx1536M' '-XX:ActiveProcessorCount=2' '-Dfile.encoding=UTF-8' "
        "'-jar' 'paper.jar' '--nogui'\nexit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    print(f"作成済み: {root}（認証情報は表示しません）")
    if not players:
        print(
            "Paper起動後 /city identity のUUIDをpaper-private.jsonのplayersへ追加し、Pythonを再起動してください。"
        )
    if not accept_eula:
        print(
            "https://aka.ms/MinecraftEULA を確認し、同意する場合だけeula.txtをeula=trueに変更してください。"
        )
    print("起動手順・都市の初回導入・anchor設定: docs/PAPER.md")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-dir", type=Path, default=Path("output/paper-local"))
    parser.add_argument("--download-cache", type=Path, default=Path("output/paper-downloads"))
    parser.add_argument("--project", type=Path, default=Path("examples/managed-city/project.json"))
    parser.add_argument(
        "--player", action="append", default=[], help="Allowed Minecraft player UUID"
    )
    parser.add_argument("--accept-eula", action="store_true")
    args = parser.parse_args()
    prepare(
        args.server_dir.resolve(),
        args.download_cache.resolve(),
        args.project.resolve(),
        args.player,
        args.accept_eula,
    )
