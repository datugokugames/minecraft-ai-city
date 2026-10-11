import nbtlib
import numpy as np
import pytest

from minecraft_city.building import build_city
from minecraft_city.schematic import BlockVolume, write_schematic


def decode_varints(data):
    """Independent decoder, deliberately not shared with the production encoder."""
    result, current, shift = [], 0, 0
    for byte in data:
        byte = int(byte) & 255
        current |= (byte & 127) << shift
        if byte & 128:
            shift += 7
            assert shift <= 28
        else:
            result.append(current)
            current, shift = 0, 0
    assert shift == 0
    return np.array(result)


def test_roundtrip_and_axis_order(tmp_path):
    volume = BlockVolume(3, 4, 5)
    volume.put(2, 1, 4, "gold_block")
    path = tmp_path / "test.schem"
    write_schematic(volume, path)
    root = nbtlib.load(path)
    assert root.root_name == "Schematic"
    assert root["Version"] == 2
    assert root["DataVersion"] == 3465
    assert list(root["Offset"]) == [0, 0, 0]
    data = decode_varints(root["BlockData"])
    assert len(data) == 3 * 4 * 5
    assert data[2 + 4 * 3 + 1 * 3 * 5] == root["Palette"]["minecraft:gold_block"]
    assert data.sum() == 1


def test_multibyte_palette_and_deterministic_gzip(tmp_path):
    volume = BlockVolume(140, 1, 1)
    # Synthetic names are only an encoding fixture, never exported by the actual builder.
    for x in range(140):
        volume.put(x, 0, 0, f"encoding_fixture_{x}")
    a, b = tmp_path / "a.schem", tmp_path / "b.schem"
    write_schematic(volume, a)
    write_schematic(volume, b)
    assert a.read_bytes() == b.read_bytes()
    root = nbtlib.load(a)
    assert len(root["BlockData"]) > 140
    assert np.array_equal(decode_varints(root["BlockData"]), np.arange(1, 141))


def test_full_city_build_roundtrip(city, tmp_path):
    volume = build_city(city)
    path = tmp_path / "city.schem"
    metadata = write_schematic(volume, path)
    root = nbtlib.load(path)
    decoded = decode_varints(root["BlockData"])
    assert np.array_equal(decoded.reshape(volume.blocks.shape), volume.blocks)
    assert metadata["width"] == metadata["length"] == 256
    assert decoded.max() < root["PaletteMax"]
    assert len(root["Palette"]) == root["PaletteMax"]
    for detail in (
        "oak_door[",
        "light_blue_stained_glass",
        "rail[",
        "oak_leaves[",
        "deepslate_tile_stairs[",
        "sea_lantern",
    ):
        assert any(detail in block for block in volume.palette)
    for building in city.buildings:
        assert volume.blocks[4, building.z + 1, building.x + 1] == 0  # Hollow interior.
    assert np.count_nonzero(volume.blocks) > 256 * 256 * 4


def test_builder_bounds():
    v = BlockVolume(8, 8, 8)
    with pytest.raises(ValueError, match="bounds"):
        v.put(-1, 0, 0, "stone")
    with pytest.raises(ValueError, match="bounds"):
        v.fill(0, 0, 0, 9, 8, 8, "stone")
