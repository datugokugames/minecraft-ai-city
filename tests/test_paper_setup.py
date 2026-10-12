"""Setup safety without network downloads or accepting the EULA."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("city_setup", SCRIPTS / "setup_paper.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)
sys.path.pop(0)


def test_setup_never_overwrites_and_starts_private(tmp_path, monkeypatch, capsys):
    fake_repo = tmp_path / "repo"
    plugin = fake_repo / "paper/target/minecraft-ai-city-paper-0.4.0.jar"
    plugin.parent.mkdir(parents=True)
    plugin.write_bytes(b"test fixture")
    monkeypatch.setattr(setup, "__file__", str(fake_repo / "scripts/setup_paper.py"))
    monkeypatch.setattr(setup, "load_project", lambda project: None)
    monkeypatch.setattr(setup, "server_files", lambda root, cache: None)
    root = tmp_path / "server with ' quote"
    setup.prepare(root, tmp_path / "cache", tmp_path / "project.json", [])
    credentials = json.loads((root / "paper-private.json").read_text())
    assert len(credentials["token"]) >= 32 and credentials["players"] == []
    assert credentials["token"] not in capsys.readouterr().out
    assert (root / "eula.txt").read_text() == "eula=false\n"
    properties = (root / "server.properties").read_text()
    assert "server-ip=127.0.0.1" in properties and "online-mode=true" in properties
    startup = (root / "start-backend.ps1").read_text()
    assert (
        "Test-Path" in startup and "@ProjectArgs" in startup and "server with '' quote" in startup
    )
    sentinel = root / "keep.txt"
    sentinel.write_text("existing world")
    with pytest.raises(FileExistsError):
        setup.prepare(root, tmp_path / "cache", tmp_path / "project.json", [])
    assert sentinel.read_text() == "existing world"
    assert json.loads((root / "paper-private.json").read_text()) == credentials
