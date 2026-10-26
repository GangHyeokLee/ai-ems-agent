"""Validate and atomically import a geographic map. Never modify the source file.

The source ``country_polygons`` is the OSM country relation including the territorial sea, so it
is not carried over. ``--land-boundary`` (from scripts/build_kr_land_boundary.py) supplies the
land layer that is drawn instead; without it the map shows no country outline.
"""
import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
from ai_ems.grid_catalog import load_korea_map


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "data/grids/kr-osm-transmission-map.json")
    parser.add_argument("--land-boundary", type=Path, help="kr-land-boundary.json (land polygons)")
    args = parser.parse_args()
    source = args.source.expanduser().resolve(strict=True)
    target = args.output.expanduser().resolve()
    if source == target:
        parser.error("원본 파일에 덮어쓸 수 없습니다.")
    data = load_korea_map(source)
    data["metadata"] = {**data["metadata"], "source_map_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    data["metadata"].pop("land_boundary", None)
    data["country_polygons"] = []
    if args.land_boundary:
        land_file = args.land_boundary.expanduser().resolve(strict=True)
        land = json.loads(land_file.read_text(encoding="utf-8"))
        meta = land.get("metadata") if isinstance(land, dict) else None
        if (not isinstance(meta, dict) or land.get("schema_version") != "kr-land-boundary-1.0"
                or land.get("country") != "KR" or meta.get("kind") != "land_polygons"):
            parser.error("한국 육지 경계 파일 형식이 올바르지 않습니다.")
        data["country_polygons"] = land["polygons"]
        data["metadata"]["land_boundary"] = {
            **{key: meta.get(key) for key in ("kind", "source", "license", "attribution", "osm_data_timestamp",
                                              "pbf_sha256", "simplify_tolerance_m", "min_island_area_m2",
                                              "displayed_polygons", "displayed_vertices")},
            "file_sha256": hashlib.sha256(land_file.read_bytes()).hexdigest(),
        }
    else:
        print("경고: --land-boundary가 없어 국가 외곽선을 표시하지 않습니다.", file=sys.stderr)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        load_korea_map(temporary)
        os.replace(temporary, target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    print(json.dumps(dict(output=str(target), buses=len(data["buses"]), lines=len(data["lines"]), powerplants=len(data["powerplants"]),
                          land_polygons=len(data["country_polygons"]), bytes=target.stat().st_size), ensure_ascii=False))


if __name__ == "__main__":
    main()
