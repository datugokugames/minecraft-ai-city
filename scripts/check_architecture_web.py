"""Actual browser flow for Phase 5 exterior dimensions, preview, approval and undo."""

import argparse
import json
import shutil
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

from minecraft_city.web import CityServer
from minecraft_city.web_state import Workspace


def run(output, browser_name):
    output.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).resolve().parents[1] / "examples/managed-city/project.json"
    with tempfile.TemporaryDirectory(prefix="city-architecture-web-") as temp:
        workspace = Workspace(Path(temp) / "session", source, "rules")
        before = workspace.data["revision"]
        original_schem = workspace.artifact("current/city.schem").read_bytes()
        try:
            with CityServer(workspace, port=0) as server:
                thread = threading.Thread(target=server.serve_forever)
                thread.start()
                try:
                    with sync_playwright() as playwright:
                        options = {"headless": True}
                        if browser_name == "chromium" and shutil.which("chromium"):
                            options["executable_path"] = shutil.which("chromium")
                        browser = getattr(playwright, browser_name).launch(**options)
                        page = browser.new_page(
                            **playwright.devices["iPhone 13"], accept_downloads=True
                        )
                        page.set_default_timeout(60000)
                        errors = []
                        page.on("pageerror", lambda e: errors.append(str(e)))
                        page.goto(f"http://127.0.0.1:{server.server_port}/#token={server.token}")
                        page.wait_for_function(
                            "() => !document.querySelector('#plan-button').disabled"
                        )
                        page.locator('[data-mode="point"]').click()
                        bbox = page.locator("#map").bounding_box()
                        page.touchscreen.tap(
                            bbox["x"] + bbox["width"] * 64.5 / 256,
                            bbox["y"] + bbox["height"] * 20.5 / 256,
                        )
                        assert "X " in page.locator("#selected").inner_text()
                        page.locator(".architecture-settings summary").click()
                        page.locator("#arch-category").select_option("apartment")
                        assert page.locator("#arch-width").input_value() == "30"
                        assert page.locator("#arch-depth").input_value() == "40"
                        page.locator("#instruction").fill("この場所にマンションを建てて")
                        page.locator("#plan-button").click()
                        page.locator("#review").wait_for(state="visible")
                        page.wait_for_function("() => !document.querySelector('#approve').disabled")
                        assert workspace.data["revision"] == before
                        assert page.locator("#architecture-view").is_visible()
                        assert page.locator("#architecture-preview").evaluate(
                            "(img)=>img.naturalWidth>0"
                        )
                        assert (
                            "phase5-1" not in page.locator("#plan-details").inner_text()
                            or "architecture" in page.locator("#plan-details").inner_text()
                        )
                        page.screenshot(
                            path=str(output / "mobile-exterior-review.png"), full_page=True
                        )
                        page.locator("#approve").click()
                        page.wait_for_function(
                            "() => document.querySelector('#city-stats').textContent.includes('52棟')"
                        )
                        building = workspace.project.city.buildings[-1]
                        assert (
                            building.architecture.category == "apartment"
                            and building.architecture.site_width == 30
                            and building.architecture.site_depth == 40
                        )
                        with page.expect_download() as download:
                            page.locator('[data-download="city.schem"]').click()
                        download.value.save_as(output / "edited-city.schem")
                        page.locator("#undo").click()
                        page.locator("#undo-approve").click()
                        page.wait_for_function(
                            "() => document.querySelector('#city-stats').textContent.includes('51棟')"
                        )
                        assert workspace.data["revision"] == before
                        assert (
                            workspace.artifact("current/city.schem").read_bytes() == original_schem
                        )
                        assert not errors, errors
                        browser.close()
                        report = {
                            "browser": browser_name,
                            "site": [30, 40],
                            "category": "apartment",
                            "mode": "local",
                            "point_selection": True,
                            "exterior_preview": True,
                            "unapproved_unchanged": True,
                            "approval_applied": True,
                            "undo_project_exact": True,
                            "undo_schematic_exact": True,
                            "javascript_errors": errors,
                            "minecraft_client": "not tested",
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
