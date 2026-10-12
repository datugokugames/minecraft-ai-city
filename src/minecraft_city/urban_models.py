"""Optional v0.6 planning records, distinct from occupied project parcels.

These are a planning snapshot, not an authorization to edit a world. Existing
editing still checks the current city's physical buildings, sites and transport.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from shapely.geometry import Polygon


class PlanningModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class LandPolygon(PlanningModel):
    polygon: list[tuple[float, float]] = Field(min_length=4)
    holes: list[list[tuple[float, float]]] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_polygon(self):
        geometry = Polygon(self.polygon, self.holes)
        if not geometry.is_valid or geometry.area <= 0:
            raise ValueError("planning polygon must be valid with positive area")
        return self


class UrbanBlock(LandPolygon):
    id: str
    boundary_block: bool = False


class UrbanParcel(LandPolygon):
    id: str
    block_id: str
    district_id: int
    use: Literal["residential", "commercial", "industrial", "park", "parking", "plaza", "vacant"]
    disposition: Literal["built", "open_space", "landlocked", "unfilled", "existing"]
    building_id: int | None = None
    frontage_road_ids: list[int] = Field(default_factory=list)
    access_path: list[tuple[float, float]] = Field(default_factory=list)
    corner: bool = False


class RoadGraphNode(PlanningModel):
    id: int = Field(ge=0)
    x: float
    z: float


class RoadGraphEdge(PlanningModel):
    source: int = Field(ge=0)
    target: int = Field(ge=0)
    length: float = Field(gt=0)
    road_ids: list[int] = Field(min_length=1)


class UrbanPlan(PlanningModel):
    version: Literal[1] = 1
    seed: int = Field(default=42, ge=0, le=2**63 - 1)
    source_city_sha256: str = ""
    blocks: list[UrbanBlock] = Field(default_factory=list)
    parcels: list[UrbanParcel] = Field(default_factory=list)
    nodes: list[RoadGraphNode] = Field(default_factory=list)
    edges: list[RoadGraphEdge] = Field(default_factory=list)
    added_road_ids: list[int] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def references(self):
        blocks = {b.id for b in self.blocks}
        nodes = {n.id for n in self.nodes}
        if len(blocks) != len(self.blocks) or len(nodes) != len(self.nodes):
            raise ValueError("duplicate block or graph node IDs")
        if len({p.id for p in self.parcels}) != len(self.parcels):
            raise ValueError("duplicate planning parcel IDs")
        if any(p.block_id not in blocks for p in self.parcels):
            raise ValueError("planning parcel refers to missing block")
        if any(
            e.source not in nodes or e.target not in nodes or e.source == e.target
            for e in self.edges
        ):
            raise ValueError("invalid graph edge endpoints")
        return self
