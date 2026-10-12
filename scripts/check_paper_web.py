"""Browser/Paper protocol hand-off check (world edits tested separately on Paper)."""

import argparse
import json
import secrets
import shutil
import tempfile
import threading
import uuid
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

from minecraft_city.paper import PaperCoordinator
from minecraft_city.web import CityServer
from minecraft_city.web_state import Workspace


def run(output, browser_name):
    output.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).resolve().parents[1] / "examples/managed-city/project.json"
    with tempfile.TemporaryDirectory(prefix="city-paper-web-") as temporary:
        root = Path(temporary)
        actor = str(uuid.uuid4())
        token = secrets.token_urlsafe(40)
        config = root / "paper-private.json"
        config.write_text(json.dumps({"token": token, "players": [actor]}))
        workspace = Workspace(root / "session", source, "rules")
        PaperCoordinator(workspace, config)
        original = workspace.data["revision"]
        binding = {
            "server_id": str(uuid.uuid4()),
            "world_uuid": str(uuid.uuid4()),
            "world_name": "world",
            "origin": [0, 80, 0],
            "rotation": 0,
        }
        try:
            with CityServer(workspace, port=0) as server:
                thread = threading.Thread(target=server.serve_forever)
                thread.start()
                base = f"http://127.0.0.1:{server.server_port}"

                def call(path, body):
                    response = requests.post(
                        base + "/api/paper/" + path,
                        json=body,
                        headers={"Authorization": "Bearer " + token, "X-City-Player": actor},
                        timeout=60,
                    )
                    response.raise_for_status()
                    return response.json()

                def simulate_plugin_finish(job):
                    call(
                        "prepare",
                        {
                            "job": job["id"],
                            "revision": job["before_revision"],
                            "approval": job["plan_id"],
                            "binding": binding,
                        },
                    )
                    call("progress", {"job": job["id"], "completed": job["changed_blocks"]})
                    call("finish", {"job": job["id"], "outcome": "commit"})

                try:
                    with sync_playwright() as playwright:
                        options = {"headless": True}
                        if browser_name == "chromium" and shutil.which("chromium"):
                            options["executable_path"] = shutil.which("chromium")
                        browser = getattr(playwright, browser_name).launch(**options)
                        context = browser.new_context(**playwright.devices["iPhone 13"])
                        page = context.new_page()
                        page.set_default_timeout(40000)
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.goto(base + "/#token=" + server.token)
                        page.wait_for_function(
                            "() => !document.querySelector('#plan-button').disabled"
                        )
                        page.locator("#instruction").fill("中央区の空き地にコンビニを作って")
                        page.locator("#plan-button").click()
                        page.locator("#review").wait_for(state="visible")
                        page.wait_for_function("() => !document.querySelector('#approve').disabled")
                        review = workspace.data["reviews"][-1]
                        page.locator("#approve").click()
                        assert f"/city review {review}" in page.locator("#messages").inner_text()
                        assert workspace.data["revision"] == original
                        page.screenshot(
                            path=str(output / "mobile-paper-review.png"), full_page=True
                        )
                        job = call("adopt", {"review": review, "binding": binding})
                        saved = json.loads((workspace.directory(review) / "plan.json").read_text())
                        assert job["plan_id"] == saved["plan_id"]
                        simulate_plugin_finish(job)
                        page.wait_for_function(
                            "() => document.querySelector('#city-stats').textContent.includes('52棟')"
                        )
                        assert page.locator("#review").is_hidden()
                        page.screenshot(
                            path=str(output / "mobile-paper-applied.png"), full_page=True
                        )
                        page.locator("#undo").click()
                        assert "/city undo" in page.locator("#messages").inner_text()
                        assert not page.locator("#undo-confirm").is_visible()
                        simulate_plugin_finish(call("undo-plan", binding))
                        page.wait_for_function(
                            "() => document.querySelector('#city-stats').textContent.includes('51棟')"
                        )
                        assert workspace.data["revision"] == original
                        assert not errors, errors
                        browser.close()
                        report = {
                            "browser": browser_name,
                            "web_requires_game_approval": True,
                            "same_plan_id": True,
                            "automatic_map_refresh": True,
                            "undo_requires_game_approval": True,
                            "python_state_restored": True,
                            "world_operation": "simulated protocol here; real Paper tested separately",
                            "javascript_errors": errors,
                        }
                        (output / "verification.json").write_text(
                            json.dumps(report, indent=2) + "\n"
                        )
                        print(json.dumps(report))
                finally:
                    server.shutdown()
                    thread.join()
        finally:
            workspace.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--browser", choices=["chromium", "webkit"], default="chromium")
    args = parser.parse_args()
    run(args.output, args.browser)
