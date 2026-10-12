"""Durable two-phase Paper transactions; Minecraft owns world backup and application.

Candidate schematics are review data. The shared city state advances only after
the authenticated plugin confirms its durable world operation has succeeded.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from .building import build_city
from .edit_models import Selection, digest
from .editing import load_plan, verify_plan
from .models import StrictModel
from .project import load_project
from .schematic import BlockVolume, write_schematic
from .web_state import Conflict


class PaperConfig(StrictModel):
    token: str = Field(min_length=32, max_length=256)
    players: list[str] = Field(default_factory=list)
    max_blocks: int = Field(default=5000, ge=1, le=20000)
    max_volume: int = Field(default=262144, ge=1, le=1048576)


class Binding(StrictModel):
    server_id: uuid.UUID
    world_uuid: uuid.UUID
    world_name: str = Field(min_length=1, max_length=64)
    origin: tuple[int, int, int]
    rotation: Literal[0] = 0

    @model_validator(mode="after")
    def bounded_origin(self):
        if any(abs(v) > 30000000 for v in self.origin):
            raise ValueError("Minecraft origin out of bounds")
        return self


class GamePlan(StrictModel):
    revision: str
    instruction: str = Field(min_length=1, max_length=2000)
    selection: Selection = Field(default_factory=Selection)
    seed: int = Field(default=42, ge=0, le=2**63 - 1)
    binding: Binding


class Adopt(StrictModel):
    review: str = Field(pattern=r"^[0-9a-f]{32}$")
    binding: Binding


class Prepare(StrictModel):
    job: str = Field(pattern=r"^[0-9a-f]{32}$")
    revision: str
    approval: str = Field(pattern=r"^[0-9a-f]{64}$")
    binding: Binding


class Finish(StrictModel):
    job: str = Field(pattern=r"^[0-9a-f]{32}$")
    outcome: Literal["commit", "rollback"]


class Progress(StrictModel):
    job: str = Field(pattern=r"^[0-9a-f]{32}$")
    completed: int = Field(ge=0)


def block_patch(before, after, output, max_blocks=5000, max_volume=262144):
    """Diff actual rendered states, never palette IDs; unchanged cells are not written."""
    first, second = build_city(before.city), build_city(after.city)
    height = max(first.blocks.shape[0], second.blocks.shape[0])
    size = before.city.size
    if size != after.city.size:
        raise ValueError("Phase 4 does not resize cities")
    states = sorted(set(first.palette) | set(second.palette))
    common = {name: i for i, name in enumerate(states)}

    def aligned(volume):
        remap = np.empty(len(volume.palette), dtype=np.uint16)
        for name, index in volume.palette.items():
            remap[index] = common[name]
        array = np.full((height, size, size), common["minecraft:air"], dtype=np.uint16)
        array[: volume.blocks.shape[0]] = remap[volume.blocks]
        return array

    a, b = aligned(first), aligned(second)
    coords = np.argwhere(a != b)  # y,z,x order used by the schematic writer
    count = len(coords)
    if not 0 < count <= max_blocks:
        raise ValueError(f"変更ブロック数 {count} は許可範囲1..{max_blocks}外です。")
    y0, z0, x0 = coords.min(axis=0).tolist()
    y1, z1, x1 = (coords.max(axis=0) + 1).tolist()
    volume_size = (y1 - y0) * (z1 - z0) * (x1 - x0)
    if volume_size > max_volume:
        raise ValueError(f"差分の包絡領域 {volume_size} は上限{max_volume}を超えています。")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for label, array in (("before", a), ("after", b)):
        patch = BlockVolume(x1 - x0, y1 - y0, z1 - z0)
        patch.palette = common
        patch.blocks = array[y0:y1, z0:z1, x0:x1].copy()
        file = output / f"{label}.schem"
        write_schematic(patch, file, f"City edit {label}", "© OpenStreetMap contributors")
        hashes[label] = hashlib.sha256(file.read_bytes()).hexdigest()
    return {"changed_blocks": count, "bounds": [x0, y0, z0, x1, y1, z1], "schematic_sha256": hashes}


class PaperCoordinator:
    def __init__(self, workspace, config_path):
        self.workspace = workspace
        self.config = PaperConfig.model_validate_json(Path(config_path).read_text(encoding="utf-8"))
        for actor in self.config.players:
            if actor != "*":
                uuid.UUID(actor)
        self.path = workspace.root / "paper-state.json"
        self.data = (
            json.loads(self.path.read_text())
            if self.path.exists()
            else {"binding": None, "active": None, "jobs": {}}
        )
        self.committing = False
        workspace.paper = self

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(self.data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def actor(self, value):
        actor = str(uuid.UUID(value))
        if "*" not in self.config.players and actor not in self.config.players:
            raise PermissionError("Paperプレイヤーの権限がありません。")
        return actor

    def binding(self, value):
        data = Binding.model_validate(value).model_dump(mode="json")
        if self.data["binding"] is not None and self.data["binding"] != data:
            raise Conflict(
                "都市のワールド・原点が一致しません。登録済みのanchorを使用してください。"
            )
        return data

    def guard(self):
        if self.data["active"] and not self.committing:
            raise Conflict(
                "Paperワールドの編集中または復旧待ちです。/city status を確認してください。"
            )

    def web_guard(self):
        if not self.committing:
            raise Conflict("この都市はPaper連携中です。ゲーム内で承認・Undoしてください。")

    def summary(self):
        return {"enabled": True, "active_job": self.data["active"], "binding": self.data["binding"]}

    def get_job(self, identifier, actor):
        job = self.data["jobs"].get(identifier)
        if job is None:
            raise FileNotFoundError("Paper job not found")
        if job["actor"] != actor:
            raise PermissionError("他プレイヤーの計画は操作できません。")
        # Recover a lost finish response / process stop between the two manifest writes.
        if (
            job["status"] in {"prepared", "running"}
            and digest(self.workspace.project) == job["after_revision"]
        ):
            job["status"] = "committed"
            job["completed"] = job["changed_blocks"]
            if job["action"] == "apply":
                job["snapshot"] = self.workspace.data["current"]
            else:
                self.data["jobs"][job["undo_of"]]["status"] = "undone"
            self.data["active"] = None
            self.save()
        return job

    def create(self, actor, binding, review=None, undo=False):
        self.guard()
        binding = self.binding(binding)
        workspace = self.workspace
        if undo:
            if not workspace.data["stack"]:
                raise Conflict("取り消せる編集がありません。")
            entry = workspace.data["stack"][-1]
            committed = [
                j
                for j in self.data["jobs"].values()
                if j["status"] == "committed"
                and j["action"] == "apply"
                and j["after_revision"] == workspace.data["revision"]
                and j.get("snapshot") == entry["snapshot"]
            ]
            if len(committed) != 1 or committed[0]["actor"] != actor:
                raise Conflict("現在状態に対応するPaperワールド履歴がありません。")
            before_snapshot = workspace.directory(entry["snapshot"]) / "before.project.json"
            after = load_project(before_snapshot)
            plan_id = entry["plan_id"]
            undo_of = committed[0]["id"]
        else:
            if review not in workspace.data["reviews"]:
                raise Conflict("有効な編集計画がありません。")
            if any(
                j["review"] == review and j["actor"] != actor for j in self.data["jobs"].values()
            ):
                raise PermissionError("他プレイヤーが所有する編集計画です。")
            plan = load_plan(workspace.directory(review) / "plan.json")
            after = verify_plan(workspace.project, plan)
            plan_id = plan.plan_id
            undo_of = None
        identifier = uuid.uuid4().hex
        patch = block_patch(
            workspace.project,
            after,
            workspace.directory(identifier),
            self.config.max_blocks,
            self.config.max_volume,
        )
        job = {
            "id": identifier,
            "protocol": 1,
            "actor": actor,
            "binding": binding,
            "review": review,
            "action": "undo" if undo else "apply",
            "undo_of": undo_of,
            "plan_id": plan_id,
            "before_revision": workspace.data["revision"],
            "after_revision": digest(after),
            "status": "planned",
            "completed": 0,
            **patch,
        }
        self.data["jobs"][identifier] = job
        self.data["binding"] = binding
        self.save()
        return job

    def plan(self, actor, payload):
        req = GamePlan.model_validate(payload)
        self.guard()
        self.binding(req.binding.model_dump(mode="json"))
        result = self.workspace.plan(req.model_dump(exclude={"binding"}, mode="json"))
        return {
            **result,
            "job": self.create(actor, req.binding.model_dump(mode="json"), result["review"]),
        }

    def prepare(self, actor, payload):
        req = Prepare.model_validate(payload)
        job = self.get_job(req.job, actor)
        self.binding(req.binding.model_dump(mode="json"))
        if req.binding.model_dump(mode="json") != job["binding"] or req.approval != job["plan_id"]:
            raise Conflict("承認IDまたは建築位置が一致しません。")
        self.workspace.check_revision(req.revision)
        if job["before_revision"] != req.revision or job["status"] != "planned":
            raise Conflict("古い計画または二重承認です。再計画してください。")
        if job["action"] == "apply":
            if job["review"] not in self.workspace.data["reviews"]:
                raise Conflict("計画の有効期間が終了しました。再計画してください。")
            verify_plan(
                self.workspace.project,
                load_plan(self.workspace.directory(job["review"]) / "plan.json"),
            )
        self.guard()
        job["status"] = "prepared"
        self.data["active"] = job["id"]
        self.save()
        return job

    def finish(self, actor, payload):
        req = Finish.model_validate(payload)
        job = self.get_job(req.job, actor)
        target = "committed" if req.outcome == "commit" else "rolled_back"
        if job["status"] == target:
            return {"job": job, "state": self.workspace.state()}
        if self.data["active"] != job["id"] or job["status"] not in {"prepared", "running"}:
            raise Conflict("確定できるワールドジョブではありません。")
        self.workspace.check_revision(job["before_revision"])
        if req.outcome == "commit":
            if job["completed"] != job["changed_blocks"]:
                raise Conflict("ワールドの全ブロック反映が完了していません。")
            self.committing = True
            try:
                if job["action"] == "apply":
                    self.workspace.apply(
                        {
                            "revision": job["before_revision"],
                            "review": job["review"],
                            "approval": job["plan_id"],
                        }
                    )
                else:
                    self.workspace.undo(
                        {"revision": job["before_revision"], "approval": job["plan_id"]}
                    )
            finally:
                self.committing = False
            if job["action"] == "apply":
                job["snapshot"] = self.workspace.data["current"]
            else:
                self.data["jobs"][job["undo_of"]]["status"] = "undone"
        job["status"] = target
        if target == "committed":
            job["completed"] = job["changed_blocks"]
        self.data["active"] = None
        self.save()
        return {"job": job, "state": self.workspace.state()}

    def api(self, method, route, payload, actor):
        actor = self.actor(actor)
        parts = route.split("/")
        if method == "GET" and route == "state":
            return {**self.workspace.state(), "paper": self.summary()}
        if method == "GET" and len(parts) in {2, 3} and parts[0] == "jobs":
            job = self.get_job(parts[1], actor)
            if len(parts) == 2:
                return job
            if parts[2] not in {"before.schem", "after.schem"}:
                raise FileNotFoundError()
            file = self.workspace.directory(job["id"]) / parts[2]
            if file.is_symlink() or not file.is_file():
                raise FileNotFoundError()
            return file
        if method != "POST":
            raise FileNotFoundError()
        if route == "plan":
            return self.plan(actor, payload)
        if route == "adopt":
            req = Adopt.model_validate(payload)
            return self.create(actor, req.binding.model_dump(mode="json"), req.review)
        if route == "undo-plan":
            binding = Binding.model_validate(payload)
            return self.create(actor, binding.model_dump(mode="json"), undo=True)
        if route == "prepare":
            return self.prepare(actor, payload)
        if route == "finish":
            return self.finish(actor, payload)
        if route == "progress":
            req = Progress.model_validate(payload)
            job = self.get_job(req.job, actor)
            if (
                self.data["active"] != job["id"]
                or job["status"] not in {"prepared", "running"}
                or not job["completed"] <= req.completed <= job["changed_blocks"]
            ):
                raise Conflict("進捗を更新できません。")
            job.update(status="running", completed=req.completed)
            self.save()
            return job
        if route == "cancel":
            if set(payload) != {"job"}:
                raise ValueError("cancel requires a job identifier only")
            job = self.get_job(payload["job"], actor)
            if job["status"] != "planned":
                raise Conflict("ワールド処理中のキャンセルは復元完了後に報告してください。")
            job["status"] = "cancelled"
            self.save()
            return job
        raise FileNotFoundError()
