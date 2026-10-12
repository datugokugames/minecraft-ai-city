"""Small reusable, bounded exterior components; no external block datasets."""


def door(volume, x, y, z, facing="north"):
    for half, yy in (("lower", y), ("upper", y + 1)):
        volume.put(
            x, yy, z, f"oak_door[facing={facing},half={half},hinge=left,open=false,powered=false]"
        )


def canopy(volume, x0, z0, x1, z1, y):
    volume.fill(x0, y, z0, x1, y + 1, z1, "smooth_stone_slab[type=bottom,waterlogged=false]")


def window(volume, x0, z0, x1, z1, y, height=2):
    volume.fill(x0, y, z0, x1, y + height, z1, "light_blue_stained_glass")


def fascia(volume, points, y, accent="blue_concrete"):
    for i, (x, z) in enumerate(points):
        volume.put(x, y, z, accent if i % 3 == 0 else "white_concrete")


def light(volume, x, y, z):
    volume.put(x, y, z, "sea_lantern")


def balcony(volume, points, y):
    for x, z in points:
        volume.put(x, y, z, "smooth_stone_slab[type=top,waterlogged=false]")
        volume.put(x, y + 1, z, "glass")


def roof(volume, x, z, width, depth, y, style, material="deepslate_tiles"):
    volume.fill(x, y, z, x + width, y + 1, z + depth, material)
    if style == "gable":
        for i in range(min(5, (depth + 1) // 2)):
            if depth - 2 * i > 0:
                volume.fill(x, y + i, z + i, x + width, y + i + 1, z + depth - i, material)
    else:
        for xx in range(x, x + width):
            for zz in (z, z + depth - 1):
                volume.put(xx, y + 1, zz, material)
        for zz in range(z, z + depth):
            for xx in (x, x + width - 1):
                volume.put(xx, y + 1, zz, material)
        if style == "terrace":
            light(volume, x + 1, y + 1, z + 1)
