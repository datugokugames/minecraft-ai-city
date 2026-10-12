"""Voxel geometry from bounded masses: shells, floors, fenestration and exterior details."""

import numpy as np
from PIL import Image, ImageDraw

from . import architecture_parts as parts
from .architecture_models import ArchitectureSpec
from .schematic import BlockVolume

MAX_SOLID_BLOCKS = 50000


def render_architecture(spec, max_blocks=MAX_SOLID_BLOCKS):
    spec = ArchitectureSpec.model_validate(spec.model_dump())
    w, d = spec.site_width, spec.site_depth
    v = BlockVolume(w, spec.height + 1, d)
    v.fill(0, 0, 0, w, 1, d, "grass_block[snowy=false]")
    masks = []
    for level in range(spec.floors):
        footprint = np.zeros((d, w), dtype=bool)
        for mass in spec.masses:
            if mass.floors > level:
                footprint[mass.z : mass.z + mass.depth, mass.x : mass.x + mass.width] = True
        masks.append(footprint)
        y = 1 + level * spec.story_height
        interior = (
            footprint
            & np.roll(footprint, 1, 0)
            & np.roll(footprint, -1, 0)
            & np.roll(footprint, 1, 1)
            & np.roll(footprint, -1, 1)
        )
        boundary = footprint & ~interior
        v.blocks[y, footprint] = v.state("smooth_stone")
        for yy in range(y + 1, y + spec.story_height):
            v.blocks[yy, boundary] = v.state(spec.wall)
        # Windows keep fixed block dimensions; larger facades add bays instead of scaling.
        for z, x in np.argwhere(boundary):
            horizontal = not footprint[z - 1, x] or not footprint[z + 1, x]
            vertical = not footprint[z, x - 1] or not footprint[z, x + 1]
            along = x if horizontal else z
            corner = horizontal and vertical
            if not corner and along % spec.windows.spacing < spec.windows.width:
                v.blocks[y + 2 : y + 2 + spec.windows.height, z, x] = v.state(
                    "light_blue_stained_glass"
                )
            elif along % spec.windows.spacing == spec.windows.spacing - 1:
                v.blocks[y + 1 : y + spec.story_height, z, x] = v.state(spec.accent)
        # Roofs on each exposed lower wing; higher masses remain structurally independent.
        above = np.zeros_like(footprint)
        for mass in spec.masses:
            if mass.floors > level + 1:
                above[mass.z : mass.z + mass.depth, mass.x : mass.x + mass.width] = True
        roofmask = footprint & ~above
        roof_y = y + spec.story_height
        v.blocks[roof_y, roofmask] = v.state(spec.roof_material)
        if spec.roof in {"terrace", "flat"}:
            rim = roofmask & (
                ~np.roll(roofmask, 1, 0)
                | ~np.roll(roofmask, -1, 0)
                | ~np.roll(roofmask, 1, 1)
                | ~np.roll(roofmask, -1, 1)
            )
            top = spec.height if level == spec.floors - 1 else roof_y + 1
            for ry in range(roof_y + 1, top + 1):
                v.blocks[ry, rim] = v.state(spec.accent)
        elif spec.roof == "gable":
            for mass in spec.masses:
                if mass.floors != level + 1:
                    continue
                # Two sloping planes, with bounded ridge rise and one-block modules.
                for x in range(mass.x, mass.x + mass.width):
                    rise = round(
                        min(x - mass.x, mass.x + mass.width - 1 - x)
                        / max(1, (mass.width - 1) // 2)
                        * (spec.height - roof_y)
                    )
                    v.fill(
                        x,
                        roof_y,
                        mass.z,
                        x + 1,
                        roof_y + rise + 1,
                        mass.z + mass.depth,
                        spec.roof_material,
                    )
        if spec.balconies and level > 0:
            for z, x in np.argwhere(boundary):
                if not footprint[z - 1, x] and x % spec.windows.spacing < spec.windows.width:
                    v.put(x, y, z - 1, "smooth_stone_slab[type=top,waterlogged=false]")
                    v.put(x, y + 1, z - 1, "glass")
    front = min(m.z for m in spec.masses)
    ex = spec.entrance_x
    # Door pair, sheltered path and generous hollow entrance. No furniture or block entities.
    parts.door(v, ex, 2, front)
    v.fill(ex, 0, 0, ex + 1, 1, front, "smooth_stone")
    v.fill(min(ex, w // 2), 0, 0, max(ex, w // 2) + 1, 1, 1, "smooth_stone")
    v.put(ex, 1, front - 1, "oak_stairs[facing=south,half=bottom,shape=straight,waterlogged=false]")
    if spec.category in {"convenience_store", "supermarket", "commercial", "station"}:
        for x in range(spec.masses[0].x + 1, spec.masses[0].x + spec.masses[0].width - 1):
            if abs(x - ex) > 1:
                v.fill(x, 2, front, x + 1, 4, front + 1, "glass")
    if spec.canopy:
        v.fill(
            max(1, ex - 2),
            4,
            front - 1,
            min(w - 1, ex + 3),
            5,
            front,
            "smooth_stone_slab[type=bottom,waterlogged=false]",
        )
    if spec.signage:
        # Unbranded coloured fascia; no sign NBT or generated third-party logo.
        for x in range(spec.masses[0].x + 1, spec.masses[0].x + spec.masses[0].width - 1):
            v.put(x, 5, front, "blue_concrete" if (x + spec.seed) % 3 == 0 else "white_concrete")
        if spec.category == "station":
            v.put(ex, 5, front, "sea_lantern")
            v.put(ex, 6, front, "black_concrete")
    if spec.parking and front >= 4:
        for x in range(2, w - 2):
            if abs(x - ex) <= 1:
                continue
            v.fill(x, 0, 1, x + 1, 1, front - 1, "gray_concrete")
            if x % 4 == 0:
                v.fill(x, 0, 1, x + 1, 1, front - 1, "white_concrete")
    if spec.planting:
        for x in (1, w - 2):
            for z in range(2, d - 2, 5):
                v.put(x, 1, z, "oak_leaves[distance=1,persistent=true,waterlogged=false]")
    if spec.stairs:
        m = spec.masses[0]
        if m.depth < spec.story_height + 3:
            raise ValueError(
                "階段の長さが不足しています。stairs=falseまたは奥行きを増やしてください。"
            )
        for level in range(m.floors - 1):
            base = 1 + level * spec.story_height
            reverse = level % 2
            for step in range(spec.story_height):
                z = m.z + 2 + (spec.story_height - 1 - step if reverse else step)
                x = m.x + 2 + reverse
                v.fill(
                    x,
                    base + step + 1,
                    z,
                    x + 1,
                    min(base + spec.story_height + 3, spec.height),
                    z + 1,
                    "air",
                )
                v.put(
                    x,
                    base + step + 1,
                    z,
                    "oak_stairs[facing="
                    + ("north" if reverse else "south")
                    + ",half=bottom,shape=straight,waterlogged=false]",
                )
    count = int(np.count_nonzero(v.blocks))
    if count > max_blocks:
        raise ValueError(
            f"外装{count}ブロックは上限{max_blocks}を超えます。代替案：敷地18×22、高さ10、または建物の階数を減らしてください。"
        )
    return v


def paste_architecture(volume, building):
    import re

    spec = building.architecture
    local = render_architecture(spec)
    quarter = {"north": 0, "east": 1, "south": 2, "west": 3}[building.facing]
    blocks = np.rot90(local.blocks, k=-quarter, axes=(1, 2))
    compass = ["north", "east", "south", "west"]
    remap = np.array(
        [
            volume.state(
                re.sub(
                    r"facing=(north|east|south|west)",
                    lambda m: "facing=" + compass[(compass.index(m[1]) + quarter) % 4],
                    state,
                )
            )
            for state in local.palette
        ],
        dtype=np.uint16,
    )
    h, d, w = blocks.shape
    volume.blocks[3 : 3 + h, building.z : building.z + d, building.x : building.x + w] = remap[
        blocks
    ]


def render_isometric(volume, path):
    """Three exposed voxel faces, orthographic isometric projection; standalone PNG."""
    a = volume.blocks[:, ::-1, ::-1]  # Camera looks at the north-facing entrance.
    h, d, w = a.shape
    scale = max(3, min(8, 640 // max(w, d)))
    width = (w + d) * scale + 80
    height = (w + d) * scale // 2 + h * scale + 80
    image = Image.new("RGB", (width, height), "#edf1ef")
    draw = ImageDraw.Draw(image)
    names = list(volume.palette)
    colours = {
        "white": (226, 228, 223),
        "gray": (145, 152, 158),
        "glass": (126, 187, 201),
        "blue": (89, 136, 166),
        "sandstone": (213, 192, 154),
        "bricks": (176, 115, 95),
        "oak": (145, 117, 78),
        "grass": (135, 159, 107),
        "leaves": (96, 138, 84),
        "deepslate": (84, 93, 105),
    }

    def colour(index):
        name = names[index]
        return next((c for k, c in colours.items() if k in name), (169, 170, 161))

    def point(x, y, z):
        return (40 + d * scale + (x - z) * scale, 40 + h * scale + (x + z) * scale / 2 - y * scale)

    # Face culling keeps even large hollow buildings inexpensive to draw.
    for z in range(d):
        for x in range(w):
            for y in np.flatnonzero(a[:, z, x]):
                c = colour(int(a[y, z, x]))
                for visible, vertices, shade in (
                    (
                        y == h - 1 or a[y + 1, z, x] == 0,
                        [
                            (x, y + 1, z),
                            (x + 1, y + 1, z),
                            (x + 1, y + 1, z + 1),
                            (x, y + 1, z + 1),
                        ],
                        1.08,
                    ),
                    (
                        x == w - 1 or a[y, z, x + 1] == 0,
                        [
                            (x + 1, y, z),
                            (x + 1, y + 1, z),
                            (x + 1, y + 1, z + 1),
                            (x + 1, y, z + 1),
                        ],
                        0.78,
                    ),
                    (
                        z == d - 1 or a[y, z + 1, x] == 0,
                        [
                            (x, y, z + 1),
                            (x + 1, y, z + 1),
                            (x + 1, y + 1, z + 1),
                            (x, y + 1, z + 1),
                        ],
                        0.92,
                    ),
                ):
                    if visible:
                        draw.polygon(
                            [point(*v) for v in vertices],
                            fill=tuple(min(255, int(channel * shade)) for channel in c),
                        )
    image.save(path)
