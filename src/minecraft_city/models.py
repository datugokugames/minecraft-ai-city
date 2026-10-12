"""Validated public JSON contracts; never pass arbitrary AI block names to the builder."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from .architecture_models import ArchitectureSpec, FacadeRecipe
from .urban_models import UrbanPlan


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Metrics(StrictModel):
    area_km2: float = Field(gt=0)
    road_density_km_km2: float = Field(ge=0)
    intersection_count: int = Field(ge=0)
    intersection_density_km2: float = Field(ge=0)
    building_count: int = Field(ge=0)
    building_density_km2: float = Field(ge=0)
    building_coverage: float = Field(ge=0, le=1)
    mean_building_area_m2: float = Field(ge=0)
    median_block_area_m2: float = Field(ge=0)
    mean_block_compactness: float = Field(ge=0, le=1)
    orthogonal_road_share: float = Field(ge=0, le=1)
    mean_height_m: float = Field(gt=0)
    height_tag_coverage: float = Field(ge=0, le=1)
    landuse_tag_coverage: float = Field(default=0, ge=0, le=1)
    residential_share: float = Field(ge=0, le=1)
    commercial_share: float = Field(ge=0, le=1)
    park_share: float = Field(ge=0, le=1)
    road_sinuosity: float = Field(default=1.05, ge=1)
    turn_degrees_per_km: float = Field(default=250, ge=0)
    orientation_entropy: float = Field(default=0.6, ge=0, le=1)
    t_junction_share: float = Field(default=0.55, ge=0, le=1)
    cross_junction_share: float = Field(default=0.25, ge=0, le=1)
    irregular_junction_share: float = Field(default=0.2, ge=0, le=1)
    dead_end_share: float = Field(default=0.12, ge=0, le=1)
    road_spacing_cv: float = Field(default=0.4, ge=0)
    block_area_cv: float = Field(default=0.7, ge=0)
    block_elongation: float = Field(default=1.6, ge=1)
    arterial_share: float = Field(default=0.2, ge=0, le=1)
    alley_share: float = Field(default=0.25, ge=0, le=1)
    mean_road_width_m: float = Field(default=5.5, gt=0)
    width_tag_coverage: float = Field(default=0, ge=0, le=1)
    river_density_km_km2: float = Field(default=0, ge=0)
    railway_density_km_km2: float = Field(default=0, ge=0)
    barrier_crossings_km2: float = Field(default=0, ge=0)
    terrain_relief_m: float = Field(default=0, ge=0)
    elevation_sample_count: int = Field(default=0, ge=0)


class Profile(StrictModel):
    schema_version: Literal[1] = 1
    name: str
    source: Literal["osm", "synthetic_sample"]
    attribution: str
    bounds: list[float] | None = None
    retrieved_at: str | None = None
    notes: list[str] = Field(default_factory=list)
    metrics: Metrics
    distributions: dict[str, list[float]] = Field(default_factory=dict)


class CityPlan(StrictModel):
    schema_version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(max_length=600)
    style: Literal["traditional", "modern", "mixed"]
    road_spacing: int = Field(ge=32, le=64)
    road_width: int = Field(ge=5, le=9)
    irregularity: float = Field(ge=0, le=0.3)
    residential_ratio: float = Field(ge=0.25, le=0.8)
    commercial_ratio: float = Field(ge=0.1, le=0.6)
    park_ratio: float = Field(ge=0.08, le=0.35)
    building_coverage: float = Field(ge=0.15, le=0.6)
    max_floors: int = Field(ge=2, le=8)
    railway: bool

    @model_validator(mode="after")
    def district_sum(self):
        total = self.residential_ratio + self.commercial_ratio + self.park_ratio
        if abs(total - 1.0) > 0.001:
            raise ValueError("district ratios must sum to 1")
        return self


class District(StrictModel):
    id: int
    kind: Literal["residential", "commercial", "park", "industrial"]
    polygon: list[tuple[float, float]]
    character: str = "legacy"
    center: tuple[float, float] | None = None
    influence: float = Field(default=1, gt=0, le=2)


class Building(StrictModel):
    id: int
    district_id: int
    kind: Literal["house", "apartment", "mixed_use", "shop", "school", "factory", "station"]
    x: int
    z: int
    width: int = Field(ge=7)
    depth: int = Field(ge=7)
    floors: int = Field(ge=1, le=23)
    facing: Literal["north", "south", "east", "west"]
    palette: Literal["traditional", "modern", "warm"]
    angle: float = 0
    footprint: list[tuple[float, float]] = Field(default_factory=list)
    entrance: tuple[float, float] | None = None
    access_path: list[tuple[float, float]] = Field(default_factory=list)
    variant: int = Field(default=0, ge=0, le=7)
    detail: Literal["convenience_store"] | None = None
    architecture: ArchitectureSpec | None = None
    facade: FacadeRecipe | None = None

    @model_validator(mode="after")
    def consistent_architecture(self):
        if self.architecture is not None:
            spec = self.architecture
            expected = (
                (spec.site_depth, spec.site_width)
                if self.facing in {"east", "west"}
                else (spec.site_width, spec.site_depth)
            )
            if (
                (self.width, self.depth) != expected
                or self.floors != spec.floors
                or self.facing != spec.road_facing
                or self.angle != 0
            ):
                raise ValueError(
                    "architecture dimensions/floors/facing must match the city building"
                )
        return self

    @model_serializer(mode="wrap")
    def compatible_detail(self, handler):
        data = handler(self)
        # Preserve v0.2.1 City JSON/digests exactly when no new template is used.
        if self.detail is None:
            data.pop("detail", None)
        if self.architecture is None:
            data.pop("architecture", None)
        if self.facade is None:
            data.pop("facade", None)
        return data


class RoadSpec(StrictModel):
    kind: Literal["arterial", "collector", "local", "alley"]
    width: float = Field(ge=2, le=12)
    purpose: str = "service"
    intentional_end: bool = False


class Site(StrictModel):
    kind: Literal["parking", "vacant", "park", "plaza"]
    polygon: list[tuple[float, float]]
    access_path: list[tuple[float, float]] = Field(default_factory=list)


class Landscape(StrictModel):
    river: list[tuple[float, float]] = Field(default_factory=list)
    river_width: float = 8
    bridge_z: list[float] = Field(default_factory=list)
    rail_crossings_x: list[float] = Field(default_factory=list)
    hills: list[tuple[float, float, float, float]] = Field(default_factory=list)
    source: str = "synthetic geography; source statistics only, no copied terrain"


class City(StrictModel):
    schema_version: Literal[1] = 1
    size: int = Field(ge=128, le=512)
    seed: int = Field(ge=0, le=2**63 - 1)
    plan: CityPlan
    planning_source: str
    sources: list[dict]
    blended_metrics: Metrics
    roads: list[list[tuple[float, float]]]
    districts: list[District]
    buildings: list[Building]
    railway_z: int | None
    validation: dict = Field(default_factory=dict)
    layout: Literal["legacy", "organic"] = "legacy"
    road_specs: list[RoadSpec] = Field(default_factory=list)
    sites: list[Site] = Field(default_factory=list)
    landscape: Landscape = Field(default_factory=Landscape)
    quality: dict = Field(default_factory=dict)
    urban_plan: UrbanPlan | None = None

    @model_serializer(mode="wrap")
    def compatible_urban_plan(self, handler):
        data = handler(self)
        if self.urban_plan is None:
            data.pop("urban_plan", None)
        return data


def save_json(path: str | Path, value: BaseModel | dict | list) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


def load_profile(path: str | Path) -> Profile:
    return Profile.model_validate_json(Path(path).read_text(encoding="utf-8"))
