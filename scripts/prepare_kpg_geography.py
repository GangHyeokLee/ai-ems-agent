"""Fetch public South Korean ADM0 / ADM1 outlines for the KPG-193 map.

Source: geoBoundaries gbOpen (https://www.geoboundaries.org), CC BY 4.0;
the 2021 KOR boundary files attribute original geometry to Natural Earth.
This provides geographic context only: KPG-193 is a *synthetic* test grid.

Run from AI-EMS Python venv: python scripts/prepare_kpg_geography.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from export_pypsa_map import read_admin_regions, read_country_polygons

API_TEMPLATE = "https://www.geoboundaries.org/api/current/gbOpen/KOR/{level}/"
MAX_DOWNLOAD_BYTES = 15 * 1024 * 1024


def fetch_json(url: str) -> dict:
    """Read a bounded HTTPS JSON download, checking both size and schema."""
    parts = urlparse(url)
    if parts.scheme != "https" or parts.hostname not in {
        "www.geoboundaries.org", "geoboundaries.org",
        "github.com", "raw.githubusercontent.com",
        "media.githubusercontent.com",
    }:
        raise ValueError(f"Unapproved geographic download URL: {url}")

    request = Request(url, headers={"User-Agent": "KEMS-geography-preview/1.0"})
    with urlopen(request, timeout=45) as response:
        raw = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(raw) > MAX_DOWNLOAD_BYTES:
        raise ValueError("Geographic JSON exceeds allowed size")
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(
            "Geographic download was not JSON; verify geoBoundaries source"
        ) from exc
    if not isinstance(document, dict):
        raise ValueError("Expected JSON object for geographic data")
    return document


def build_geography(country_geojson: dict, admin_geojson: dict) -> dict:
    """Generate the same validated topology shape used by Mongolian maps."""
    with TemporaryDirectory(prefix="kems-kor-geography-") as temp:
        country = Path(temp) / "adm0.geojson"
        admin = Path(temp) / "adm1.geojson"
        country.write_text(json.dumps(country_geojson), encoding="utf-8")
        admin.write_text(json.dumps(admin_geojson), encoding="utf-8")
        country_polygons = read_country_polygons(country, tolerance=0.015)
        admin_regions = read_admin_regions(admin, tolerance=0.015)

    # ADM1 from geoBoundaries contains 17 provinces/metropolitan regions,
    # as recorded in the KOR-ADM1 metadata. Do not invent missing regions.
    if len(admin_regions) != 17:
        raise ValueError(
            f"Expected 17 Korean ADM1 regions, got {len(admin_regions)}; "
            "inspect source metadata before generating the map"
        )
    return {
        "country_polygons": country_polygons,
        "admin_regions": admin_regions,
        "attribution": "Boundary: geoBoundaries gbOpen / Natural Earth, CC BY 4.0",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "grids" / "kr-geography.json",
    )
    args = parser.parse_args()

    geometries = {}
    for level in ("ADM0", "ADM1"):
        metadata = fetch_json(API_TEMPLATE.format(level=level))
        if metadata.get("boundaryISO") != "KOR" or metadata.get("boundaryType") != level:
            raise ValueError(f"Unexpected geoBoundaries metadata for {level}")
        source_url = metadata.get("simplifiedGeometryGeoJSON")
        if not isinstance(source_url, str) or not source_url:
            raise ValueError(f"No simplified geoJSON URL for {level}")
        geometries[level] = fetch_json(source_url)

    payload = build_geography(geometries["ADM0"], geometries["ADM1"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    print(
        f"KR geography: {len(payload['country_polygons'])} country polygons, "
        f"{len(payload['admin_regions'])} ADM1 regions"
    )
    print(f"Saved: {args.output.resolve()}")


if __name__ == "__main__":
    main()
