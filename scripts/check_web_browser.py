"""Optional real-browser integration check; no API key or Minecraft server required."""

import argparse
import hashlib
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
    with tempfile.TemporaryDirectory(prefix="city-web-browser-") as temporary:
        workspace = Workspace(Path(temporary) / "session", source, "rules")
        original_revision = workspace.data["revision"]
        original_schematic = workspace.artifact("current/city.schem").read_bytes()
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
                        context = browser.new_context(
                            **playwright.devices["iPhone 13"], accept_downloads=True
                        )
                        page = context.new_page()
                        page.set_default_timeout(30000)
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.goto(f"http://127.0.0.1:{server.server_port}/#token={server.token}")
                        page.wait_for_function(
                            "() => document.querySelector('#city-stats').textContent.includes('51棟')"
                        )
                        page.wait_for_function(
                            "() => !document.querySelector('#plan-button').disabled"
                        )
                        assert "token=" not in page.url
                        assert page.evaluate(
                            "document.documentElement.scrollWidth <= window.innerWidth"
                        )
                        page.locator('[data-building="0"]').tap()
                        assert "建物 0" in page.locator("#selected").inner_text()
                        page.locator("#clear").click()
                        page.locator("#ward").select_option("ward-1")
                        district = next(
                            d
                            for d in workspace.state()["districts"]
                            if d["ward_id"] == "ward-1" and d["polygon"]
                        )
                        page.locator("#district").select_option(district["id"])
                        assert district["name"] in page.locator("#selected").inner_text()
                        page.locator("#clear").click()
                        page.locator('[data-mode="point"]').click()
                        bounds = page.locator("#map").bounding_box()
                        page.touchscreen.tap(
                            bounds["x"] + bounds["width"] * 0.5,
                            bounds["y"] + bounds["height"] * 0.5,
                        )
                        assert "X " in page.locator("#selected").inner_text()
                        page.locator('[data-mode="rect"]').click()
                        page.mouse.move(bounds["x"] + 60, bounds["y"] + 80)
                        page.mouse.down()
                        page.mouse.move(bounds["x"] + 120, bounds["y"] + 130, steps=5)
                        page.mouse.up()
                        assert "範囲" in page.locator("#selected").inner_text()
                        page.locator("#clear").click()
                        page.locator('[data-mode="select"]').click()
                        old_view = page.locator("#map").get_attribute("viewBox")
                        page.locator("#zoom-in").click()
                        assert page.locator("#map").get_attribute("viewBox") != old_view
                        page.locator("#fit").click()
                        pinch_checked = False
                        if browser_name == "chromium":
                            cdp = context.new_cdp_session(page)
                            cx, cy = (
                                bounds["x"] + bounds["width"] / 2,
                                bounds["y"] + bounds["height"] / 2,
                            )
                            for event, distance in [
                                ("touchStart", 30),
                                ("touchMove", 35),
                                ("touchMove", 70),
                            ]:
                                cdp.send(
                                    "Input.dispatchTouchEvent",
                                    {
                                        "type": event,
                                        "touchPoints": [
                                            {"x": cx - distance, "y": cy, "id": 1},
                                            {"x": cx + distance, "y": cy, "id": 2},
                                        ],
                                    },
                                )
                            cdp.send(
                                "Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []}
                            )
                            assert page.locator("#map").get_attribute("viewBox") != old_view
                            pinch_checked = True
                            page.locator("#fit").click()
                        page.screenshot(path=str(output / "mobile-map.png"), full_page=True)
                        page.locator("#instruction").fill("中央区の空き地にコンビニを作って")
                        page.locator("#plan-button").click()
                        page.locator("#review").wait_for(state="visible")
                        page.wait_for_function("() => !document.querySelector('#approve').disabled")
                        assert workspace.data["revision"] == original_revision
                        assert page.locator(".proposal-mark").count() == 1
                        page.locator("#focus-change").click()
                        assert page.locator("#map").get_attribute("viewBox") != old_view
                        page.locator("#fit").click()
                        page.screenshot(path=str(output / "mobile-review.png"), full_page=True)
                        page.locator("#approve").click()
                        page.wait_for_function(
                            "() => document.querySelector('#city-stats').textContent.includes('52棟')"
                        )
                        page.wait_for_function("() => !document.querySelector('#undo').disabled")
                        assert len(workspace.project.city.buildings) == 52
                        with page.expect_download() as event:
                            page.locator('[data-download="city.schem"]').click()
                        event.value.save_as(output / "edited-city.schem")
                        with page.expect_download() as event:
                            page.locator('[data-download="project.json"]').click()
                        event.value.save_as(output / "edited-project.json")
                        assert (output / "edited-city.schem").read_bytes() != original_schematic
                        page.locator("#undo").click()
                        page.locator("#undo-approve").click()
                        page.wait_for_function(
                            "() => document.querySelector('#city-stats').textContent.includes('51棟')"
                        )
                        page.wait_for_function(
                            "() => !document.querySelector('#plan-button').disabled"
                        )
                        assert workspace.data["revision"] == original_revision
                        assert (
                            workspace.artifact("current/city.schem").read_bytes()
                            == original_schematic
                        )
                        # Desktop uses the same session and engine.
                        desktop = browser.new_context(viewport={"width": 1440, "height": 1000})
                        second = desktop.new_page()
                        second.goto(f"http://127.0.0.1:{server.server_port}/#token={server.token}")
                        second.wait_for_function(
                            "() => !document.querySelector('#plan-button').disabled"
                        )
                        second.screenshot(path=str(output / "desktop-map.png"), full_page=True)
                        assert not errors, errors
                        browser.close()
                        report = {
                            "browser": browser_name,
                            "viewport": "iPhone 13 emulation (390x664)",
                            "building_and_district_selection": True,
                            "point_and_rectangle": True,
                            "pinch_zoom": pinch_checked,
                            "unapproved_unchanged": True,
                            "approved_buildings": 52,
                            "undo_buildings": 51,
                            "undo_schematic_byte_equal": True,
                            "javascript_errors": errors,
                            "download_sha256": hashlib.sha256(
                                (output / "edited-city.schem").read_bytes()
                            ).hexdigest(),
                        }
                        (output / "verification.json").write_text(
                            json.dumps(report, indent=2) + "\n", encoding="utf-8"
                        )
                        print(json.dumps(report))
                finally:
                    server.shutdown()
                    thread.join()
        finally:
            workspace.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--browser", choices=["chromium", "webkit"], default="chromium")
    args = parser.parse_args()
    run(args.output, args.browser)
