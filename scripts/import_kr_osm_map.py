"""Validate and atomically import a geographic map. Never modify the source file."""
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
    args = parser.parse_args()
    source = args.source.expanduser().resolve(strict=True)
    target = args.output.expanduser().resolve()
    if source == target:
        parser.error("원본 파일에 덮어쓸 수 없습니다.")
    data = load_korea_map(source)
    data["metadata"] = {**data["metadata"], "source_map_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
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
    print(json.dumps(dict(output=str(target), buses=len(data["buses"]), lines=len(data["lines"]), powerplants=len(data["powerplants"]), bytes=target.stat().st_size), ensure_ascii=False))


if __name__ == "__main__":
    main()
