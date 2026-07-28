# XGBoosted Flash Flood Predictions

XGBoosted Flash Flood Predictions (XGBFFP) is an experimental decision-support
website for flash-flood potential associated with mesoscale convective systems.
The default Forecast view combines XGBoost neighborhood-radius probabilities,
WPC Excessive Rainfall Outlook context, post-event Practically Perfect
verification, predictor diagnostics, radar, flood alerts, storm reports,
mPING reports, and a date archive.

**Experimental machine-learning guidance. Not an official NWS forecast, watch,
or warning.**

## OpenAI Build Week development record

GPT-5.6 and Codex were used to help develop and validate website features,
improve the efficiency of the XGBoost training workflows, and build out the
real-time prediction and verification pipeline. See
[`OPENAI_BUILD_WEEK_README.md`](OPENAI_BUILD_WEEK_README.md) for the full
engineering contribution record and the boundary between AI-assisted
development and human scientific decisions.

## Website views

- `?view=forecast` — 2D/3D map and Location Briefing
- `?view=skill` — independent 2024–2025 test-set skill
- `?view=running` — issued-forecast trailing-30-day and seasonal verification
- `?view=explainability` — finalized global SHAP and dependence figures
- `?view=about` — product and evaluation-population overview

The Forecast view supports `date=YYYYMMDD` and `map=3d`.

## Forecast products

The website reads the available fields in each archive instead of assuming all
dates have the newest schema. Products can include r40, r60, r75, r100, the
ML ensemble mean, WPC ERO, and post-event Practically Perfect.

## Location Briefing

Click the map to select the nearest valid grid point. The panel displays every
available product probability and category, standard-member agreement, a
deterministic interpretation, decoded predictor values, nearby alerts/reports,
post-event context, and copy-ready plain text.

Agreement uses the standard r40/r60/r75/r100 products:

- High: all members share a risk category and the probability range is at most
  10 percentage points.
- Moderate: categories differ by at most one level or the range is at most 20
  points.
- Low: all other available-member combinations.

The exact 5, 15, 40, and 70 percent boundaries are inclusive. A click more than
100 km from the nearest grid point is outside the forecast domain. Nearby
reports and UFVS observations use a labeled 40-km search radius.

## Evaluation and data sources

Formal model skill uses the final saved 2024–2025 test-set figures produced by
the v33 viewer notebook. Running verification is calculated only from
realtime-issued, MCS-eligible archive maps and can be viewed against either the
Practically Perfect field or the observed UFVS flood proxies expanded 40 km;
formal test cases are never backfilled into the realtime statistics. See
[`docs/METRICS.md`](docs/METRICS.md) and
[`docs/DATA_SCHEMA.md`](docs/DATA_SCHEMA.md).

Map context includes WPC ERO, NWS API flood alerts, Iowa Environmental Mesonet
local storm reports, RainViewer radar, mPING flood-impact reports when a
publisher token is configured, and the UFVS proxy collections included by the
verification publisher.

Realtime and archived MCS eligibility is determined by the actual upstream
PyFLEXTRKR package using HRRR simulated brightness temperature and composite
reflectivity. The modified thresholds, HRRR-to-PyFLEXTRKR input contract,
official pipeline stages, provenance fields, and reproducible environment are
documented in [`docs/PYFLEXTRKR_HRRR.md`](docs/PYFLEXTRKR_HRRR.md).

## Reproducible dashboard publishing

`generate_dashboard_data.py`:

1. copies selected final figures to stable `docs/` locations;
2. records their source function, target, model, period, and path in manifests;
3. validates every manifest path;
4. reads verified realtime `map.json` files;
5. writes daily and pooled rolling verification JSON.

It consumes saved outputs and does not retrain. After initial static-asset
publishing, the operational verification workflow calls:

```bash
python generate_dashboard_data.py --verification-only
```

`publish_verification_output.sh` refreshes and stages the realtime verification
files after publishing a verified map. Forecast generation and feature
creation are unchanged.

## Daily dual-repository publishing

During the OpenAI Build Week submission freeze, the installed forecast and
verification jobs continue to run from the original `gempak-scripts` checkout
and publish its daily generated artifacts first. After each successful source
publication, `sync_daily_from_build_week_repo.sh` updates a separate clean
publisher checkout, merges the source repository's new daily commit onto the
latest `xgbffp/main`, and pushes the result to this repository.

This merge-based sync intentionally allows website development to continue in
`xgbffp` without requiring the two repositories' branch tips to remain
identical. The dedicated publisher checkout and non-blocking file lock keep
the automation isolated from an interactive development checkout. A dirty
publisher checkout or merge conflict stops the target push instead of
overwriting either repository.

## Local development and checks

Serve `docs/` from the repository root:

```bash
python -m http.server 8000 --directory docs
```

Then open `http://localhost:8000/?view=forecast`.

Core checks:

```bash
python -m pytest tests/test_dashboard_data.py tests/test_interactive_map_realtime_selection.py
node tests/test_briefing.js
node --check docs/app.js
bash -n publish_latest_ml_output.sh publish_verification_output.sh
bash -n sync_daily_from_build_week_repo.sh
```

## Known limitations

- Final global SHAP figures currently exist for r60 and r100; final
  pre-rendered dependence panels currently exist only for r100.
- Map JSON does not export local SHAP values, so raw predictor diagnostics are
  never mislabeled as local SHAP contributions.
- Running verification began with the realtime website record and can be
  sample-limited or have missing dates; every view reports cases and
  completeness.
- External radar, alerts, storm reports, and mPING services can be temporarily
  unavailable without affecting the forecast map data.
