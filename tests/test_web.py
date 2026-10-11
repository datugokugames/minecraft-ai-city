import hashlib
import json
import threading
from pathlib import Path

import pytest
import requests

from minecraft_city.edit_models import digest
from minecraft_city.project import load_project
from minecraft_city.web import CityServer, validate_bind
from minecraft_city.web_state import Workspace

SOURCE = Path(__file__).parents[1] / "examples/managed-city/project.json"


@pytest.fixture
def workspace(tmp_path):
    value = Workspace(tmp_path / "session", SOURCE, parser="rules")
    yield value
    value.close()


@pytest.fixture
def server(workspace):
    with CityServer(workspace, port=0) as http:
        thread = threading.Thread(target=http.serve_forever)
        thread.start()
        yield (
            http,
            f"http://127.0.0.1:{http.server_port}",
            {"Authorization": "Bearer " + http.token},
        )
        http.shutdown()
        thread.join()


def test_http_complete_edit_and_undo(server, workspace):
    http, base, headers = server
    get = lambda path: requests.get(base + path, headers=headers, timeout=15)
    post = lambda path, value: requests.post(base + path, json=value, headers=headers, timeout=30)
    before = get("/api/state").json()
    original = get("/api/files/current/project.json").content
    schematic = get("/api/files/current/city.schem").content
    assert get("/").status_code == 200
    assert get("/api/files/current/map.png").headers["Content-Type"] == "image/png"
    assert len(before["buildings"]) == 51 and len(before["districts"]) == 7
    proposal = post(
        "/api/plan",
        {"revision": before["revision"], "instruction": "中央区の空き地にコンビニを作って"},
    )
    assert proposal.status_code == 200, proposal.text
    proposal = proposal.json()
    assert get("/api/files/current/project.json").content == original
    for filename in ["before.png", "after.png", "plan.json", "diff.json", "validation.json"]:
        assert get(f"/api/files/reviews/{proposal['review']}/{filename}").status_code == 200
    request = {
        "revision": before["revision"],
        "review": proposal["review"],
        "approval": proposal["plan"]["plan_id"],
    }
    assert post("/api/apply", {**request, "approval": "0" * 64}).status_code == 422
    assert get("/api/files/current/project.json").content == original
    applied = post("/api/apply", request)
    assert applied.status_code == 200, applied.text
    applied = applied.json()
    assert len(applied["buildings"]) == 52 and applied["history_depth"] == 1
    assert post("/api/apply", request).status_code == 409
    updated = get("/api/files/current/city.schem")
    assert updated.content != schematic and updated.headers["Content-Disposition"].endswith(
        '"city.schem"'
    )
    undo = post("/api/undo", {"revision": applied["revision"], "approval": applied["undo"]})
    assert undo.status_code == 200, undo.text
    assert undo.json()["revision"] == before["revision"]
    assert get("/api/files/current/city.schem").content == schematic
    assert digest(workspace.project) == digest(load_project(SOURCE))
    assert len(list(workspace.root.glob("*/history.json"))) == 2
    assert http.token not in (workspace.root / "session.json").read_text()


@pytest.mark.parametrize(
    "path", ["/api/state", "/api/files/current/project.json", "/api/files/current/city.schem"]
)
def test_data_requires_token(server, path):
    _, base, _ = server
    response = requests.get(base + path, timeout=10)
    assert response.status_code == 401
    assert "Access-Control-Allow-Origin" not in response.headers


def test_http_boundaries(server, workspace):
    _, base, headers = server
    for extra in [{"Host": "evil.test"}, {"Origin": "https://evil.test"}]:
        assert (
            requests.get(base + "/api/state", headers={**headers, **extra}, timeout=10).status_code
            == 403
        )
    for path in [
        "/api/files/current/server.lock",
        "/api/files/current/../../../etc/passwd",
        "/api/files/reviews/ffffffffffffffffffffffffffffffff/plan.json",
        "/api/files/current/%2e%2e%2fproject.json",
    ]:
        assert requests.get(base + path, headers=headers, timeout=10).status_code == 404
    response = requests.post(
        base + "/api/plan",
        data="x" * 17000,
        headers={**headers, "Content-Type": "application/json"},
        timeout=10,
    )
    assert response.status_code == 413
    assert (
        requests.post(base + "/api/plan", data="{}", headers=headers, timeout=10).status_code == 415
    )
    response = requests.post(
        base + "/api/plan",
        json={
            "revision": workspace.data["revision"],
            "instruction": "ここにコンビニを作って",
            "path": "/etc/passwd",
        },
        headers=headers,
        timeout=10,
    )
    assert response.status_code == 422
    with workspace.lock:
        assert requests.get(base + "/api/state", headers=headers, timeout=10).status_code == 409


def test_resume_history_lock_and_stale_tabs(workspace):
    original = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):
        Workspace(workspace.root)
    proposal = workspace.plan(
        {"revision": workspace.data["revision"], "instruction": "中央区にコンビニを作って"}
    )
    workspace.apply(
        {
            "revision": workspace.data["revision"],
            "review": proposal["review"],
            "approval": proposal["plan"]["plan_id"],
        }
    )
    root = workspace.root
    workspace.close()
    resumed = Workspace(root, parser="rules")
    try:
        assert len(resumed.project.city.buildings) == 52
        state = resumed.state()
        resumed.undo({"revision": state["revision"], "approval": state["undo"]})
        assert digest(resumed.project) == digest(load_project(SOURCE))
    finally:
        resumed.close()
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == original


def test_failed_state_commit_keeps_current(workspace, monkeypatch):
    before = workspace.state()
    proposal = workspace.plan(
        {"revision": before["revision"], "instruction": "中央区にコンビニを作って"}
    )
    monkeypatch.setattr(
        workspace, "save", lambda value: (_ for _ in ()).throw(OSError("full disk"))
    )
    with pytest.raises(OSError):
        workspace.apply(
            {
                "revision": before["revision"],
                "review": proposal["review"],
                "approval": proposal["plan"]["plan_id"],
            }
        )
    assert workspace.state()["revision"] == before["revision"]
    manifest = json.loads((workspace.root / "session.json").read_text())
    assert manifest["revision"] == before["revision"]


@pytest.mark.parametrize(
    "host,lan,allowed",
    [
        ("127.0.0.1", False, True),
        ("192.168.1.2", True, True),
        ("192.168.1.2", False, False),
        ("0.0.0.0", True, False),
        ("8.8.8.8", True, False),
        ("::", True, False),
    ],
)
def test_private_bind_only(host, lan, allowed):
    if allowed:
        validate_bind(host, lan)
    else:
        with pytest.raises(ValueError):
            validate_bind(host, lan)
