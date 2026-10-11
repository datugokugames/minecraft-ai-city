"""Versioned contracts for bounded, reviewable edits; no executable AI payloads."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from .models import Building, Site, StrictModel


class EditError(ValueError):
    code = "invalid_edit"


class ClarificationRequired(EditError):
    code = "clarification_required"


class Selection(StrictModel):
    city_name: str | None = None
    ward: str | None = None
    district: str | None = None
    building_id: int | None = Field(default=None, ge=0)
    point: tuple[float, float] | None = None
    bounds: tuple[float, float, float, float] | None = None
    station_side: Literal["east"] | None = None

    @model_validator(mode="after")
    def ordered_bounds(self):
        if self.bounds and not (
            self.bounds[0] < self.bounds[2] and self.bounds[1] < self.bounds[3]
        ):
            raise ValueError("bounds must be xmin,zmin,xmax,zmax with positive area")
        return self


class EditIntent(StrictModel):
    action: Literal["add_buildings", "add_park", "restyle"]
    building_type: Literal["house", "apartment", "convenience_store"] | None = None
    count: int = Field(default=1, ge=1, le=10)
    palette: Literal["modern", "traditional", "warm"] | None = None
    selection: Selection = Field(default_factory=Selection)

    @model_validator(mode="after")
    def coherent_action(self):
        if self.action == "add_buildings":
            if self.building_type is None or self.selection.building_id is not None:
                raise ValueError("building addition needs a type and a location, not a building ID")
        elif self.building_type is not None or self.count != 1:
            raise ValueError("only building additions support type/count")
        if self.action == "restyle" and self.palette is None:
            raise ValueError("restyle requires a palette")
        if self.action == "add_park" and (
            self.palette is not None or self.selection.building_id is not None
        ):
            raise ValueError("park addition needs a location only")
        return self


class BuildingChange(StrictModel):
    before: Building
    after: Building


class EditDelta(StrictModel):
    added_buildings: list[Building] = Field(default_factory=list)
    changed_buildings: list[BuildingChange] = Field(default_factory=list)
    added_sites: list[Site] = Field(default_factory=list)
    # New occupied parcels are assigned to a physical district before approval.
    site_district_ids: list[int] = Field(default_factory=list)


def digest(value) -> str:
    data = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    encoded = json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class EditPlan(StrictModel):
    schema_version: Literal[1] = 1
    engine_version: Literal["phase3a-1"] = "phase3a-1"
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_project_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    instruction: str = Field(min_length=1, max_length=2000)
    parser_source: Literal["rules", "rules_fallback", "openai"]
    seed: int = Field(ge=0, le=2**63 - 1)
    intent: EditIntent
    changes: EditDelta
    after_project_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    validation: dict
    assumptions: list[str]

    @model_validator(mode="after")
    def intact_plan(self):
        if self.plan_id != digest(self.model_dump(mode="json", exclude={"plan_id"})):
            raise ValueError("edit plan digest mismatch; regenerate and review the preview")
        return self


class EditRecord(StrictModel):
    schema_version: Literal[1] = 1
    action: Literal["apply", "undo"]
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved_plan_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    before_project_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    after_project_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
