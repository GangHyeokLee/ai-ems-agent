"""Build South Korean land polygons (display only) from OSM natural=coastline. Offline data tool.

The OSM country relation (admin_level=2) includes straight baselines and the 12 nautical-mile
territorial sea, so it is a jurisdiction boundary, not a coastline. This tool assembles the
actual OSM coastline (land on the left of each way), keeps land faces, and clips them to the
same OSM country relation so that the land border (DMZ/MDL) and island ownership follow the
OSM source. Transmission data are not read or modified.

Requires osmium, shapely and pyproj (for example the sibling korea-grid-poc virtualenv):

    ../korea-grid-poc/.venv/bin/python scripts/build_kr_land_boundary.py \\
        --pbf ../korea-grid-poc/data/raw/south-korea-261020.osm.pbf \\
        --output data/grids/kr-land-boundary.json

Optional independent comparison with Natural Earth 1:10m land (public domain):

    ... --natural-earth /path/to/ne_10m_land.shp --comparison-output outputs/kr_coastline/comparison.json
"""
import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import osmium
import shapely
from pyproj import Transformer
from shapely import from_wkb, line_merge, polygonize, STRtree
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon, mapping, shape
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[1]
COUNTRY_RELATION = 307756  # OSM "대한민국" admin_level=2, ISO3166-1=KR
# Equal-area projection centred on Korea for areas, simplification and distance metrics.
LAEA = "+proj=laea +lat_0=36 +lon_0=127.5 +datum=WGS84 +units=m +no_defs"
CHECK_ISLANDS = {  # known land points used only to report the matching polygon, never to draw
    "jeju": (126.53, 33.36), "ulleungdo": (130.87, 37.50),
    "dokdo_dongdo": (131.8696, 37.2412), "dokdo_seodo": (131.8650, 37.2420),
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class Coastline(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.lines = []
        self.invalid = 0
        self.country = None
        self.factory = osmium.geom.WKBFactory()

    def way(self, obj):
        if obj.tags.get("natural") != "coastline":
            return
        try:
            self.lines.append(LineString([(n.lon, n.lat) for n in obj.nodes]))
        except (osmium.InvalidLocationError, ValueError):
            self.invalid += 1

    def area(self, obj):
        if not obj.from_way() and obj.orig_id() == COUNTRY_RELATION:
            self.country = from_wkb(self.factory.create_multipolygon(obj))


def polygons_of(geom):
    if geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    return [g for part in getattr(geom, "geoms", []) for g in polygons_of(part)]


def assemble_land(lines, country):
    """Coastline faces with land on the left; closed rings by orientation, open lines by side votes."""
    merged = line_merge(MultiLineString(lines), directed=True)
    merged = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
    closed = [m for m in merged if m.is_ring and len(m.coords) >= 4]
    open_lines = [m for m in merged if not (m.is_ring and len(m.coords) >= 4)]
    islands, water = [], []
    for ring in closed:
        poly = Polygon(ring.coords)
        (islands if poly.exterior.is_ccw else water).append(poly.buffer(0))
    # Open coastline (mainland, rings cut by the extract) is closed by the country relation.
    noded = unary_union([country.boundary, *open_lines])
    faces = [f for f in polygonize(list(getattr(noded, "geoms", [noded]))).geoms
             if country.contains(f.representative_point())]
    tree, votes = STRtree(faces), [0] * len(faces)
    eps = 1e-6
    for line in open_lines:
        coords = list(line.coords)
        step = max(1, len(coords) // 400)
        for i in range(0, len(coords) - 1, step):
            (x1, y1), (x2, y2) = coords[i], coords[i + 1]
            length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
            if not length:
                continue
            mx, my, nx, ny = (x1 + x2) / 2, (y1 + y2) / 2, -(y2 - y1) / length, (x2 - x1) / length
            for sign in (1, -1):  # left of the way is land (+1), right is water (-1)
                point = Point(mx + sign * eps * nx, my + sign * eps * ny)
                for index in tree.query(point, predicate="within"):
                    votes[index] += sign
    mainland = [f for f, v in zip(faces, votes) if v > 0]
    land = unary_union(mainland + islands)
    if water:
        land = land.difference(unary_union(water))
    stats = dict(coastline_closed_rings=len(closed), coastline_open_lines=len(open_lines),
                 ccw_island_rings=len(islands), cw_water_rings=len(water), open_faces=len(faces),
                 open_land_faces=len(mainland), unvoted_faces=sum(1 for v in votes if v == 0))
    return land.intersection(country), stats


def project(geom, transformer):
    return shapely.transform(geom, lambda xy: np.column_stack(transformer.transform(xy[:, 0], xy[:, 1])))


def area_km2(geom, to_m):
    return project(geom, to_m).area / 1e6


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pbf", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "data/grids/kr-land-boundary.json")
    parser.add_argument("--simplify-m", type=float, default=50.0, help="display simplification tolerance (m)")
    parser.add_argument("--min-area-m2", type=float, default=20000.0, help="drop islands smaller than this (m²)")
    parser.add_argument("--natural-earth", type=Path, help="ne_10m_land.shp for independent comparison")
    parser.add_argument("--comparison-output", type=Path)
    args = parser.parse_args()
    pbf = args.pbf.expanduser().resolve(strict=True)
    with osmium.io.Reader(str(pbf)) as reader:
        header = reader.header()
        timestamp = header.get("osmosis_replication_timestamp")
    handler = Coastline()
    handler.apply_file(str(pbf), locations=True, idx="flex_mem",
                       filters=[osmium.filter.KeyFilter("natural", "boundary")])
    if handler.country is None or not handler.lines:
        raise SystemExit("OSM 국가 경계 relation 또는 해안선이 없습니다.")
    land, stats = assemble_land(handler.lines, handler.country)

    to_m = Transformer.from_crs("EPSG:4326", LAEA, always_xy=True)
    to_deg = Transformer.from_crs(LAEA, "EPSG:4326", always_xy=True)
    full_parts = polygons_of(land)
    kept_full, dropped, dropped_area = [], 0, 0.0
    for part in full_parts:
        metric = project(part, to_m)
        if metric.area < args.min_area_m2:
            dropped, dropped_area = dropped + 1, dropped_area + metric.area
        else:
            kept_full.append(metric)
    # One topology-preserving pass over all islands so neighbouring rings cannot cross.
    simple = MultiPolygon(kept_full).simplify(args.simplify_m, preserve_topology=True)
    kept = [project(p, to_deg) for p in polygons_of(simple)]
    polygons = [[[[round(x, 6), round(y, 6)] for x, y in ring.coords]
                 for ring in (p.exterior, *p.interiors)] for p in kept]
    vertices = sum(len(r) for p in polygons for r in p)
    full_area = area_km2(land, to_m)
    display = MultiPolygon(kept)
    islands = {}
    for name, (lon, lat) in CHECK_ISLANDS.items():
        hit = next((p for p in full_parts if p.contains(Point(lon, lat))), None)
        islands[name] = None if hit is None else dict(
            area_km2=round(area_km2(hit, to_m), 4), bounds=[round(v, 5) for v in hit.bounds],
            vertices=len(hit.exterior.coords),
            displayed=any(k.contains(Point(lon, lat)) for k in kept))
    metadata = dict(
        kind="land_polygons", meaning="육지 해안선(OSM natural=coastline)을 OSM 대한민국 국경 relation으로 클리핑한 지도 표시용 면",
        source="OpenStreetMap natural=coastline + boundary relation/307756 (Geofabrik extract)",
        license="ODbL-1.0", attribution="© OpenStreetMap contributors",
        pbf_file=pbf.name, pbf_sha256=sha256(pbf), osm_data_timestamp=timestamp,
        coordinate_order="[longitude, latitude] WGS84", simplify_tolerance_m=args.simplify_m,
        simplify_crs=LAEA, min_island_area_m2=args.min_area_m2,
        full_resolution_parts=len(full_parts), displayed_polygons=len(polygons), displayed_vertices=vertices,
        full_resolution_area_km2=round(full_area, 1), displayed_area_km2=round(area_km2(display, to_m), 1),
        dropped_small_islands=dropped,
        dropped_small_islands_area_km2=round(dropped_area / 1e6, 3),
        invalid_coastline_ways=handler.invalid, assembly=stats, check_islands=islands,
        built_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        command="scripts/build_kr_land_boundary.py --simplify-m %g --min-area-m2 %g" % (args.simplify_m, args.min_area_m2))
    payload = dict(schema_version="kr-land-boundary-1.0", country="KR", polygons=polygons, metadata=metadata)

    target = args.output.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False) as stream:
        json.dump(payload, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    os.replace(stream.name, target)
    print(json.dumps({k: v for k, v in metadata.items() if k != "meaning"}, ensure_ascii=False, indent=2))

    if args.natural_earth:
        compare(args, land, display, kept_full, handler.country, to_m)


def simplification_deviation(kept_full, display_m, tolerance):
    """Share of full-resolution coastline vertices of displayed islands within tolerance of the display."""
    edge = display_m.boundary
    shapely.prepare(edge)
    points = shapely.points(np.concatenate([shapely.get_coordinates(p.boundary) for p in kept_full]))
    within = shapely.dwithin(edge, points, tolerance + 1.0)
    return dict(full_resolution_vertices=len(points), within_tolerance_plus_1m=int(within.sum()),
                share=round(float(within.mean()), 6),
                symmetric_difference_km2=round(shapely.set_precision(unary_union(kept_full), 0.01).symmetric_difference(
                    shapely.set_precision(display_m, 0.01)).area / 1e6, 2))


def compare(args, land, display, kept_full, country, to_m):
    """Independent check against Natural Earth 1:10m land (coarser, public domain)."""
    import fiona
    frame = country.envelope.buffer(0.2)
    with fiona.open(args.natural_earth) as source:
        ne = unary_union([shape(f["geometry"]).intersection(frame) for f in source
                          if shape(f["geometry"]).intersects(frame)])
    ne_kr = ne.intersection(country)
    land_m, display_m, ne_m = (project(g, to_m) for g in (land, display, ne_kr))
    def boundary_distance(a, b):
        return round(a.boundary.hausdorff_distance(b.boundary), 1)
    regions = {"jeju": (126.1, 33.1, 127.0, 33.6), "ulleung_dokdo": (130.7, 37.1, 132.0, 37.6),
               "southwest_coast": (125.9, 34.3, 126.7, 35.1)}
    per_region = {}
    for name, bbox in regions.items():
        box = project(Polygon.from_bounds(*bbox), to_m)
        a, b = land_m.intersection(box), ne_m.intersection(box)
        per_region[name] = dict(osm_area_km2=round(a.area / 1e6, 2), natural_earth_area_km2=round(b.area / 1e6, 2),
                                osm_polygons=len(polygons_of(a)), natural_earth_polygons=len(polygons_of(b)))
    jeju_point = Point(*project(Point(*CHECK_ISLANDS["jeju"]), to_m).coords[0])
    jeju_osm = next(p for p in polygons_of(land_m) if p.contains(jeju_point))
    jeju_ne = next((p for p in polygons_of(ne_m) if p.contains(jeju_point)), None)
    old = project(country, to_m)
    result = dict(
        reference="Natural Earth 10m land v5.1.1 (public domain), clipped to the OSM KR relation",
        osm_land_area_km2=round(land_m.area / 1e6, 1), displayed_land_area_km2=round(display_m.area / 1e6, 1),
        natural_earth_land_area_km2=round(ne_m.area / 1e6, 1),
        old_country_polygon_area_km2=round(old.area / 1e6, 1),
        old_polygon_sea_share=round(1 - land_m.area / old.area, 3),
        osm_vs_ne_symmetric_difference_km2=round(land_m.symmetric_difference(ne_m).area / 1e6, 1),
        display_vs_full=simplification_deviation(kept_full, display_m, args.simplify_m),
        jeju=dict(osm_area_km2=round(jeju_osm.area / 1e6, 1),
                  natural_earth_area_km2=None if jeju_ne is None else round(jeju_ne.area / 1e6, 1),
                  boundary_hausdorff_m=None if jeju_ne is None else boundary_distance(jeju_osm, jeju_ne)),
        regions=per_region)
    if args.comparison_output:
        out = args.comparison_output.expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        (out.parent / "natural_earth_10m_land_kr.json").write_text(json.dumps(
            dict(source=result["reference"], polygons=[mapping(p)["coordinates"] for p in polygons_of(ne_kr)]),
            separators=(",", ":")), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
