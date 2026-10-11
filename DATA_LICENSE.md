# Data attribution and licensing

© OpenStreetMap contributors. https://www.openstreetmap.org/copyright

OSM data is available under the Open Database License (ODbL) v1.0:
https://opendatacommons.org/licenses/odbl/1-0/

The following are distributed under ODbL 1.0, separately from the MIT-licensed code:

- `examples/osm/raw/*.json`: bounded public Overpass responses and retrieval timestamps.
- `examples/osm/tokyo.json`, `kyoto.json`, `comparison.json`: aggregate analysis of those responses.
- OSM-derived metric/source fields in `examples/japan-city/city.json` and `report.json`.
- `examples/osm-v2/raw/*.json` and the v0.2 profiles/comparison in `examples/osm-v2/`:
  the expanded road, river, railway, and elevation-tag query over the same boxes.
- OSM-derived metric/source fields in `examples/natural-city/` and
  `examples/natural-comparison/`.
- The same OSM-derived fields embedded in `examples/management/`,
  `examples/managed-city/` and `examples/phase2-comparison/` (v0.2.1).
- The same retained OSM-derived fields in `examples/phase3a/` editing plans,
  snapshots, reports and project files (v0.3.0). Original source data is unchanged.

Downloaded on 2026-10-11 using https://overpass-api.de/api/interpreter.
The region names identify only these small bounding boxes, not whole-city statistics:

| Region | south | west | north | east |
| --- | --- | --- | --- | --- |
| Tokyo | 35.705 | 139.789 | 35.711 | 139.797 |
| Kyoto | 35.001 | 135.758 | 35.007 | 135.766 |

The exact extraction and transformation process is provided in
`src/minecraft_city/analysis.py`. Raw responses are supplied for reproducibility
and the current responses are directly usable with
`citygen analyze --cache-dir examples/osm-v2/raw`.
The older `examples/osm/raw/` files retain the original v0.1 query responses;
they do not include the extra v0.2 barrier/elevation query and have different cache keys.
Do not remove attribution or represent these data as an original proprietary database.
If distributing an adapted database, comply with ODbL's attribution, share-alike,
and availability requirements.

Generated geometry is procedural and does not reuse source map coordinates.
For published maps, previews, or Minecraft cities based on the OSM profiles,
retain the visible attribution and a link to the copyright/license page.
The tool writes attribution into profile JSON, city JSON, reports, PNG previews,
and schematic metadata. A published Minecraft world should additionally display
attribution in its description/documentation because schematic metadata is not
normally visible in-game.

`examples/demo/` is based on project-generated synthetic shapes and contains
no measured OSM city geometry or statistics. It is labeled accordingly.
