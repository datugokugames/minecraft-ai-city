"""Single-project workspace. All construction delegates to the Phase 3A engine."""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from pathlib import Path

from PIL import Image
from pydantic import Field

from .edit_models import Selection, digest
from .editing import apply_edit, load_plan, prepare_edit, undo_edit, write_preview
from .exporting import export_city
from .generation import building_area
from .models import StrictModel
from .project import load_project, save_project


class Conflict(ValueError):
    pass


class PlanRequest(StrictModel):
    revision: str
    instruction: str = Field(min_length=1, max_length=2000)
    selection: Selection = Field(default_factory=Selection)
    seed: int = Field(default=42, ge=0, le=2**63 - 1)


class ApplyRequest(StrictModel):
    revision: str
    review: str = Field(pattern=r"^[0-9a-f]{32}$")
    approval: str = Field(pattern=r"^[0-9a-f]{64}$")


class UndoRequest(StrictModel):
    revision: str
    approval: str = Field(pattern=r"^[0-9a-f]{64}$")


class Workspace:
    def __init__(self, root, project_path=None, parser="auto"):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.parser = parser
        self.lockfile = self.root / "server.lock"
        # A second process must never race the atomic state pointer.
        self.fd = os.open(self.lockfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(self.fd, str(os.getpid()).encode())
        try:
            manifest = self.root / "session.json"
            if manifest.exists():
                if project_path is not None:
                    raise ValueError("既存セッションでは --project を省略してください。")
                self.data = json.loads(manifest.read_text(encoding="utf-8"))
                self.project = load_project(self.directory(self.data["current"]) / "project.json")
                if self.data["revision"] != digest(self.project):
                    raise ValueError("保存済みセッションのprojectが変更されています。")
            else:
                if project_path is None:
                    raise ValueError("新規セッションには --project が必要です。")
                self.project = load_project(project_path)
                name = uuid.uuid4().hex
                out = self.root / name
                out.mkdir()
                export_city(self.project.city.model_copy(deep=True), out)
                save_project(out / "project.json", self.project)
                self.map_image(out, self.project)
                self.data = {
                    "current": name,
                    "revision": digest(self.project),
                    "stack": [],
                    "reviews": [],
                }
                self.save(self.data)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
            self.lockfile.unlink(missing_ok=True)

    def directory(self, name):
        if not isinstance(name, str) or not re.fullmatch(r"[0-9a-f]{32}", name):
            raise ValueError("Invalid snapshot identifier")
        target = (self.root / name).resolve()
        if target.parent != self.root:
            raise ValueError("Snapshot outside session")
        return target

    def save(self, data):
        temporary = self.root / "session.tmp"
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.root / "session.json")
        self.data = data

    @staticmethod
    def map_image(out, project):
        top = 90 if project.city.layout == "organic" else 92
        size = project.city.size * 4
        with Image.open(out / "preview.png") as image:
            image.crop((32, top, 32 + size, top + size)).save(out / "map.png")

    def check_revision(self, revision):
        if revision != self.data["revision"]:
            raise Conflict("都市が更新されています。地図を再読み込みして計画を作り直してください。")

    def state(self):
        p = self.project
        physical = {d.id: d for d in p.city.districts}
        return {
            "city_name": p.city_name,
            "size": p.city.size,
            "revision": self.data["revision"],
            "wards": [w.model_dump() for w in p.wards],
            "districts": [
                {
                    **d.model_dump(),
                    "polygon": physical[d.city_district_id].polygon
                    if d.city_district_id in physical
                    else [],
                }
                for d in p.districts
            ],
            "buildings": [
                {
                    "id": b.id,
                    "kind": b.detail or b.kind,
                    "district_id": b.district_id,
                    "floors": b.floors,
                    "polygon": list(building_area(b).exterior.coords),
                }
                for b in p.city.buildings
            ],
            "undo": self.data["stack"][-1]["plan_id"] if self.data["stack"] else None,
            "history_depth": len(self.data["stack"]),
            "parser": self.parser,
            "attribution": "© OpenStreetMap contributors / ODbL · 合成データの出典はダウンロードしたproject.jsonを参照",
        }

    def plan(self, value):
        req = PlanRequest.model_validate(value)
        self.check_revision(req.revision)
        plan, _ = prepare_edit(self.project, req.instruction, req.selection, req.seed, self.parser)
        name = uuid.uuid4().hex
        write_preview(self.project, plan, self.directory(name))
        self.save({**self.data, "reviews": [*self.data["reviews"][-19:], name]})
        return {"review": name, "plan": plan.model_dump(mode="json")}

    def apply(self, value):
        req = ApplyRequest.model_validate(value)
        self.check_revision(req.revision)
        if req.review not in self.data["reviews"]:
            raise Conflict("計画が見つかりません。計画を作り直してください。")
        plan = load_plan(self.directory(req.review) / "plan.json")
        name = uuid.uuid4().hex
        after = apply_edit(self.project, plan, req.approval, self.directory(name))
        self.map_image(self.directory(name), after)
        data = {
            **self.data,
            "current": name,
            "revision": digest(after),
            "reviews": [],
            "stack": [*self.data["stack"], {"snapshot": name, "plan_id": plan.plan_id}],
        }
        self.save(data)
        self.project = after
        return self.state()

    def undo(self, value):
        req = UndoRequest.model_validate(value)
        self.check_revision(req.revision)
        if not self.data["stack"]:
            raise Conflict("取り消せる編集がありません。")
        previous = self.data["stack"][-1]
        name = uuid.uuid4().hex
        after = undo_edit(
            self.project,
            self.directory(previous["snapshot"]) / "history.json",
            req.approval,
            self.directory(name),
        )
        self.map_image(self.directory(name), after)
        self.save(
            {
                **self.data,
                "current": name,
                "revision": digest(after),
                "reviews": [],
                "stack": self.data["stack"][:-1],
            }
        )
        self.project = after
        return self.state()

    def artifact(self, route):
        parts = route.split("/")
        if (
            len(parts) == 2
            and parts[0] == "current"
            and parts[1] in {"project.json", "city.schem", "preview.png", "map.png"}
        ):
            directory, filename = self.directory(self.data["current"]), parts[1]
        elif (
            len(parts) == 3
            and parts[0] == "reviews"
            and parts[1] in self.data["reviews"]
            and parts[2]
            in {
                "before.png",
                "after.png",
                "comparison.png",
                "plan.json",
                "diff.json",
                "validation.json",
            }
        ):
            directory, filename = self.directory(parts[1]), parts[2]
        else:
            raise FileNotFoundError("Unknown artifact")
        path = directory / filename
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError("Unknown artifact")
        return path
