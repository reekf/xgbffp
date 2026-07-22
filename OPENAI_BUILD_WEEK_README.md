# OpenAI Build Week: GPT-5.6 and Codex Development Record

## Project

**XGBoosted Flash Flood Predictions (XGBFFP)** is an experimental website and
real-time pipeline for displaying XGBoost flash-flood guidance, WPC Excessive
Rainfall Outlook context, post-event Practically Perfect verification, and
model explainability information.

During OpenAI Build Week, GPT-5.6 through Codex was used as the primary
AI-assisted software-engineering environment for extending and hardening this
project. Codex inspected the existing repository, implemented changes, ran
checks, reviewed diffs, and helped publish the completed work to GitHub Pages.
Development was iterative: the human project lead reviewed the scientific
meaning and presentation of each result, supplied corrections, and retained
final authority over the product.

## Starting point and Build Week scope

This was an extension of an active human-led research project, not a project
created from an empty repository. The core meteorological research, XGBoost
modeling approach, saved model artifacts, forecast targets, and portions of the
forecast/verification workflow already existed. GPT-5.6/Codex was used to help
extend, integrate, optimize, test, document, and deploy that work. The
contributions below describe that AI-assisted engineering scope without
reassigning authorship of the underlying science.

## How GPT-5.6 and Codex were used

### Website development

GPT-5.6/Codex helped turn the existing forecast map into a broader
decision-support and evaluation website. Codex-assisted work included:

- auditing the existing Leaflet, MapLibre/deck.gl, archive, and publishing
  code before extending it;
- building direct-link Forecast, Model Skill, Running Verification,
  Explainability, and About views;
- adding a click-to-select Location Briefing with probabilities, risk
  categories, multi-model agreement, predictor diagnostics, nearby reports,
  alerts, verification context, and copy-ready text;
- adding and refining 2D/3D forecast layers, contour controls, radar, flood
  alerts, local storm reports, mPING reports, predictor overlays, and
  observation/report radius displays;
- connecting finalized 2024–2025 ETS, Practically Perfect ETS, Any Flood Proxy
  Brier Score, and SHAP figures to machine-readable manifests;
- improving figure legibility with full-width, high-resolution presentation
  and direct full-resolution links;
- creating responsive layouts and cache-versioned assets for reliable GitHub
  Pages updates; and
- adding automated JavaScript, Python, JSON, shell, schema, and unit checks.

### Recent Codex-assisted website changes

The following July 20 refinements were specified and scientifically reviewed
by the human project lead, then traced through the existing data publisher,
browser code, tests, Git history, and public deployment by Codex:

- replaced the first verification page's pooled pixel contingency display
  with one day-level risk-occurrence outcome per product and threshold;
- defined day-level Hits as days when both a forecast and Practically Perfect
  contained the selected risk, Misses as PP-only days, False Alarms as
  forecast-only days, and Correct Negatives as days when neither contained it;
- calculated internal day-level CSI and ETS from those occurrence counts and
  highlighted every configuration tied for the best selected score;
- updated Running Verification to show how many verified forecasts each ML
  member and WPC issued the selected risk, the corresponding PP risk-day
  count, all four day-level contingency counts, CSI, and ETS;
- defaulted both verification views to Moderate-or-greater and removed Brier
  Skill Score from the published interface;
- corrected the running ETS graphic to use a signed, zero-centered scale, with
  positive ETS extending right and negative ETS extending left;
- handled the perfect all-event day-level ETS edge case explicitly. For
  example, the formal Marginal comparison has 45 Hits and no Misses or False
  Alarms for the ML configurations, so the documented occurrence convention
  reports ETS 1.0 instead of leaving a `0 / 0` result blank;
- restricted the formal comparison to the requested ML radius members,
  ensemble mean, and WPC, excluding Local PMM, ensemble maximum, and
  r100kmV2;
- connected only the authoritative finalized ETS and Brier Score figures,
  retaining Brier Score including/excluding Marginal while omitting Brier
  Skill Score, the common-case risk-area plot, and standalone MRMS-over-FFG
  descriptive statistics;
- made r60kmV2 the default map member, labeled it Beta/testing, and set the
  default forecast opacity to 100%;
- made 2D and 3D zooming more granular and fixed the selected forecast so it
  renders immediately when switching from 2D to 3D;
- added a dashed gray XGBFFP forecast-domain outline in 2D and 3D, then moved
  its label just outside the northern boundary so it does not cover forecast
  risk areas;
- extended the Reflectivity controls with a selectable single-site NEXRAD N0B
  base-reflectivity layer sourced from online radar-station metadata and
  timestamped individual-site archive tiles, while retaining the existing
  composite loop as a mutually exclusive option; added distinguishable
  radar-site points that appear on the 2D map only when single-site mode is
  selected, so clicking a site selects it and starts a recent scan loop, with
  a play/pause control and highlighted active site;
- temporarily grayed out and disabled the nonfunctional mPING report control,
  stopped its unavailable archive request, and documented that temporary
  state in the map instructions;
- rewrote the probability legend to identify the display explicitly as
  probability of flash flooding and label the four thresholds as Marginal
  (at least 5%), Slight (at least 15%), Moderate (at least 40%), and High
  (at least 70%), then changed the legend to a compact vertical stack for
  easier scanning;
- fixed warning and watch polygon interaction by making those visual overlays
  click-through, allowing a user to select the underlying forecast pixel and
  open its Location Briefing while retaining the alert geometry for the
  briefing's watch/warning context; and
- regenerated the machine-readable verification assets, ran Python/JavaScript/
  JSON and contract tests, validated live single-site radar metadata and
  timestamped archive tiles, reviewed focused diffs, pushed scoped commits,
  and monitored GitHub Pages until the new public bundles and JSON were live.

### Improving ML-code efficiency

GPT-5.6/Codex was also used to inspect and improve the large XGBoost training
workflows so they could operate more efficiently on multi-case, multi-radius
datasets. This work included:

- developing memory-safe master-Parquet assembly with incremental PyArrow
  row-group writing instead of concatenating every daily dataset in memory;
- creating variants that retain the identifiers, target fields,
  and predictor columns required for training while omitting unnecessary
  intermediate/debug columns;
- adding bounded train/test row sampling before full pandas materialization,
  while preserving full-domain feature engineering and formal evaluation;
- reducing dataframe memory with narrower numeric dtypes and explicit cleanup
  of large temporary objects;
- controlling XGBoost/Optuna/Ray concurrency and releasing Ray resources before
  local full-model fitting;
- generating consistent R40, R60, R75, R100, and experimental same-radius
  training-script variants from known working provenance;
- centralizing case-catalog parsing, date deduplication, and stable case IDs;
  and
- adding compile, artifact, feature-radius, target-contract, and synthetic
  validation checks before treating a training result as trustworthy.

These changes were intended to improve memory use, repeatability, and
iteration speed without silently changing the human-defined modeling
contract.

### Building the real-time prediction and verification pipeline

Codex-assisted engineering helped build out and harden the operational path
from model artifacts to the public website:

1. The real-time plotter loads current atmospheric inputs and the saved XGBoost
   radius models.
2. Forecast probabilities are exported to a validated machine-readable map
   schema with ML members, ensemble mean, WPC context, contours, and available
   predictor diagnostics.
3. `publish_latest_ml_output.sh` validates and publishes the forecast,
   refreshes the latest product, and maintains the date archive.
4. `publish_verification_output.sh` adds the post-event Practically Perfect
   layer and verification graphics after the valid period.
5. `generate_dashboard_data.py` rebuilds daily and pooled weekly, monthly, and
   seasonal issued-forecast verification.
6. Git commits and GitHub Pages deployment make the updated forecast and
   verification available publicly.

Codex helped add schema and required-layer gates, stale-output protection,
archive/status rebuilding, resilient Git synchronization behavior, public-data
sanitization, rolling-verification aggregation, selected-risk case counts, and
live deployment checks. These checks reduce the chance that an incomplete or
stale run is presented as the latest forecast.

After the Build Week submission entered its editing freeze, Codex created the
separate `reekf/xgbffp` continuation repository and configured merge-based
dual publishing. The existing scheduled jobs continue publishing daily
forecast and verification data to the frozen-project repository, then an
isolated clean checkout merges those generated commits onto the latest
`xgbffp/main`. This keeps the continuation site's data current without
discarding website-only commits made in the new repository.

### Separate Day-2 training and verification workflow

Under the human-defined forecast and target contract, Codex also developed a
separate Day-2 workflow rather than changing the existing Day-1 models:

- generated four XGBoost classification programs for 40-, 60-, 75-, and
  100-km MRMS-over-FFG targets;
- retained the existing 0–24-hour RAP features and added distinct 24–48-hour
  instantaneous, precipitation-accumulation, maximum-accumulation, and
  QPF/FFG-ratio features;
- aligned the target to the D+1 12Z through D+2 12Z period following the RAP
  initialization, with explicit guards against reusing incomplete Day-1
  feature chunks or future-observation target features;
- created a separate Day-2 verification viewer that retrieves WPC Day-2 ERO
  products for the exact valid window and compares them with each ML radius;
- kept Day-2 model tags, caches, manifests, verification output, and viewer
  artifacts separate from Day 1; and
- added contract tests and a dedicated
  `DAY2_XGBFFP_TRAINING_AND_VERIFICATION.md` runbook.

## Human scientific and product responsibility

GPT-5.6/Codex supported software implementation, refactoring, testing,
documentation, and deployment. Human-authored project logic remained
authoritative for:

- flood-proxy and Practically Perfect definitions;
- XGBoost classification targets and predictor selection;
- 5%, 15%, 40%, and 70% risk thresholds;
- neighborhood and report-expansion radii;
- training/test case selection;
- finalized figure selection and scientific interpretation; and
- decisions about which products and statistics belong on the public site.

GPT-5.6/Codex did not replace scientific review, originate official NWS
guidance, or convert this experimental product into an official forecast.

## Development workflow

The Build Week collaboration followed a reviewable engineering loop:

1. inspect the existing code and saved scientific outputs;
2. restate the requested scientific or interface contract;
3. make focused edits without including unrelated working-tree changes;
4. regenerate affected data and figures;
5. run proportional static, unit, schema, and numerical checks;
6. review the staged diff;
7. commit and publish the scoped changes through the repository's selected
   GitHub workflow; and
8. monitor GitHub Pages and verify the public HTML, JavaScript, and JSON files.

This workflow let the human project lead give rapid scientific and product
feedback while GPT-5.6/Codex handled much of the repository-scale inspection,
implementation, consistency checking, and deployment verification.

## Representative repository artifacts

| Area | Key artifacts |
| --- | --- |
| Website | `docs/index.html`, `docs/app.js`, `docs/style.css`, `docs/briefing.js` |
| Dashboard data | `generate_dashboard_data.py`, `docs/model-skill/`, `docs/verification/` |
| Real-time maps | `realtime_mcs_trigger_plot.py`, `generate_interactive_map_data.py` |
| Publishing | `publish_latest_ml_output.sh`, `publish_verification_output.sh`, `realtime_ml.crontab` |
| Efficient training | `hazard_ml_training_v28_r100km_singletarget_radiusstats_regression_MEMSAFE_V3.py` |
| Radius workflows | `run_hazard_ml_v33_radius_sensitivity_from_WORKING_v28_radiusstats_SLIMMASTER_ROWSAMPLE.sh` and its generator |
| Day-2 workflow | `run_hazard_ml_v33_day2_radius_sensitivity_from_WORKING_v28_radiusstats_SLIMMASTER_ROWSAMPLE.sh`, its generator, `hazard_ml_v33_day2_verification_viewer.py`, and `DAY2_XGBFFP_TRAINING_AND_VERIFICATION.md` |
| Validation | `tests/test_dashboard_data.py`, `tests/test_briefing.js`, publisher schema checks |

## Representative development milestones

- `f481af6`: standalone HRRR MCS-triggered real-time ML plotter
- `647929d`: GitHub Pages site for real-time ML output
- `3ce5f3b`: stabilized real-time RAP prediction pipeline
- `e0d9d3a`: interactive ML forecast map
- `690a83d`: live radar and daily-publishing hardening
- `04d04fa`: SHAP predictor overlays and radar improvements
- `b74ef5a`: flood alerts and multi-radius predictor layers
- `f7a84ee`: XGBFFP Location Briefing and evaluation dashboard
- `03988de`: corrected authoritative model-skill figures
- `764030f`: added Hits and removed unneeded MRMS-over-FFG skill figures
- `cc1c2da`: added selected-risk case totals and removed Brier Skill Score
- `c06201c`: replaced pixel contingency display with day-level occurrence
  verification and added the map defaults/domain outline
- `3d2c8d8`: moved the forecast-domain label outside the dashed boundary
- `8f3ebd5`: corrected signed ETS bars and populated the perfect Marginal
  occurrence ETS case
- `ae4f04e`: began the selectable single-site NEXRAD radar work, disabled the
  nonfunctional mPING control, clarified the flash-flood probability legend,
  and made flood-alert polygons click-through for Location Briefing selection

## Disclaimer

XGBFFP is experimental machine-learning guidance. It is not an official
National Weather Service forecast, watch, or warning. Users should evaluate it
alongside official forecasts, observations, and established operational
decision-support practices.
