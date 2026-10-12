"""Phase 5 bounded declarative design; no commands, filenames or arbitrary blocks."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

Category = Literal[
    "house",
    "rental_apartment",
    "apartment",
    "office",
    "commercial",
    "convenience_store",
    "supermarket",
    "school",
    "station",
    "factory",
    "warehouse",
    "public",
]
Material = Literal[
    "white_concrete",
    "light_gray_concrete",
    "gray_concrete",
    "white_terracotta",
    "smooth_sandstone",
    "bricks",
    "deepslate_tiles",
    "stone_bricks",
    "oak_planks",
    "dark_oak_planks",
    "smooth_stone",
    "iron_block",
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Mass(Contract):
    x: int = Field(ge=2, le=60)
    z: int = Field(ge=3, le=60)
    width: int = Field(ge=5, le=60)
    depth: int = Field(ge=5, le=60)
    floors: int = Field(ge=1, le=23)


class Windows(Contract):
    spacing: int = Field(ge=3, le=6)
    width: int = Field(ge=1, le=3)
    height: int = Field(ge=1, le=2)

    @model_validator(mode="after")
    def pillars(self):
        if self.width >= self.spacing:
            raise ValueError("窓の間には柱が必要です。")
        return self


class ArchitectureSpec(Contract):
    schema_version: Literal[1] = 1
    category: Category
    site_width: int = Field(ge=12, le=64)
    site_depth: int = Field(ge=14, le=64)
    height: int = Field(ge=7, le=96)
    floors: int = Field(ge=1, le=23)
    story_height: int = Field(ge=4, le=6)
    shape: Literal["rectangle", "l_shape", "courtyard", "stepped"]
    masses: list[Mass] = Field(min_length=1, max_length=3)
    wall: Material
    accent: Material
    roof_material: Material
    roof: Literal["flat", "gable", "terrace"]
    windows: Windows
    entrance_x: int = Field(ge=3, le=60)
    balconies: bool
    canopy: bool
    signage: bool
    parking: bool
    planting: bool
    stairs: bool = False
    road_facing: Literal["north", "east", "south", "west"] = "north"
    seed: int = Field(ge=0, le=2**63 - 1)

    @model_validator(mode="after")
    def bounded_design(self):
        if self.height < self.floors * self.story_height + 2:
            raise ValueError("階高・屋根を収める高さがありません。階数を減らしてください。")
        if max(m.floors for m in self.masses) != self.floors:
            raise ValueError("mass階数と建物階数が一致しません。")
        for m in self.masses:
            if m.x + m.width > self.site_width - 2 or m.z + m.depth > self.site_depth - 2:
                raise ValueError("建物と外装の余白が敷地に収まりません。")
        from shapely.geometry import box
        from shapely.ops import unary_union

        geometry = unary_union([box(m.x, m.z, m.x + m.width, m.z + m.depth) for m in self.masses])
        if geometry.geom_type != "Polygon":
            raise ValueError("建物の棟を接続してください。離れた棟は1建築案にしません。")
        front = min(m.z for m in self.masses)
        if not any(
            m.z == front and m.x + 1 <= self.entrance_x < m.x + m.width - 1 for m in self.masses
        ):
            raise ValueError("入口は道路側の外壁上に必要です。")
        return self


class ArchitectureRequest(Contract):
    brief: str | None = Field(default=None, max_length=2000)
    category: Category
    width: int = Field(ge=12, le=64)
    depth: int = Field(ge=14, le=64)
    height: int | None = Field(default=None, ge=7, le=96)
    mode: Literal["local", "llm", "auto"] = "local"
    # Validated design is frozen into the plan. Approval never calls the LLM again.
    design: ArchitectureSpec | None = None
    execution: dict | None = None

    @model_serializer(mode="wrap")
    def compatible_brief(self, handler):
        data = handler(self)
        if self.brief is None:
            data.pop("brief", None)
        return data
