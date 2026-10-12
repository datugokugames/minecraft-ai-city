import json
import uuid

import nbtlib
import pytest
import requests
from test_web import SOURCE

from minecraft_city.edit_models import digest
from minecraft_city.paper import PaperCoordinator, block_patch
from minecraft_city.web import CityServer
from minecraft_city.web_state import Conflict, Workspace


@pytest.fixture
def coordinator(tmp_path):
    workspace = Workspace(tmp_path / "session", SOURCE, "rules")
    actor = str(uuid.uuid4())
    config = tmp_path / "paper-config.json"
    config.write_text(json.dumps({"token": "test-token-" + "x" * 40, "players": [actor]}))
    paper = PaperCoordinator(workspace, config)
    binding = {
        "server_id": str(uuid.uuid4()),
        "world_uuid": str(uuid.uuid4()),
        "world_name": "world",
        "origin": [0, 80, 0],
        "rotation": 0,
    }
    yield paper, actor, binding
    workspace.close()


def plan(paper, actor, binding):
    return paper.plan(
        actor,
        {
            "revision": paper.workspace.data["revision"],
            "instruction": "中央区の空き地にコンビニを作って",
            "binding": binding,
        },
    )


def prepare(paper, actor, job, binding):
    return paper.prepare(
        actor,
        {
            "job": job["id"],
            "revision": job["before_revision"],
            "approval": job["plan_id"],
            "binding": binding,
        },
    )


def finish(paper, actor, job, outcome):
    return paper.finish(actor, {"job": job["id"], "outcome": outcome})


def test_two_phase_and_world_undo(coordinator):
    paper, actor, binding = coordinator
    before = digest(paper.workspace.project)
    proposal = plan(paper, actor, binding)
    job = proposal["job"]
    assert digest(paper.workspace.project) == before
    assert job["status"] == "planned" and 0 < job["changed_blocks"] <= 5000
    for name in ("before", "after"):
        schematic = nbtlib.load(paper.workspace.directory(job["id"]) / f"{name}.schem")
        assert int(schematic["Version"]) == 2
        bounds = job["bounds"]
        assert int(schematic["Width"]) == bounds[3] - bounds[0]
    prepare(paper, actor, job, binding)
    with pytest.raises(Conflict):
        finish(paper, actor, job, "commit")
    assert digest(paper.workspace.project) == before
    paper.api("POST", "progress", {"job": job["id"], "completed": job["changed_blocks"]}, actor)
    finish(paper, actor, job, "commit")
    assert len(paper.workspace.project.city.buildings) == 52
    assert finish(paper, actor, job, "commit")["job"]["status"] == "committed"
    reverse = paper.create(actor, binding, undo=True)
    assert reverse["changed_blocks"] == job["changed_blocks"]
    prepare(paper, actor, reverse, binding)
    paper.api(
        "POST", "progress", {"job": reverse["id"], "completed": reverse["changed_blocks"]}, actor
    )
    finish(paper, actor, reverse, "commit")
    assert digest(paper.workspace.project) == before


def test_failed_world_never_commits_python(coordinator):
    paper, actor, binding = coordinator
    before = digest(paper.workspace.project)
    job = plan(paper, actor, binding)["job"]
    prepare(paper, actor, job, binding)
    paper.api("POST", "progress", {"job": job["id"], "completed": 1}, actor)
    finish(paper, actor, job, "rollback")
    assert job["status"] == "rolled_back" and digest(paper.workspace.project) == before
    assert paper.data["active"] is None


def test_double_approval_stale_and_conflicting_jobs(coordinator):
    paper, actor, binding = coordinator
    job = plan(paper, actor, binding)["job"]
    second = plan(paper, actor, binding)["job"]
    prepare(paper, actor, job, binding)
    for value in (job, second):
        with pytest.raises(Conflict):
            prepare(paper, actor, value, binding)
    with pytest.raises(Conflict):
        paper.workspace.plan(
            {"revision": job["before_revision"], "instruction": "中央区に住宅を追加して"}
        )
    with pytest.raises(Conflict):
        paper.workspace.apply(
            {
                "revision": job["before_revision"],
                "review": job["review"],
                "approval": job["plan_id"],
            }
        )
    finish(paper, actor, job, "rollback")
    with pytest.raises(Conflict):
        paper.prepare(
            actor,
            {
                "job": second["id"],
                "revision": "0" * 64,
                "approval": second["plan_id"],
                "binding": binding,
            },
        )


def test_owner_binding_and_limits(coordinator, tmp_path):
    paper, actor, binding = coordinator
    job = plan(paper, actor, binding)["job"]
    with pytest.raises(PermissionError):
        paper.get_job(job["id"], str(uuid.uuid4()))
    with pytest.raises(PermissionError):
        paper.actor(str(uuid.uuid4()))
    with pytest.raises(Conflict):
        paper.binding({**binding, "origin": [1, 80, 0]})
    from minecraft_city.editing import load_plan, verify_plan

    after = verify_plan(
        paper.workspace.project, load_plan(paper.workspace.directory(job["review"]) / "plan.json")
    )
    with pytest.raises(ValueError, match="変更ブロック数"):
        block_patch(paper.workspace.project, after, tmp_path / "too-big", max_blocks=1)
    assert not (tmp_path / "too-big").exists()
    with pytest.raises(ValueError, match="包絡領域"):
        block_patch(paper.workspace.project, after, tmp_path / "too-wide", max_volume=1)


def test_persisted_active_job_and_lost_finish_reply(coordinator):
    paper, actor, binding = coordinator
    job = plan(paper, actor, binding)["job"]
    prepare(paper, actor, job, binding)
    restarted = PaperCoordinator(paper.workspace, paper.workspace.root.parent / "paper-config.json")
    assert restarted.data["active"] == job["id"]
    with pytest.raises(Conflict):
        restarted.guard()
    # Simulate process death after the shared manifest write, before paper-state write.
    restarted.committing = True
    restarted.workspace.apply(
        {"revision": job["before_revision"], "review": job["review"], "approval": job["plan_id"]}
    )
    repaired = restarted.get_job(job["id"], actor)
    assert repaired["status"] == "committed" and restarted.data["active"] is None
    assert restarted.create(actor, binding, undo=True)["action"] == "undo"


def test_web_plan_adopt_same_id_and_unsupported_station(coordinator):
    paper, actor, binding = coordinator
    web = paper.workspace.plan(
        {"revision": paper.workspace.data["revision"], "instruction": "中央区にコンビニを作って"}
    )
    job = paper.api("POST", "adopt", {"review": web["review"], "binding": binding}, actor)
    assert job["plan_id"] == web["plan"]["plan_id"]
    with pytest.raises(ValueError):
        paper.plan(
            actor,
            {
                "revision": paper.workspace.data["revision"],
                "instruction": "中央区に駅を作って",
                "binding": binding,
            },
        )


def test_paper_http_auth_and_job_files(coordinator):
    import threading

    paper, actor, binding = coordinator
    with CityServer(paper.workspace, port=0) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}/api/paper/"
            headers = {"Authorization": "Bearer " + paper.config.token, "X-City-Player": actor}
            assert requests.get(base + "state", timeout=10).status_code == 401
            assert (
                requests.get(
                    base + "state",
                    headers={**headers, "X-City-Player": str(uuid.uuid4())},
                    timeout=10,
                ).status_code
                == 403
            )
            job = plan(paper, actor, binding)["job"]
            assert (
                requests.get(base + "jobs/" + job["id"], headers=headers, timeout=10).json()[
                    "plan_id"
                ]
                == job["plan_id"]
            )
            assert (
                requests.get(
                    base + "jobs/" + job["id"] + "/before.schem", headers=headers, timeout=10
                ).status_code
                == 200
            )
            assert (
                requests.get(
                    base + "jobs/" + job["id"] + "/../paper-state.json", headers=headers, timeout=10
                ).status_code
                == 404
            )
        finally:
            server.shutdown()
            thread.join()


def test_repeated_apply_undo_keeps_unique_history(coordinator):
    paper, actor, binding = coordinator
    original = digest(paper.workspace.project)
    for _ in range(2):
        applied = plan(paper, actor, binding)["job"]
        for job in (applied,):
            prepare(paper, actor, job, binding)
            paper.api(
                "POST", "progress", {"job": job["id"], "completed": job["changed_blocks"]}, actor
            )
            finish(paper, actor, job, "commit")
        reverse = paper.create(actor, binding, undo=True)
        assert reverse["undo_of"] == applied["id"]
        prepare(paper, actor, reverse, binding)
        paper.api(
            "POST",
            "progress",
            {"job": reverse["id"], "completed": reverse["changed_blocks"]},
            actor,
        )
        finish(paper, actor, reverse, "commit")
        assert applied["status"] == "undone"
        assert digest(paper.workspace.project) == original


def test_cancel_rejects_approval_and_patch_symlinks(coordinator, tmp_path):
    paper, actor, binding = coordinator
    job = plan(paper, actor, binding)["job"]
    paper.api("POST", "cancel", {"job": job["id"]}, actor)
    with pytest.raises(Conflict):
        prepare(paper, actor, job, binding)
    patch = paper.workspace.directory(job["id"]) / "before.schem"
    patch.unlink()
    outside = tmp_path / "private.txt"
    outside.write_text("must not be served")
    patch.symlink_to(outside)
    with pytest.raises(FileNotFoundError):
        paper.api("GET", f"jobs/{job['id']}/before.schem", None, actor)
