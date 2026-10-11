"""Block-level Japanese architecture, streets, pocket parks, and elevated rail."""

from __future__ import annotations

import math

import numpy as np
from shapely.geometry import Point, Polygon

from .generation import road_area, validate_city
from .preview import mask
from .schematic import BlockVolume

PALETTES = {
    "traditional": ("white_terracotta", "dark_oak_planks", "deepslate_tiles"),
    "modern": ("white_concrete", "smooth_stone", "gray_concrete"),
    "warm": ("smooth_sandstone", "birch_planks", "bricks"),
}


def _front_point(b, along, outward=0):
    if b.facing == "north":
        return b.x + along, b.z - outward
    if b.facing == "south":
        return b.x + along, b.z + b.depth - 1 + outward
    if b.facing == "west":
        return b.x - outward, b.z + along
    return b.x + b.width - 1 + outward, b.z + along


def build_structure(v: BlockVolume, b):
    x, z, w, d = b.x, b.z, b.width, b.depth
    wall, floor, roof = PALETTES[b.palette]
    if b.kind == "school":
        wall, floor, roof = "smooth_sandstone", "birch_planks", "green_concrete"
    elif b.kind == "factory":
        wall, floor, roof = "light_gray_terracotta", "smooth_stone", "iron_block"
    elif b.kind == "station":
        wall, floor, roof = "white_concrete", "smooth_stone", "dark_prismarine"
    elif b.variant:
        wall = [wall, "white_terracotta", "smooth_sandstone", "light_gray_concrete"][b.variant % 4]
    for level in range(b.floors):
        base = 3 + 4 * level
        v.fill(x, base, z, x + w, base + 1, z + d, floor)
        v.fill(x, base + 1, z, x + w, base + 4, z + d, wall)
        v.fill(x + 1, base + 1, z + 1, x + w - 1, base + 4, z + d - 1, "air")
        for wx in range(x + 2, x + w - 1, 3):
            for zz in (z, z + d - 1):
                v.fill(
                    wx,
                    base + 2,
                    zz,
                    min(wx + 2, x + w - 1),
                    base + 4,
                    zz + 1,
                    "light_blue_stained_glass",
                )
        for wz in range(z + 2, z + d - 1, 3):
            for xx in (x, x + w - 1):
                v.fill(
                    xx,
                    base + 2,
                    wz,
                    xx + 1,
                    base + 4,
                    min(wz + 2, z + d - 1),
                    "light_blue_stained_glass",
                )
        # Fixed interior light and simple furnishings; no entities or tile-entity data.
        v.put(x + 1, base + 3, z + 1, "sea_lantern")
        if level == 0:
            v.put(x + w - 2, base + 1, z + d - 2, "crafting_table")
        if b.kind in ("apartment", "mixed_use") and level > 0:
            front_length = w if b.facing in ("north", "south") else d
            for along in range(1, front_length - 1):
                bx, bz = _front_point(b, along, 1)
                v.put(bx, base, bz, "smooth_stone_slab[type=top,waterlogged=false]")
                v.put(bx, base + 1, bz, "glass")
    # The door replaces a wall/window after all floors have been built.
    front_length = w if b.facing in ("north", "south") else d
    dx, dz = _front_point(b, front_length // 2)
    for half, y in (("lower", 4), ("upper", 5)):
        v.put(
            dx,
            y,
            dz,
            f"oak_door[facing={b.facing},half={half},hinge=left,open=false,powered=false]",
        )
    if b.kind in ("shop", "mixed_use"):
        for along in range(1, front_length - 1):
            ax, az = _front_point(b, along, 1)
            v.put(ax, 6, az, "red_concrete" if along % 2 else "white_concrete")
            ax, az = _front_point(b, along)
            v.put(ax, 7, az, "yellow_concrete" if b.kind == "shop" else "cyan_concrete")
    roof_y = 3 + b.floors * 4
    v.fill(x - 1, roof_y, z - 1, x + w + 1, roof_y + 1, z + d + 1, roof)
    if b.kind == "house" or b.palette == "traditional":
        layers = min(5, (d + 1) // 2)
        for layer in range(layers):
            low, high = z - 1 + layer, z + d - layer
            v.fill(x - 1, roof_y + layer, low, x + w + 1, roof_y + layer + 1, high + 1, roof)
            for xx in range(x - 1, x + w + 1):
                v.put(
                    xx,
                    roof_y + layer,
                    low,
                    "deepslate_tile_stairs[facing=south,half=bottom,shape=straight,waterlogged=false]",
                )
                v.put(
                    xx,
                    roof_y + layer,
                    high,
                    "deepslate_tile_stairs[facing=north,half=bottom,shape=straight,waterlogged=false]",
                )
    else:
        for xx in range(x, x + w):
            for zz in (z, z + d - 1):
                v.put(xx, roof_y + 1, zz, roof)
        for zz in range(z, z + d):
            for xx in (x, x + w - 1):
                v.put(xx, roof_y + 1, zz, roof)
        v.fill(x + 2, roof_y + 1, z + 2, x + 4, roof_y + 2, z + 4, "iron_block")


def _tree(v, x, z):
    v.fill(x, 4, z, x + 1, 9, z + 1, "oak_log[axis=y]")
    for yy, radius in ((7, 2), (8, 2), (9, 1), (10, 0)):
        for dx in range(-radius, radius + 1):
            for dz in range(-radius, radius + 1):
                if dx == dz == 0 and yy < 9:
                    continue
                if abs(dx) + abs(dz) <= radius + 1:
                    v.put(
                        x + dx,
                        yy,
                        z + dz,
                        "oak_leaves[distance=1,persistent=true,waterlogged=false]",
                    )


def build_city(city):
    if city.layout == "organic":
        from .organic_building import build_organic

        return build_organic(city)
    validate_city(city)
    size = city.size
    height = max(b.floors for b in city.buildings) * 4 + 12
    v = BlockVolume(size, height, size)
    v.fill(0, 0, 0, size, 1, size, "stone")
    v.fill(0, 1, 0, size, 3, size, "dirt")
    v.fill(0, 3, 0, size, 4, size, "grass_block[snowy=false]")
    sidewalks = mask(road_area(city), size)
    asphalt = mask(road_area(city, False), size)
    v.blocks[3, sidewalks] = v.state("smooth_stone")
    v.blocks[3, asphalt] = v.state("gray_concrete")
    # Dashes use the generated segment direction; they also work on tilted streets.
    for segment in city.roads:
        a, b = np.array(segment[0]), np.array(segment[-1])
        length = np.linalg.norm(b - a)
        for distance in np.arange(3, length - 3, 6):
            for delta in (0, 1):
                p = a + (b - a) * (distance + delta) / length
                x, z = np.floor(p).astype(int)
                if asphalt[z, x]:
                    v.put(x, 3, z, "white_concrete")
    junctions = {}
    for segment in city.roads:
        for point in (segment[0], segment[-1]):
            junctions[point] = junctions.get(point, 0) + 1
    # Small zebra crossings on every branch of each junction.
    for segment in city.roads:
        for origin, end in ((segment[0], segment[-1]), (segment[-1], segment[0])):
            if junctions[origin] < 3:
                continue
            a, b = np.array(origin), np.array(end)
            direction = (b - a) / np.linalg.norm(b - a)
            normal = np.array([-direction[1], direction[0]])
            for lateral in range(-city.plan.road_width // 2 + 1, city.plan.road_width // 2, 2):
                for delta in (0, 1):
                    p = a + direction * (city.plan.road_width / 2 + 2 + delta) + normal * lateral
                    x, z = np.floor(p).astype(int)
                    if 0 <= x < size and 0 <= z < size and asphalt[z, x]:
                        v.put(x, 3, z, "white_concrete")
    for district in city.districts:
        if district.kind != "park":
            continue
        area = Polygon(district.polygon).buffer(-(city.plan.road_width / 2 + 3))
        if area.is_empty:
            continue
        cx, cz = round(area.centroid.x), round(area.centroid.y)
        park_mask = mask(area, size)
        zz, xx = np.mgrid[0:size, 0:size]
        paths = park_mask & ((abs(xx - cx) <= 1) | (abs(zz - cz) <= 1))
        v.blocks[3, paths] = v.state("gravel")
        for z in range(math.ceil(area.bounds[1]) + 3, math.floor(area.bounds[3]) - 2, 8):
            for x in range(math.ceil(area.bounds[0]) + 3, math.floor(area.bounds[2]) - 2, 8):
                if area.covers(Point(x, z).buffer(3)) and abs(x - cx) > 3 and abs(z - cz) > 3:
                    _tree(v, x, z)
        # Benches at the plaza, entirely inside the park.
        for x in range(cx - 2, cx + 3):
            if area.covers(Point(x, cz + 3).buffer(1)):
                v.put(
                    x,
                    4,
                    cz + 3,
                    "oak_stairs[facing=south,half=bottom,shape=straight,waterlogged=false]",
                )
    for building in city.buildings:
        build_structure(v, building)
    # Lamps occupy sidewalks only, never the carriageway or a building lot.
    for z in range(14, size - 10, 20):
        for x in range(14, size - 10, 20):
            if sidewalks[z, x] and not asphalt[z, x]:
                v.fill(x, 4, z, x + 1, 8, z + 1, "iron_bars")
                v.put(x, 8, z, "sea_lantern")
    if city.railway_z is not None:
        z = city.railway_z
        v.fill(0, 9, z - 2, size, 10, z + 3, "stone_bricks")
        for x in range(size):
            for zz in (z - 1, z + 1):
                powered = x % 8 == 0
                v.put(
                    x,
                    10,
                    zz,
                    "powered_rail[powered=true,shape=east_west,waterlogged=false]"
                    if powered
                    else "rail[shape=east_west,waterlogged=false]",
                )
                if powered:
                    v.put(x, 9, zz, "redstone_block")
            for zz in (z - 2, z + 2):
                v.put(x, 10, zz, "iron_bars")
            if x % 12 == 0:
                v.fill(x, 4, z, x + 1, 9, z + 1, "stone_bricks")
    return v
