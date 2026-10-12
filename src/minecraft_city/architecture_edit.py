"""Collision-free infill adapter, shared by CLI, Web and Paper."""

import numpy as np
from shapely.geometry import LineString, Point, box
from shapely.ops import nearest_points
from shapely.strtree import STRtree

from .architecture import context_for, design, validate_request_design
from .architecture_geometry import render_architecture
from .edit_geometry import _constraints, _owner, _samples, resolve_area, validate_placement
from .edit_models import EditDelta, EditError
from .models import Building


def architecture_delta(project, intent, seed):
    req = intent.architecture
    if req is None:
        raise EditError("Architecture request missing")
    if project.city.layout != "organic":
        raise EditError(
            "自由建築の都市追加はorganic都市に対応します。単体生成はどのカテゴリでも可能です。"
        )
    area, physical = resolve_area(project, intent.selection)
    city = project.city
    forbidden, _, _, _ = _constraints(city)
    rng = np.random.default_rng(seed)
    candidates = [
        (line, t, width, side, None)
        for line, t, width, side in _samples(city, intent.selection, rng)
    ]
    if intent.selection.point is None:
        grid = [(x, z) for z in range(12, city.size - 12, 4) for x in range(12, city.size - 12, 4)]
        lines = [LineString(r) for r in city.roads]
        tree = STRtree(lines)
        for index in rng.permutation(len(grid)):
            center = np.array(grid[int(index)])
            point = Point(center)
            line_index = int(tree.nearest(point))
            line = lines[line_index]
            candidates.append(
                (line, line.project(point), city.road_specs[line_index].width, None, center)
            )
    for line, t, width, side, candidate_center in candidates:
        p = np.array(line.interpolate(t).coords[0])
        tangent = np.array(line.interpolate(min(line.length, t + 1)).coords[0]) - np.array(
            line.interpolate(max(0, t - 1)).coords[0]
        )
        tangent /= max(np.linalg.norm(tangent), 1e-9)
        if side is None:
            offset = (
                np.array(intent.selection.point if intent.selection.point else candidate_center) - p
            )
            side = 1 if np.dot(offset, [-tangent[1], tangent[0]]) >= 0 else -1
        normal = np.array([-tangent[1], tangent[0]]) * side
        facing = (
            ("west" if normal[0] > 0 else "east")
            if abs(normal[0]) > abs(normal[1])
            else ("north" if normal[1] > 0 else "south")
        )
        w, d = (req.depth, req.width) if facing in {"east", "west"} else (req.width, req.depth)
        center = (
            np.array(intent.selection.point)
            if intent.selection.point
            else candidate_center
            if candidate_center is not None
            else p + normal * (width / 2 + 6 + abs(normal[0]) * w / 2 + abs(normal[1]) * d / 2)
        )
        x, z = round(center[0] - w / 2), round(center[1] - d / 2)
        shape = box(x, z, x + w, z + d)
        envelope = shape.buffer(1.5, join_style="mitre")
        owner = _owner(envelope, physical)
        if owner is None or not area.covers(envelope) or forbidden.intersects(envelope):
            continue
        entrances = {
            "north": (x + w / 2, z),
            "south": (x + w / 2, z + d),
            "east": (x + w, z + d / 2),
            "west": (x, z + d / 2),
        }
        entrance = entrances[facing]
        road = nearest_points(Point(entrance), line)[1]
        access = LineString([entrance, road])
        try:
            validate_placement(city, shape, access, area)
        except EditError:
            continue
        context = context_for(project, x, z, owner)
        if req.design is None:
            spec, execution = design(req, seed, context, facing)
            req.design, req.execution = spec, execution
        else:
            spec = validate_request_design(req.design, req, seed, facing)
        render_architecture(spec)  # Check actual voxel count before materializing a proposal.
        kind = {
            "house": "house",
            "rental_apartment": "apartment",
            "apartment": "apartment",
            "office": "mixed_use",
            "commercial": "mixed_use",
            "convenience_store": "shop",
            "supermarket": "shop",
            "school": "school",
            "station": "station",
            "factory": "factory",
            "warehouse": "factory",
            "public": "mixed_use",
        }[req.category]
        b = Building(
            id=max((b.id for b in city.buildings), default=-1) + 1,
            district_id=owner,
            kind=kind,
            x=x,
            z=z,
            width=w,
            depth=d,
            floors=spec.floors,
            facing=facing,
            palette="modern",
            footprint=list(shape.exterior.coords),
            entrance=entrance,
            access_path=list(access.coords),
            architecture=spec,
        )
        return EditDelta(added_buildings=[b])
    raise EditError(
        f"敷地{req.width}×{req.depth}と周囲1.5ブロックの余白を収め、道路・河川・既存敷地を避けられる場所がありません。代替案：18×22へ縮小するか別の範囲を選択してください。寸法は自動縮小しません。"
    )
