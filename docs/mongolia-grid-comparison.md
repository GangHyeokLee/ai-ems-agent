# Mongolia MN / MN_35k browser comparison

## Scope and safety

This workflow keeps `MN` (110/220 kV) and `MN_35k` (35/110/220 kV)
as **separate map previews**. The browser grid picker exposes both alongside
KPG-193, and the "몽골 계통 모델 비교" table reads counts from exported JSON.
The map legend allows independent voltage-level visibility for Mongolia, and
shows inferred transformer connections separately.

**Neither Mongolia preview enables AC power-flow, dispatch, security analysis,
or chat-based grid computation.** The 27 power-plant CSV rows are geographic
records, not validated generators. `MN_35k` has unverified 35 kV line types,
synthetic transformers, two isolated buses and uncertain plant-to-bus mapping.
Keep the original NetCDF inputs unchanged.

## Generate both map JSON files on your WSL desktop

The `ai-ems-agent` repository intentionally ignores `data/grids/` and `*.nc`.
The NetCDFs are local to PyPSA-Earth and are **not stored in this GitHub repository**.
After pulling the updated feature branch, run:

```bash
cd ~/Projects/ai-ems-agent
git switch feature/kems-multi-grid
git pull origin feature/kems-multi-grid

cd ~/Projects/mongolia-grid-poc/pypsa-earth

# Legacy preview. Keep existing source records and their *unverified* inferred bus mapping.
pixi run python ~/Projects/ai-ems-agent/scripts/export_pypsa_map.py \
  networks/MN/base.nc \
  ~/Projects/ai-ems-agent/data/grids/mn-osm-map.json \
  --powerplants resources/MN/powerplants.csv \
  --country-shapes resources/MN/shapes/country_shapes.geojson \
  --admin-shapes resources/MN/shapes/gadm_shapes.geojson

# Expanded preview. Reuse plant COORDINATES only; never reuse numeric MN bus IDs.
pixi run python ~/Projects/ai-ems-agent/scripts/export_pypsa_map.py \
  networks/MN_35k/base.nc \
  ~/Projects/ai-ems-agent/data/grids/mn-osm-35k-map.json \
  --powerplants resources/MN/powerplants.csv \
  --country-shapes resources/MN/shapes/country_shapes.geojson \
  --admin-shapes resources/MN/shapes/gadm_shapes.geojson \
  --no-plant-bus-mapping
```

If the GADM file is absent, locate the actual regional shape file in the
PyPSA-Earth resources directory or omit `--admin-shapes` for **both** commands.
Country polygons and province overlays are presentation only.

## Run the web app and check the UI

```bash
cd ~/Projects/ai-ems-agent
source .venv/bin/activate
python -m pytest -q tests/test_grid_catalog.py tests/test_export_pypsa_map.py
# Start the existing FastAPI server using your usual project launch command.
```

Verify:
1. Grid selector shows KR · KPG-193, Mongolia MN (legacy), Mongolia MN_35k.
2. Both Mongolia selections show the comparison table. Expected local models
   before further edits: MN 110 buses / 111 lines / 10 transformers;
   MN_35k 173 / 159 / 27. Treat these as cross-checks, not hardcoded UI values.
3. In MN_35k, 35/110/220 kV filters show and hide corresponding buses,
   lines and inferred transformer links. Two isolated 35 kV buses remain.
4. Power plant markers use their source longitude/latitude in both maps.
   **Expanded model plant-to-bus mappings are intentionally blank.**
5. KPG-193 map and analysis workflows remain available; both Mongolia
   analysis controls remain disabled.
6. If either JSON is missing, that model appears as "지도 준비 중" and its
   comparison numbers appear as unavailable rather than faked.
7. `GET /api/grids/mongolia/compare` reports parsed map counts; verify the
   same numbers visually.

The map renders connections as straight bus-to-bus SVG segments, not full
OSM line geometries. A transformer is a topology edge from PyPSA-Earth,
**not** a physically verified transformer asset. Do not compare its symbol or
line lengths with field survey drawings.

Once model verification succeeds, remove the legacy option in a separate
reviewed change, after preserving the source data and reproduction instructions.
