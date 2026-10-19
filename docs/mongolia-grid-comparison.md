# Mongolia 35 kV grid: active web map

The AI-EMS Agent grid selector intentionally contains **two** grids:
- KR — KPG-193 test network (existing analysis enabled)
- MN — Mongolia OSM grid (MN_35k, 35/110/220 kV; **map only**)

The old MN (110/220 kV) is **retired from the web UI and API**. Its
`networks/MN/base.nc` and old `mn-osm-map.json` may remain on disk for
reproducibility; neither is used by this application. Do not delete the
source model, history, or validation reports as part of this change.

## Required local map data

The active map file is:

`~/Projects/ai-ems-agent/data/grids/mn-osm-35k-map.json`

This is a generated, gitignored artifact. **Pulling the code does not create
it.** If you generated this JSON in the previous comparison task, simply
reuse it. Otherwise export it on your WSL desktop:

```bash
cd ~/Projects/ai-ems-agent
git switch feature/kems-multi-grid
git pull origin feature/kems-multi-grid

cd ~/Projects/mongolia-grid-poc/pypsa-earth
pixi run python ~/Projects/ai-ems-agent/scripts/export_pypsa_map.py \
  networks/MN_35k/base.nc \
  ~/Projects/ai-ems-agent/data/grids/mn-osm-35k-map.json \
  --powerplants resources/MN/powerplants.csv \
  --country-shapes resources/MN/shapes/country_shapes.geojson \
  --no-plant-bus-mapping
```

If the province shapes are available, add
`--admin-shapes resources/MN/shapes/gadm_shapes.geojson`.
If the NetCDF or geographic input is absent, verify the path instead of
regenerating or silently substituting the old network.

The `--no-plant-bus-mapping` flag is essential: the old MN CSV bus numbers
do **not** represent the same physical bus IDs in MN_35k. Geographic plant
markers can appear, but their electrical connection is unverified.

## Verify

```bash
cd ~/Projects/ai-ems-agent
source .venv/bin/activate
python -m pytest -q tests/test_grid_catalog.py tests/test_export_pypsa_map.py
```

Restart your existing FastAPI service and hard-refresh the browser.
Expected grid IDs from `GET /api/grids`: `kr-kpg193`,
`mn-osm-35k` only. `GET /api/grids/mn-osm/map` returns 404;
`GET /api/grids/mn-osm-35k/map` serves the expanded JSON when present.
The retired `GET /api/grids/mongolia/compare` endpoint is also removed.

Expected topology inventory for this particular MN_35k snapshot:
173 buses, 159 lines, 27 inferred transformers, 27 geographic plant
records. These counts are not field-validated and are not hardcoded in
the UI. Voltage colors and transformer connections are **structural
preview information only**, not a solved operating grid.

**AC PF, security analysis and chat operations remain disabled for MN.**
Known data limits: unvalidated 35 kV line types; synthetic transformers
with default ratings/impedances; unverified plant ties; two isolated buses
(118 and 124); line-length discrepancy. Keep this warning until independent
model and load-flow validation has been performed.
