"""Disposable Paper + WorldEdit + real plugin integration, including restart recovery.

Requires Java 21 and both Maven-built jars. Never points at an existing world.
The fake operator is provided by a separate, console-only integration plugin.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import secrets
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import requests

from minecraft_city.edit_models import digest
from minecraft_city.paper import PaperCoordinator
from minecraft_city.project import load_project
from minecraft_city.web import CityServer
from minecraft_city.web_state import Workspace

PAPER_BUILD = 232
PAPER_SHA256 = "5ee4f542f628a14c644410b08c94ea42e772ef4d29fe92973636b6813d4eaffc"


def download(url, target, checksum, algorithm="sha256"):
    target = Path(target)
    if target.exists() and hashlib.new(algorithm, target.read_bytes()).hexdigest() == checksum:
        return
    response = requests.get(url, timeout=(10, 120))
    response.raise_for_status()
    if hashlib.new(algorithm, response.content).hexdigest() != checksum:
        raise ValueError("Download checksum mismatch")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(response.content)


def server_files(root, cache):
    cache.mkdir(parents=True, exist_ok=True)
    builds = requests.get(
        "https://fill.papermc.io/v3/projects/paper/versions/1.21.4/builds",
        headers={"User-Agent": "minecraft-ai-city-development/0.4.0 (integration testing)"},
        timeout=30,
    )
    builds.raise_for_status()
    app = next(b for b in builds.json() if b["id"] == PAPER_BUILD)["downloads"]["server:default"]
    if app["checksums"]["sha256"] != PAPER_SHA256:
        raise ValueError("Pinned Paper build checksum changed")
    download(app["url"], cache / "paper.jar", PAPER_SHA256)
    versions = requests.get(
        "https://api.modrinth.com/v2/project/worldedit/version",
        params={
            "game_versions": json.dumps(["1.21.4"]),
            "loaders": json.dumps(["paper", "bukkit"]),
        },
        timeout=30,
    )
    versions.raise_for_status()
    version = next(v for v in versions.json() if v["version_number"] == "7.3.10")
    file = next(f for f in version["files"] if f["primary"])
    download(file["url"], cache / "worldedit.jar", file["hashes"]["sha512"], "sha512")
    shutil.copy2(cache / "paper.jar", root / "paper.jar")
    shutil.copy2(cache / "worldedit.jar", root / "plugins/WorldEdit.jar")
    with zipfile.ZipFile(cache / "paper.jar") as jar:
        sha, url, filename = jar.read("META-INF/download-context").decode().strip().split("\t")
    if Path(filename).name != filename:
        raise ValueError("Invalid Mojang cache path")
    download(url, cache / "cache" / filename, sha)
    (root / "cache").mkdir(exist_ok=True)
    shutil.copy2(cache / "cache" / filename, root / "cache" / filename)


class Process:
    def __init__(self, root, number):
        flags = [
            "java",
            "-Xms256M",
            "-Xmx1024M",
            "-XX:ActiveProcessorCount=2",
            "-Dfile.encoding=UTF-8",
        ]
        # Preserve the managed session's proxy and CA, while localhost bypasses it.
        proxy = urlsplit(os.environ.get("HTTPS_PROXY", os.environ.get("HTTP_PROXY", "")))
        if proxy.hostname:
            for protocol in ("http", "https"):
                flags += [
                    f"-D{protocol}.proxyHost={proxy.hostname}",
                    f"-D{protocol}.proxyPort={proxy.port or 8080}",
                ]
            flags += ["-Dhttp.nonProxyHosts=localhost|127.*|[::1]"]
            ca = Path("/etc/ssl/certs/java/cacerts")
            if ca.exists():
                flags += ["-Djavax.net.ssl.trustStore=" + str(ca)]
        self.process = subprocess.Popen(
            [*flags, "-jar", "paper.jar", "--nogui"],
            cwd=root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self.lines = queue.Queue()
        self.log = root / f"server-{number}.log"
        self.thread = threading.Thread(target=self.capture, daemon=True)
        self.thread.start()

    def capture(self):
        with self.log.open("w", encoding="utf-8") as stream:
            for line in self.process.stdout:
                stream.write(line)
                stream.flush()
                self.lines.put(line)

    def wait(self, text, seconds=240):
        end = time.monotonic() + seconds
        recent = []
        while time.monotonic() < end:
            try:
                line = self.lines.get(timeout=1)
                recent.append(line)
                recent = recent[-25:]
                if "CITY_INTEGRATION_FAILURE" in line:
                    raise RuntimeError(line)
                if text in line:
                    return
            except queue.Empty:
                if self.process.poll() is not None:
                    break
        raise RuntimeError("Paper wait failed: " + text + "\n" + "".join(recent))

    def command(self, text):
        self.process.stdin.write(text + "\n")
        self.process.stdin.flush()

    def stop(self):
        if self.process.poll() is None:
            self.command("stop")
            self.process.wait(timeout=120)
        self.thread.join(timeout=10)


def run(root, cache, accept_eula):
    if not accept_eula:
        raise ValueError(
            "Minecraft EULA https://aka.ms/MinecraftEULA を確認し --accept-eula を指定してください。"
        )
    repo = Path(__file__).resolve().parents[1]
    plugin = repo / "paper/target/minecraft-ai-city-paper-0.4.0.jar"
    harness = repo / "paper/integration/target/city-integration-harness-0.4.0.jar"
    if not plugin.exists() or not harness.exists():
        raise ValueError("先にpaperとpaper/integrationをMavenでビルドしてください。")
    root.mkdir(parents=True, exist_ok=False)
    (root / "plugins").mkdir()
    server_files(root, cache)
    shutil.copy2(plugin, root / "plugins/MinecraftAICity.jar")
    shutil.copy2(harness, root / "plugins/CityIntegrationHarness.jar")
    (root / "eula.txt").write_text("eula=true\n")
    (root / "server.properties").write_text(
        'server-ip=127.0.0.1\nserver-port=25585\nonline-mode=false\nlevel-type=minecraft:flat\ngenerator-settings={"layers":[{"block":"minecraft:bedrock","height":1},{"block":"minecraft:dirt","height":2},{"block":"minecraft:grass_block","height":1}],"biome":"minecraft:plains","features":false,"lakes":false,"structure_overrides":[]}\ngenerate-structures=false\nallow-nether=false\nview-distance=3\nsimulation-distance=3\nspawn-protection=0\nspawn-monsters=false\nspawn-animals=false\nenable-rcon=false\nmax-players=1\n',
        encoding="utf-8",
    )
    (root / "bukkit.yml").write_text(
        "settings:\n  allow-end: false\nspawn-limits:\n  monsters: 0\n  animals: 0\n",
        encoding="utf-8",
    )
    source = repo / "examples/managed-city/project.json"
    original = digest(load_project(source))
    token = secrets.token_urlsafe(40)
    actor = str(uuid.uuid4())
    config = root / "paper-private.json"
    config.write_text(json.dumps({"token": token, "players": [actor]}), encoding="utf-8")
    workspace = Workspace(root / "backend", source, "rules")
    paper = PaperCoordinator(workspace, config)
    process = None
    try:
        with CityServer(workspace, port=0) as server:
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                settings = root / "plugins/MinecraftAICity"
                settings.mkdir()
                (settings / "config.yml").write_text(
                    f"backend-url: http://127.0.0.1:{server.server_port}\nbackend-token: '{token}'\nserver-id: '{uuid.uuid4()}'\nblocks-per-tick: 64\nmillis-per-tick: 4\nmax-changed-blocks: 5000\nmax-patch-volume: 262144\nprotected-materials: [BEDROCK, CHEST, HOPPER, SPAWNER, COMMAND_BLOCK]\ntest-player-uuid: '{actor}'\n",
                    encoding="utf-8",
                )
                print("Starting disposable Paper 1.21.4 / WorldEdit 7.3.10", flush=True)
                process = Process(root, 1)
                process.wait("Done (")
                process.command("city")
                process.command("citytest run")
                process.wait("Stopping server", seconds=300)
                process.process.wait(timeout=120)
                process.thread.join(timeout=10)
                report_path = root / "plugins/CityIntegrationHarness/report.json"
                first = json.loads(report_path.read_text())
                if not first.get("phase_one"):
                    raise RuntimeError("First phase did not complete: " + json.dumps(first))
                if not paper.data["active"]:
                    raise RuntimeError("No durable active job survived the deliberate stop")
                print(
                    "World build / undo / collision / veto rollback passed; testing restart recovery",
                    flush=True,
                )
                process = Process(root, 2)
                process.wait("Done (")
                process.command("citytest recover")
                process.wait("CITY_INTEGRATION_SUCCESS")
                process.stop()
                second = json.loads(report_path.read_text())
                assert second["success"] and second["restart_world_recovery"]
                assert digest(workspace.project) == original
                assert paper.data["active"] is None
                result = {
                    **first,
                    **second,
                    "python_project_restored": True,
                    "source_project_preserved": digest(load_project(source)) == original,
                    "paper_build": PAPER_BUILD,
                    "paper_jar_sha256": PAPER_SHA256,
                    "plugin_jar_sha256": hashlib.sha256(plugin.read_bytes()).hexdigest(),
                    "harness_jar_sha256": hashlib.sha256(harness.read_bytes()).hexdigest(),
                    "minecraft_client": "not tested; console-only synthetic operator",
                    "eula": "accepted explicitly for this disposable test",
                }
                (root / "verification.json").write_text(
                    json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                print(json.dumps(result, ensure_ascii=False, indent=2))
            finally:
                if process:
                    process.stop()
                server.shutdown()
                thread.join()
    finally:
        workspace.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-dir", type=Path, required=True)
    parser.add_argument("--download-cache", type=Path, default=Path("output/paper-downloads"))
    parser.add_argument("--accept-eula", action="store_true")
    args = parser.parse_args()
    run(args.server_dir.resolve(), args.download_cache.resolve(), args.accept_eula)
