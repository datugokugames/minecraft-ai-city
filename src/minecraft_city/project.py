"""Named administrative hierarchy around an unchanged, self-contained City snapshot.

Preferences describe future development, never implicit construction operations.
Parcel boundaries are occupied footprints, not surveyed or fully subdivided lots.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator
from shapely.geometry import Point, Polygon

from .generation import building_area, validate_city
from .models import City, StrictModel

Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Use = Literal["residential", "commercial", "industrial", "park", "mixed"]
Style = Literal["traditional", "modern", "mixed"]


class Ward(StrictModel):
    id: Identifier
    name: Name


class ManagedDistrict(StrictModel):
    id: Identifier
    name: Name
    ward_id: Identifier
    city_district_id: int | None = Field(default=None, ge=0)
    preferred_use: Use | None = None
    preferred_style: Style | None = None


class Parcel(StrictModel):
    id: Identifier
    district_id: Identifier
    building_id: int | None = Field(default=None, ge=0)
    site_index: int | None = Field(default=None, ge=0)
    polygon: list[tuple[float, float]] = Field(min_length=4)
    boundary_kind: Literal["occupied_footprint"] = "occupied_footprint"

    @model_validator(mode="after")
    def one_source(self):
        if (self.building_id is None) == (self.site_index is None):
            raise ValueError("parcel must reference exactly one building or open-space site")
        polygon = Polygon(self.polygon)
        if not polygon.is_valid or polygon.area <= 0:
            raise ValueError("parcel polygon must be valid and have positive area")
        return self


def city_digest(city: City) -> str:
    payload = json.dumps(
        city.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _unique(items, label):
    ids = [item.id for item in items]
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate {label} IDs")
    return {item.id: item for item in items}


class CityProject(StrictModel):
    schema_version: Literal[1] = 1
    city_name: Name
    city: City
    city_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    wards: list[Ward] = Field(min_length=1)
    districts: list[ManagedDistrict]
    parcels: list[Parcel]

    @model_validator(mode="after")
    def consistent_snapshot(self):
        if city_digest(self.city) != self.city_sha256:
            raise ValueError("city snapshot digest mismatch")
        wards = _unique(self.wards, "ward")
        districts = _unique(self.districts, "managed district")
        _unique(self.parcels, "parcel")
        physical = _unique(self.city.districts, "city district")
        buildings = _unique(self.city.buildings, "building")
        if len({w.name for w in self.wards}) != len(wards):
            raise ValueError("ward names must be unique within a city")
        if len({(d.ward_id, d.name) for d in self.districts}) != len(districts):
            raise ValueError("district names must be unique within a ward")
        linked = []
        for district in self.districts:
            if district.ward_id not in wards:
                raise ValueError("district references an unknown ward")
            if district.city_district_id is not None:
                linked.append(district.city_district_id)
        if len(linked) != len(set(linked)) or set(linked) != set(physical):
            raise ValueError("each city district must have exactly one managed district")
        seen_buildings, seen_sites = set(), set()
        for parcel in self.parcels:
            district = districts.get(parcel.district_id)
            if district is None or district.city_district_id is None:
                raise ValueError("parcel must belong to an existing physical district")
            if parcel.building_id is not None:
                building = buildings.get(parcel.building_id)
                if building is None or parcel.building_id in seen_buildings:
                    raise ValueError("missing or multiply assigned building")
                if building.district_id != district.city_district_id:
                    raise ValueError("building and parcel district mismatch")
                seen_buildings.add(parcel.building_id)
                expected = building_area(building)
            else:
                index = parcel.site_index
                if index >= len(self.city.sites) or index in seen_sites:
                    raise ValueError("missing or multiply assigned site")
                seen_sites.add(index)
                expected = Polygon(self.city.sites[index].polygon)
            if Polygon(parcel.polygon).symmetric_difference(expected).area > 1e-6:
                raise ValueError("parcel boundary differs from its occupied footprint")
        if seen_buildings != set(buildings) or seen_sites != set(range(len(self.city.sites))):
            raise ValueError("every building and site must have a parcel")
        validate_city(self.city)
        return self


DISTRICT_NAMES = {
    "station": "駅前地区",
    "old_town": "旧市街",
    "suburban": "郊外住宅地区",
    "planned": "新興住宅地区",
    "industrial": "工業地区",
    "riverside": "河川沿い地区",
}


def create_project(
    city: City, city_name: str | None = None, ward_name: str = "中央区"
) -> CityProject:
    """Import legacy or organic geometry without changing any City fields."""
    districts = [
        ManagedDistrict(
            id=f"district-{d.id}",
            name=f"{DISTRICT_NAMES.get(d.character, '地区')} {d.id}",
            ward_id="ward-1",
            city_district_id=d.id,
            preferred_use=d.kind,
            preferred_style=city.plan.style,
        )
        for d in city.districts
    ]
    parcels = [
        Parcel(
            id=f"building-{b.id}",
            district_id=f"district-{b.district_id}",
            building_id=b.id,
            polygon=list(building_area(b).exterior.coords),
        )
        for b in city.buildings
    ]
    for i, site in enumerate(city.sites):
        center = Polygon(site.polygon).centroid
        # Sites did not have administrative IDs in v0.2. Prefer a containing district,
        # otherwise the nearest shape/centre; ID is a deterministic final tie-breaker.
        district = min(
            city.districts,
            key=lambda d: (
                Polygon(d.polygon).distance(center),
                Point(d.center).distance(center)
                if d.center
                else Polygon(d.polygon).centroid.distance(center),
                d.id,
            ),
        )
        parcels.append(
            Parcel(
                id=f"site-{i}",
                district_id=f"district-{district.id}",
                site_index=i,
                polygon=site.polygon,
            )
        )
    return CityProject(
        city_name=city.plan.name if city_name is None else city_name,
        city=city.model_copy(deep=True),
        city_sha256=city_digest(city),
        wards=[Ward(id="ward-1", name=ward_name)],
        districts=districts,
        parcels=parcels,
    )


class DistrictSetting(StrictModel):
    id: Identifier
    name: Name
    ward_id: Identifier
    preferred_use: Use | None = None
    preferred_style: Style | None = None


class ProjectSettings(StrictModel):
    city_name: Name | None = None
    wards: list[Ward] = Field(default_factory=list)
    districts: list[DistrictSetting] = Field(default_factory=list)


def configure_project(project: CityProject, settings: ProjectSettings) -> CityProject:
    """Explicit upserts only: omitted records/preferences remain untouched.

    A new district is a named, unbuilt reservation. Assigning construction geometry
    or deleting/redeveloping buildings requires a future reviewed change operation.
    """
    _unique(settings.wards, "ward setting")
    _unique(settings.districts, "district setting")
    payload = project.model_dump()
    if settings.city_name is not None:
        payload["city_name"] = settings.city_name
    wards = {w["id"]: w for w in payload["wards"]}
    for ward in settings.wards:
        wards[ward.id] = ward.model_dump(mode="json")
    districts = {d["id"]: d for d in payload["districts"]}
    for setting in settings.districts:
        previous = districts.get(setting.id, {"city_district_id": None})
        districts[setting.id] = {**previous, **setting.model_dump(mode="json", exclude_unset=True)}
    payload["wards"], payload["districts"] = list(wards.values()), list(districts.values())
    return CityProject.model_validate(payload)


def load_project(path: str | Path) -> CityProject:
    return CityProject.model_validate_json(Path(path).read_text(encoding="utf-8"))


def save_project(path: str | Path, project: CityProject) -> None:
    """Write a new snapshot exclusively; never overwrite an earlier project."""
    verified = CityProject.model_validate(project.model_dump())
    payload = verified.model_dump_json(indent=2) + "\n"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(payload)


def project_summary(project: CityProject) -> dict:
    """Hierarchy/selection data shared by CLI and future web/game adapters."""
    physical = {d.id: d for d in project.city.districts}
    wards = []
    for ward in project.wards:
        districts = []
        for district in project.districts:
            if district.ward_id != ward.id:
                continue
            geometry = physical.get(district.city_district_id)
            parcels = [p for p in project.parcels if p.district_id == district.id]
            districts.append(
                {
                    **district.model_dump(mode="json"),
                    "developed": geometry is not None,
                    "actual_use": geometry.kind if geometry else None,
                    "character": geometry.character if geometry else None,
                    "parcel_count": len(parcels),
                    "building_ids": [p.building_id for p in parcels if p.building_id is not None],
                    "site_indices": [p.site_index for p in parcels if p.site_index is not None],
                }
            )
        wards.append({**ward.model_dump(), "districts": districts})
    return {
        "city_name": project.city_name,
        "city_sha256": project.city_sha256,
        "size": project.city.size,
        "wards": wards,
    }
