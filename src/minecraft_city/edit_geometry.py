"""Select existing administrative areas and find collision-free road-fronting infill."""

from __future__ import annotations

import math

import numpy as np
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import nearest_points, unary_union
from shapely.prepared import prep

from .edit_models import BuildingChange, ClarificationRequired, EditDelta, EditError
from .generation import building_area, road_area
from .models import Building, Site
from .organic import river_shape


def resolve_area(project, selection):
    city = project.city
    boundary = box(0, 0, city.size, city.size)
    if selection.city_name is not None and selection.city_name != project.city_name:
        raise ClarificationRequired(f"都市名が一致しません: {selection.city_name}")
    wards = project.wards
    if selection.ward is not None:
        wards = [w for w in wards if selection.ward in (w.id, w.name)]
        if len(wards) != 1:
            raise ClarificationRequired("区名・区IDを一意に指定してください。")
    districts = [d for d in project.districts if d.ward_id in {w.id for w in wards}]
    if selection.district is not None:
        districts = [d for d in districts if selection.district in (d.id, d.name)]
        if len(districts) != 1:
            raise ClarificationRequired(
                "地区名が不明または重複しています。区名または地区IDも指定してください。"
            )
    ids = {d.city_district_id for d in districts if d.city_district_id is not None}
    if not ids:
        raise EditError("対象地区は未開発です。Phase 3Aでは既存地区への追加のみ対応しています。")
    physical = {d.id: Polygon(d.polygon) for d in city.districts if d.id in ids}
    area = (
        unary_union(list(physical.values())) if selection.ward or selection.district else boundary
    )
    if selection.bounds:
        rectangle = box(*selection.bounds)
        if not boundary.covers(rectangle):
            raise EditError("指定範囲が都市の外にはみ出しています。")
        area = area.intersection(rectangle)
    if selection.point and not area.covers(Point(selection.point)):
        raise EditError("指定座標が都市・選択地区・範囲の外です。")
    if selection.station_side:
        stations = [b for b in city.buildings if b.kind == "station"]
        if len(stations) != 1:
            raise ClarificationRequired(
                "駅を一意に特定できません。座標または範囲を指定してください。"
            )
        east = building_area(stations[0]).bounds[2]
        area = area.intersection(box(east, 0, city.size, city.size))
        if selection.point and not area.covers(Point(selection.point)):
            raise EditError("指定座標は駅の東側ではありません。")
    if area.is_empty:
        raise EditError("条件を満たす選択範囲がありません。")
    if not any(value is not None for value in selection.model_dump().values()):
        raise ClarificationRequired("都市名・区・地区・座標・範囲のいずれかを指定してください。")
    return area, physical


def _barriers(city):
    water = river_shape(city) if city.layout == "organic" else Polygon()
    rail = (
        box(0, city.railway_z - 5, city.size, city.railway_z + 5)
        if city.railway_z is not None
        else Polygon()
    )
    return water, rail


def _constraints(city):
    water, rail = _barriers(city)
    bodies = [building_area(b).buffer(1.5, join_style="mitre") for b in city.buildings]
    sites = [Polygon(s.polygon).buffer(1, join_style="mitre") for s in city.sites]
    paths = [
        LineString(item.access_path).buffer(1)
        for item in [*city.buildings, *city.sites]
        if item.access_path
    ]
    occupied = unary_union([*bodies, *sites])
    forbidden = unary_union([road_area(city), water.buffer(1), rail, occupied, *paths])
    return prep(forbidden), occupied, water, rail


def validate_placement(city, shape, access, area):
    """Validate physical clearance and an independent, traversable road connection."""
    envelope = shape.buffer(1.5, join_style="mitre")
    if not box(1, 1, city.size - 1, city.size - 1).covers(envelope):
        raise EditError("建築物・敷地が都市境界を越えます。")
    if not area.covers(envelope):
        raise EditError("建築物・敷地が指定範囲を越えます。")
    forbidden, occupied, water, rail = _constraints(city)
    if forbidden.intersects(envelope):
        raise EditError("既存建物・道路・鉄道・河川・敷地・接道通路と衝突します。")
    if not 0.5 <= access.length <= 20:
        raise EditError("接道通路は0.5〜20ブロック以内である必要があります。")
    corridor = access.buffer(0.9)
    if not box(0, 0, city.size, city.size).covers(corridor) or corridor.intersects(
        unary_union([occupied, water, rail])
    ):
        raise EditError("安全な接道通路を確保できません。")
    lines = unary_union([LineString(r) for r in city.roads])
    if (
        Point(access.coords[0]).distance(shape.boundary) > 0.1
        or Point(access.coords[-1]).distance(lines) > 0.1
    ):
        raise EditError("敷地から道路までの接続が不正です。")


def _samples(city, selection, rng):
    lines = [LineString(r) for r in city.roads]
    if selection.point:
        center = Point(selection.point)
        order = sorted(range(len(lines)), key=lambda i: lines[i].distance(center))[:4]
        for i in order:
            line = lines[i]
            t = line.project(center)
            yield line, t, city.road_specs[i].width, None
        return
    candidates = [
        (line, float(t), spec.width, side)
        for line, spec in zip(lines, city.road_specs)
        for t in np.arange(5, line.length - 4, 3)
        for side in (-1, 1)
    ]
    for i in rng.permutation(len(candidates)):
        yield candidates[int(i)]


def _owner(shape, physical):
    candidates = [i for i, region in physical.items() if region.covers(shape)]
    return min(candidates) if candidates else None


def find_building(city, intent, area, physical, rng, next_id):
    kind = intent.building_type
    dimensions = {
        "convenience_store": [(10, 8), (8, 8), (7, 7)],
        "house": [(8, 9), (7, 7)],
        "apartment": [(10, 10), (8, 9)],
    }[kind]
    forbidden, occupied, water, rail = _constraints(city)
    world = box(1, 1, city.size - 1, city.size - 1)
    last_error = "建築可能な接道空地がありません。"
    for line, t, width, side in _samples(city, intent.selection, rng):
        p = np.array(line.interpolate(t).coords[0])
        tangent = np.array(line.interpolate(min(line.length, t + 1)).coords[0]) - np.array(
            line.interpolate(max(0, t - 1)).coords[0]
        )
        tangent /= max(1e-9, np.linalg.norm(tangent))
        if side is None:
            offset = np.array(intent.selection.point) - p
            side = 1 if np.dot(offset, [-tangent[1], tangent[0]]) >= 0 else -1
        normal = np.array([-tangent[1], tangent[0]]) * side
        for w, d in dimensions:
            center = (
                np.array(intent.selection.point)
                if intent.selection.point
                else p + normal * (width / 2 + 1.5 + 3.2 + d / 2)
            )
            x, z = round(center[0] - w / 2), round(center[1] - d / 2)
            center = np.array([x + w / 2, z + d / 2])
            angle = math.degrees(math.atan2(tangent[1], tangent[0])) + (180 if side < 0 else 0)
            shape = affinity.rotate(box(x, z, x + w, z + d), angle)
            envelope = shape.buffer(1.5, join_style="mitre")
            owner = _owner(envelope, physical)
            if (
                owner is None
                or not area.covers(envelope)
                or not world.covers(envelope)
                or forbidden.intersects(envelope)
            ):
                continue
            entrance = center - normal * d / 2
            access = LineString([entrance, p])
            if not 0.5 <= access.length <= 20 or access.buffer(0.9).intersects(
                unary_union([occupied, water, rail])
            ):
                continue
            try:
                validate_placement(city, shape, access, area)
            except EditError as exc:
                last_error = str(exc)
                continue
            return Building(
                id=next_id,
                district_id=owner,
                kind="shop" if kind == "convenience_store" else kind,
                x=x,
                z=z,
                width=w,
                depth=d,
                floors={
                    "house": 2,
                    "apartment": min(4, city.plan.max_floors),
                    "convenience_store": 1,
                }[kind],
                facing="north",
                palette=intent.palette or ("modern" if kind != "house" else "warm"),
                angle=angle,
                footprint=list(shape.exterior.coords),
                entrance=tuple(entrance),
                access_path=list(access.coords),
                detail="convenience_store" if kind == "convenience_store" else None,
            )
    raise EditError(
        f"{last_error} 衝突または面積・接道条件のため配置できません。範囲を変更してください。"
    )


def find_park(city, selection, area, physical, rng):
    if selection.bounds:
        # Keep a 2-block buffer for park borders/vegetation within the requested rectangle.
        shapes = [box(*selection.bounds).buffer(-2, join_style="mitre")]
    elif selection.point:
        x, z = selection.point
        shapes = [box(x - 6, z - 6, x + 6, z + 6)]
    else:
        shapes = []
        for line, t, width, side in _samples(city, selection, rng):
            p = np.array(line.interpolate(t).coords[0])
            tangent = np.array(line.interpolate(min(line.length, t + 1)).coords[0]) - np.array(
                line.interpolate(max(0, t - 1)).coords[0]
            )
            tangent /= max(1e-9, np.linalg.norm(tangent))
            x, z = p + np.array([-tangent[1], tangent[0]]) * side * (width / 2 + 13)
            shapes.append(box(x - 5, z - 5, x + 5, z + 5))
    roads = unary_union([LineString(r) for r in city.roads])
    for shape in shapes:
        if shape.is_empty or shape.area < 49:
            continue
        owner = _owner(shape.buffer(1.5, join_style="mitre"), physical)
        if owner is None:
            continue
        start, end = nearest_points(shape, roads)
        path = LineString([start, end])
        try:
            validate_placement(city, shape, path, area)
        except EditError:
            continue
        return Site(
            kind="park", polygon=list(shape.exterior.coords), access_path=list(path.coords)
        ), owner
    raise EditError("公園の面積・接道・衝突条件を満たす場所がありません。")


def compute_delta(project, intent, seed):
    area, physical = resolve_area(project, intent.selection)
    city = project.city
    if intent.action == "restyle":
        candidates = [b for b in city.buildings if b.district_id in physical]
        if intent.selection.building_id is not None:
            candidates = [b for b in candidates if b.id == intent.selection.building_id]
        if intent.selection.point:
            candidates = [
                b for b in candidates if building_area(b).covers(Point(intent.selection.point))
            ]
        candidates = [b for b in candidates if area.covers(building_area(b))]
        if len(candidates) != 1:
            raise ClarificationRequired(
                "外観を変更する建物を1棟だけ指定してください。--building-id を使用できます。"
            )
        before = candidates[0]
        if before.kind in {"school", "factory", "station"}:
            raise EditError("学校・工場・駅の専用外観変更はPhase 3Aでは未対応です。")
        after = before.model_copy(
            update={"palette": intent.palette, "variant": 0, "detail": None}, deep=True
        )
        if before == after:
            raise EditError("指定した建物は既にその外観です。変更はありません。")
        return EditDelta(changed_buildings=[BuildingChange(before=before, after=after)])
    if city.layout != "organic":
        raise EditError(
            "建物・公園の追加はorganic都市に対応しています。legacy都市は外観変更のみ対応です。"
        )
    rng = np.random.default_rng(seed)
    if intent.action == "add_park":
        site, owner = find_park(city, intent.selection, area, physical, rng)
        return EditDelta(added_sites=[site], site_district_ids=[owner])
    working = city.model_copy(deep=True)
    next_id = max((b.id for b in city.buildings), default=-1) + 1
    added = []
    for i in range(intent.count):
        building = find_building(working, intent, area, physical, rng, next_id + i)
        working.buildings.append(building)
        added.append(building)
    return EditDelta(added_buildings=added)
