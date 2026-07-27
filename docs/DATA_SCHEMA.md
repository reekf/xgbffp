# XGBFFP Website Data Schemas

All paths are relative to `docs/`. Missing optional fields must be rendered as
“Not available”; they must not be replaced with fabricated or zero values.

## Forecast map JSON — schema version 5

Path: `archive/YYYYMMDD/map.json` (and `latest/map.json`)

Core fields:

```text
schema_version
date
valid_period_label
generated_utc
source_class                 realtime | historical
probability_encoding         integer 0..1000; divide by 10 for percent
risk_threshold_percent[]     [5, 15, 40, 70]
grid.lat[]
grid.lon[]
layers.<product>.label
layers.<product>.kind
layers.<product>.values[]
contours.<product>.<threshold>[]
observations.<proxy>.points[]
predictors.r<radius>.<name>
```

Every layer/predictor value array aligns by index with `grid.lat` and
`grid.lon`. Predictor values are normalized 0–1000 positions between
`scale_min` and `scale_max`; metadata includes units, global SHAP rank, and
direction. They are raw predictor diagnostics, not local SHAP values.

Older archives can omit r60kmV2, ensemble mean, PP, predictors, or
observations. The consumer detects availability per date.

## Skill manifest — schema version 1

Path: `model-skill/manifest.json`

The top-level `dataset_class` is `formal-independent-test-set`. Each `figures`
entry records title, metric, target, threshold list, test period, model,
source script/function, generation timestamp, and repo-relative image `path`.
The publisher fails if a referenced path is missing.

`model-skill/risk-occurrence.json` contains one categorical outcome per product,
threshold, and test day, derived from the final PP ETS contingency-count table.
A hit day has the selected risk in both the forecast and Practically Perfect; a
miss day has it only in Practically Perfect; a false-alarm day has it only in the
forecast; and a correct-negative day has it in neither. Day-level CSI and ETS
are recalculated from those 45 outcomes. Local PMM, ensemble maximum, and
r100kmV2 are intentionally excluded. It remains formal test-set data.

## Explainability manifest — schema version 1

Path: `explainability/manifest.json`

The `dataset_class` is `formal-independent-test-set-explainability`. Figure
entries include model, kind (`beeswarm`, `importance`, or `dependence`), test
period, source function, timestamp, and path.

## Daily realtime verification — schema version 3

Path: `verification/daily/YYYYMMDD.json`

Required fields:

```text
dataset_class                 realtime-issued-verification
default_reference             practically_perfect
date
valid_period_label
references.practically_perfect.products.<product>.<threshold>
references.ufvs_40km.products.<product>.<threshold>
products.<product>.<threshold>  backward-compatible PP alias
```

Threshold records contain non-negative contingency counts, sample count, truth
and forecast positive counts, squared-error sum, ETS, CSI, POD, FAR, frequency
bias, and Brier Score. Undefined metrics are `null`.

Only maps with `source_class == "realtime"`, an actual `layers.pp` array, and a
status that does not mark the case `mcs_eligible: false` are eligible. The UFVS
reference is binary and places an event within 40 km of every archived observed
flood-proxy point.

## Rolling realtime verification — schema version 4

Paths:

```text
verification/rolling/latest.json
verification/rolling/monthly.json
verification/rolling/seasonal.json
```

Each window records its definition, start/end dates, verified dates and count,
expected calendar days, missing-day count, completeness, both selectable
references, and pooled product/threshold metrics. Each product/threshold also
records forecast and selected-reference risk-day totals; day-level hits,
misses, false alarms, and correct negatives; and day-level CSI and ETS.
`risk_case_count` is the number of verified forecasts containing at least one
grid cell at or above that threshold. `latest.json` embeds the trailing-30-day
and seasonal windows for one browser request.

`verification/index.json` lists available daily dates and paths.

## Validation and versioning

`generate_dashboard_data.py` writes JSON with non-finite values disallowed,
validates aligned layer lengths, requires real finalized static figures, and
validates manifest paths. New incompatible contracts require a schema-version
increment; consumers must continue treating absent newer fields as optional for
archive compatibility.
