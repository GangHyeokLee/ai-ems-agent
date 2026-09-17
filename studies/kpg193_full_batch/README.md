# KPG-193 Full Batch Security Study

This study extends the existing single-contingency domain tools into a
reproducible batch pipeline. It keeps the AI/Agent layer out of risk scoring:
classification and ranking are derived from PyPowSyBl physical-analysis
results and stable tie-break rules.

## Run

```bash
python scripts/run_kpg193_full_batch.py \
  --legacy-csv /path/to/kpg_day02_n-1_results.csv
```

Optional generator contingencies and batch chunking:

```bash
python scripts/run_kpg193_full_batch.py \
  --include-generators \
  --chunk-size 100 \
  --legacy-csv /path/to/kpg_day02_n-1_results.csv
```

`--chunk-size 0` (the default) registers every requested contingency in one
Security Analysis. If a batch fails, the runner recursively isolates the
failing contingency and records it as `EXECUTION_ERROR` while preserving the
remaining results.

## Classification

- `CONVERGED_CLEAN`
- `CONVERGED_VIOLATION`
- `ISLANDED`
- `NON_CONVERGED`
- `EXECUTION_ERROR`

The original PyPowSyBl status is retained in `raw_status`. Execution errors do
not receive a physical-risk rank.

## Deterministic review order

1. normalized physical outcome class
2. disconnected element count
3. violated equipment count and violation-record count
4. maximum loading percent
5. maximum relative limit excess
6. maximum voltage violation
7. minimum bus voltage
8. contingency ID

This is a review priority, not an AI score and not a failure probability.

## Outputs

- `run_manifest.json`
- `contingency_summary.csv`
- `violations.csv`
- `risk_ranking.csv`
- `topn_sensitivity.csv`
- `summary.json`
- `report.html`
- `legacy_comparison.csv` when `--legacy-csv` is supplied
- `equipment_mapping.csv` when `--legacy-csv` is supplied

Legacy comparison maps MATPOWER branch row numbers to PyPowSyBl line IDs by
unordered bus pair and deterministic parallel-circuit occurrence. Review
`equipment_mapping.csv` before interpreting numerical differences.
