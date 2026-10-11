"""Sponge Schematic v2 writer with nbtlib; Minecraft 1.20.1 block states.

BlockData uses unsigned VarInts and x + z*Width + y*Width*Length ordering.
The NBT reader in the tests independently decodes and verifies the output.
"""

from __future__ import annotations

import gzip
import io
from pathlib import Path

import nbtlib
import numpy as np


class BlockVolume:
    def __init__(self, width: int, height: int, length: int):
        self.blocks = np.zeros((height, length, width), dtype=np.uint16)
        self.palette = {"minecraft:air": 0}

    def state(self, name):
        name = name if ":" in name else "minecraft:" + name
        if name not in self.palette:
            if len(self.palette) >= 65536:
                raise ValueError("palette exceeds uint16 capacity")
            self.palette[name] = len(self.palette)
        return self.palette[name]

    def put(self, x, y, z, block):
        height, length, width = self.blocks.shape
        if not (0 <= x < width and 0 <= z < length and 0 <= y < height):
            raise ValueError(f"Block out of bounds: {(x, y, z)}")
        self.blocks[y, z, x] = self.state(block)

    def fill(self, x0, y0, z0, x1, y1, z1, block):
        """Half-open bounds, matching NumPy slices."""
        height, length, width = self.blocks.shape
        if not (0 <= x0 <= x1 <= width and 0 <= y0 <= y1 <= height and 0 <= z0 <= z1 <= length):
            raise ValueError("Fill out of bounds")
        self.blocks[y0:y1, z0:z1, x0:x1] = self.state(block)


def encode_varints(values):
    values = np.asarray(values).ravel()
    if values.size == 0:
        return b""
    if np.any(values < 0):
        raise ValueError("palette indices cannot be negative")
    if values.max() < 128:
        return values.astype(np.uint8).tobytes()
    encoded = bytearray()
    for number in values:
        number = int(number)
        while number >= 128:
            encoded.append((number & 127) | 128)
            number >>= 7
        encoded.append(number)
    return bytes(encoded)


def write_schematic(volume: BlockVolume, path: str | Path, name="Original city", attribution=""):
    height, length, width = volume.blocks.shape
    tags = nbtlib.tag
    root = nbtlib.File(
        {
            "Version": tags.Int(2),
            "DataVersion": tags.Int(3465),
            "Width": tags.Short(width),
            "Height": tags.Short(height),
            "Length": tags.Short(length),
            "Offset": tags.IntArray([0, 0, 0]),
            "Metadata": tags.Compound(
                {
                    "Name": tags.String(name),
                    "Author": tags.String("minecraft-ai-city"),
                    "Attribution": tags.String(attribution),
                }
            ),
            "PaletteMax": tags.Int(len(volume.palette)),
            "Palette": tags.Compound(
                {block: tags.Int(index) for block, index in volume.palette.items()}
            ),
            "BlockData": tags.ByteArray(
                np.frombuffer(encode_varints(volume.blocks), dtype=np.int8)
            ),
            "BlockEntities": tags.List[tags.Compound]([]),
            "Entities": tags.List[tags.Compound]([]),
        },
        root_name="Schematic",
    )
    raw = io.BytesIO()
    root.write(raw)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Fixed gzip metadata makes identical seeds/plans byte-for-byte reproducible.
    with (
        path.open("wb") as output,
        gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0) as compressed,
    ):
        compressed.write(raw.getvalue())
    return {
        "format": "Sponge Schematic v2",
        "minecraft_data_version": 3465,
        "width": width,
        "height": height,
        "length": length,
        "palette_size": len(volume.palette),
        "non_air_blocks": int(np.count_nonzero(volume.blocks)),
        "compressed_bytes": path.stat().st_size,
    }
