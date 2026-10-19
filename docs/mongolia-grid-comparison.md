# Mongolia 35 kV grid: active map and synthetic PF preview

The AI-EMS Agent grid selector intentionally contains **two** grids:
- KR — KPG-193 test network (existing analysis enabled)
- MN — Mongolia OSM grid (MN_35k, 35/110/220 kV; map and a **read-only synthetic AC PF preview**)

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

## Optional precomputed PF preview

The map's PF preview uses only the already validated
`reduced_27_q_limited_with_reactor` scenario. It is not calculated by web
requests. Generate the local ignored JSON with PyPSA 1.2.4 from the Mongolia
Pixi environment:

```bash
cd ~/Projects/mongolia-grid-poc/pypsa-earth
pixi run python ~/Projects/ai-ems-agent/scripts/export_mn35k_pf_preview.py \
  --mongolia-root ~/Projects/mongolia-grid-poc \
  --map-file ~/Projects/ai-ems-agent/data/grids/mn-osm-35k-map.json
```

The generated file is:

`~/Projects/ai-ems-agent/data/grids/mn35k-synthetic-pf-preview.json`

It is a local, gitignored artifact. The exporter checks the map NetCDF, full
`MN_35k_poc` NetCDF, 166-bus PF scenario NetCDF, exact scenario IDs in stored
CSVs, `result_validation.json`, balance reports, and source hashes. It stops
rather than guessing if identities, endpoints, ratings, or validation results
disagree.

The JSON contract is `metadata`, `summary`, and `buses`, `lines`, and
`transformers` arrays. Bus results map to `bus_voltage_results.csv`; line and
transformer results map to `branch_loading_results.csv` (`component`
distinguishes them). Terminal P/Q signs are preserved from PyPSA: positive
terminal flow is from the bus into the branch. Unsolved equipment is listed
in `metadata.excluded_bus_ids` / `excluded_line_ids` and is not assigned
zero-valued results. The API verifies the payload against the current map
fingerprint, equipment IDs, endpoints, analysis counts, status, and
finite-value rules.

`GET /api/grids/mn-osm-35k/pf-preview` serves only this fixed JSON file.
Missing files return 404; invalid or stale data returns 422. The catalog's
`pf_preview_available` indicates whether the local files exist; it does not
enable `analysis_available`.

The UI supports the default nominal-voltage map, solved bus voltage (pu), and
MVA-based line loading. It permanently labels the result as synthetic and not
actual Mongolian operations. The result covers 166 buses, 156 lines, and 27
transformers; the remaining 7 buses and 3 lines are explicitly excluded. It
uses 45 synthetic generators, 25.6 MW / 8.414 MVAr synthetic load, and one
20 MVAr synthetic reactor. The 2013-01-01 snapshot is a model timestamp, not
a measurement date. Model data reliability remains
`MODEL_DATA_REQUIRES_REVIEW`.

## Verify

```bash
cd ~/Projects/ai-ems-agent
source .venv/bin/activate
python -m pytest -q tests/test_grid_catalog.py tests/test_export_pypsa_map.py \
  tests/test_mn35k_pf_export.py tests/test_mn35k_pf_preview.py \
  tests/test_mn35k_pf_api.py
```

Restart your existing FastAPI service and hard-refresh the browser.
Expected grid IDs from `GET /api/grids`: `kr-kpg193`,
`mn-osm-35k` only. `GET /api/grids/mn-osm/map` returns 404;
`GET /api/grids/mn-osm-35k/map` serves the expanded JSON when present.
The retired `GET /api/grids/mongolia/compare` endpoint is also removed.

Expected map topology inventory for this particular MN_35k snapshot:
173 buses, 159 lines, 27 inferred transformers, 27 geographic plant
records. These counts are not field-validated and are not hardcoded in
the UI. Voltage colors and transformer connections are **structural
preview information only**, not a solved operating grid.

The synthetic PF preview is read-only. **AC PF execution, N-1, Redispatch,
security analysis, and chat operations remain disabled for MN.**
Known data limits: unvalidated 35 kV line types; synthetic transformers
with default ratings/impedances; unverified plant ties; two isolated buses
(118 and 124); line-length discrepancy. Keep this warning until independent
model and load-flow validation has been performed.
