"""Rasterize rotated structures and landscape with the same footprints as the plan."""

import math
import re

import numpy as np
from shapely.geometry import LineString, Point, Polygon

from .building import _tree, build_structure
from .generation import building_area, road_area
from .organic import river_shape
from .preview import mask
from .schematic import BlockVolume


def rotated_structure(volume, building):
    if building.architecture is not None:
        from .architecture_geometry import paste_architecture

        paste_architecture(volume, building)
        return
    local = BlockVolume(building.width + 4, volume.blocks.shape[0], building.depth + 4)
    local.fill(0, 3, 0, building.width + 4, 4, building.depth + 4, "grass_block[snowy=false]")
    build_structure(
        local, building.model_copy(update={"x": 2, "z": 2, "angle": 0, "footprint": []})
    )
    envelope = building_area(building).buffer(1.4, join_style="mitre")
    minx, minz, maxx, maxz = envelope.bounds
    zz, xx = np.mgrid[
        max(0, math.floor(minz)) : min(volume.blocks.shape[1], math.ceil(maxz)),
        max(0, math.floor(minx)) : min(volume.blocks.shape[2], math.ceil(maxx)),
    ]
    angle = math.radians(building.angle)
    dx, dz = (
        xx + 0.5 - (building.x + building.width / 2),
        zz + 0.5 - (building.z + building.depth / 2),
    )
    u = np.floor(dx * math.cos(angle) + dz * math.sin(angle) + building.width / 2 + 2).astype(int)
    v = np.floor(-dx * math.sin(angle) + dz * math.cos(angle) + building.depth / 2 + 2).astype(int)
    valid = (u >= 1) & (u < building.width + 3) & (v >= 1) & (v < building.depth + 3)
    compass = ["north", "east", "south", "west"]
    quarter_turns = round(building.angle / 90)

    def rotate_state(block):
        return re.sub(
            r"facing=(north|east|south|west)",
            lambda m: "facing=" + compass[(compass.index(m[1]) + quarter_turns) % 4],
            block,
        )

    remap = np.array(
        [volume.state(rotate_state(block)) for block in local.palette], dtype=np.uint16
    )
    volume.blocks[3:, zz[valid], xx[valid]] = remap[local.blocks[3:, v[valid], u[valid]]]
    # A sub-voxel diagonal doorway can disappear in nearest-neighbour rasterization.
    # Explicitly carve the real entrance and place a complete, cardinal-facing door pair.
    inward = np.array([-math.sin(angle), math.cos(angle)])
    door = np.floor(np.array(building.entrance) + inward * 0.65).astype(int)
    for t in np.arange(-1, 2.6, 0.4):
        x, z = np.floor(np.array(building.entrance) + inward * t).astype(int)
        volume.blocks[4:6, z, x] = 0
    facing = compass[quarter_turns % 4]
    for y, half in ((4, "lower"), (5, "upper")):
        volume.put(
            int(door[0]),
            y,
            int(door[1]),
            f"oak_door[facing={facing},half={half},hinge=left,open=false,powered=false]",
        )


def build_organic(city):
    from .quality import validate_organic

    validate_organic(city)
    size = city.size
    height = max(
        28,
        max(
            (b.architecture.height + 4 if b.architecture else b.floors * 4 + 12)
            for b in city.buildings
        ),
    )
    v = BlockVolume(size, height, size)
    v.fill(0, 0, 0, size, 1, size, "stone")
    v.fill(0, 1, 0, size, 3, size, "dirt")
    v.fill(0, 3, 0, size, 4, size, "grass_block[snowy=false]")
    streets, asphalt = mask(road_area(city), size), mask(road_area(city, False), size)
    water = mask(river_shape(city), size)
    v.blocks[3, water] = 0
    v.blocks[2, water] = v.state("water[level=0]")
    zz, xx = np.mgrid[0:size, 0:size]
    clear = streets | water
    for building in city.buildings:
        clear |= mask(building_area(building).buffer(0 if building.architecture else 5), size)
        clear |= mask(LineString(building.access_path).buffer(2), size)
    for site in city.sites:
        clear |= mask(Polygon(site.polygon).buffer(2), size)
    for hx, hz, radius, top in city.landscape.hills:
        heights = (top * np.exp(-((xx - hx) ** 2 + (zz - hz) ** 2) / radius**2)).astype(int)
        for y in range(1, int(top) + 1):
            pixels = (heights >= y) & ~clear
            v.blocks[3 + y, pixels] = v.state(
                "grass_block[snowy=false]" if y == int(top) else "dirt"
            )
            surface = (heights == y) & ~clear
            v.blocks[3 + y, surface] = v.state("grass_block[snowy=false]")
    v.blocks[3, streets] = v.state("smooth_stone")
    v.blocks[3, asphalt] = v.state("gray_concrete")
    v.blocks[3, water & streets] = v.state("stone_bricks")
    for road, spec in zip(city.roads, city.road_specs):
        line = LineString(road)
        if spec.kind == "alley":
            alley = mask(line.buffer(spec.width / 2), size)
            v.blocks[3, alley] = v.state("andesite")
        if spec.kind == "arterial":
            for t in np.arange(3, line.length - 2, 7):
                for dt in (0, 1):
                    p = line.interpolate(t + dt)
                    v.put(int(p.x), 3, int(p.y), "white_concrete")
        if spec.intentional_end and spec.purpose != "regional_gateway":
            bulb = mask(Point(road[-1]).buffer(spec.width * 0.6), size) & ~water
            v.blocks[3, bulb] = v.state("gray_concrete")
    for site in city.sites:
        area = mask(Polygon(site.polygon), size)
        if site.access_path:
            v.blocks[3, mask(LineString(site.access_path).buffer(0.9), size) & ~asphalt] = v.state(
                "smooth_stone"
            )
        v.blocks[3, area] = v.state(
            "light_gray_concrete"
            if site.kind == "parking"
            else "grass_block[snowy=false]"
            if site.kind == "park"
            else "coarse_dirt"
        )
        if site.kind == "parking":
            v.blocks[3, area & (xx % 4 == 0)] = v.state("white_concrete")
        elif site.kind == "park":
            polygon = Polygon(site.polygon)
            cx, cz = int(polygon.centroid.x), int(polygon.centroid.y)
            path = area & ((abs(xx - cx) <= 1) | (abs(zz - cz) <= 1))
            v.blocks[3, path] = v.state("gravel")
            for z in range(
                max(2, math.floor(polygon.bounds[1]) + 2),
                min(size - 2, math.ceil(polygon.bounds[3]) - 1),
            ):
                for x in range(
                    max(2, math.floor(polygon.bounds[0]) + 2),
                    min(size - 2, math.ceil(polygon.bounds[2]) - 1),
                ):
                    if not polygon.covers(Point(x + 0.5, z + 0.5)) or path[z, x]:
                        continue
                    if x % 4 == 0 and z % 4 == 0:
                        v.put(x, 4, z, "poppy")
            if polygon.covers(Point(cx - 3, cz - 3).buffer(2)):
                _tree(v, cx - 3, cz - 3)
            if polygon.covers(Point(cx + 2, cz + 2)):
                v.put(
                    cx + 2,
                    4,
                    cz + 2,
                    "oak_stairs[facing=north,half=bottom,shape=straight,waterlogged=false]",
                )
    park = Polygon(city.districts[5].polygon)
    for z in range(8, size - 8, 9):
        for x in range(8, size - 8, 9):
            if (
                park.covers(Point(x, z).buffer(3))
                and not clear[z - 3 : z + 4, x - 3 : x + 4].any()
                and not np.any(v.blocks[4:11, z - 2 : z + 3, x - 2 : x + 3])
            ):
                _tree(v, x, z)
    for building in city.buildings:
        path = mask(LineString(building.access_path).buffer(0.9), size)
        v.blocks[3, path & ~asphalt] = v.state("smooth_sandstone")
        rotated_structure(v, building)
    for z in range(10, size - 10, 17):
        for x in range(10, size - 10, 17):
            if streets[z, x] and not asphalt[z, x] and not water[z, x]:
                v.fill(x, 4, z, x + 1, 8, z + 1, "iron_bars")
                v.put(x, 8, z, "sea_lantern")
    if city.railway_z is not None:
        z = city.railway_z
        v.fill(0, 12, z - 2, size, 13, z + 3, "stone_bricks")
        for x in range(size):
            for rail_z in (z - 1, z + 1):
                v.put(x, 13, rail_z, "rail[shape=east_west,waterlogged=false]")
            if x % 12 == 0 and not streets[z - 2 : z + 3, x].any():
                v.fill(x, 3, z, x + 1, 12, z + 1, "stone_bricks")
    return v
