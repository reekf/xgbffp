# XGBFFP Build-Week Changelog

## Existing before this extension

The repository already had the operational v33 forecast/verification pipeline,
GitHub Pages forecast archive, 2D and fixed-angle 3D maps, multiradius ML and
ensemble/WPC/Practically Perfect layers, predictor overlays, radar, NWS flood
alerts, IEM LSRs, mPING reports, and 40-km observation/report rings.

Relevant dated commits include:

- `e0d9d3a` (2026-07-03): interactive ML forecast map
- `0c58b9a` (2026-07-04): fixed-angle 3D probability map
- `3caddb4` (2026-07-04): 40-km proxy/report rings
- `690a83d` (2026-07-08): radar and publish hardening
- `04d04fa` and `b74ef5a` (2026-07-08): predictor and flood-alert layers
- `f5f3455` (2026-07-13): r60kmV2 website forecast routing

## Added in the 2026-07-19 extension

- XGBoosted Flash Flood Predictions/XGBFFP product naming
- direct-link Forecast, Model Skill, Running Verification, Explainability, and
  About views
- click-to-select Location Briefing with actual grid probabilities
- standard-member agreement, deterministic interpretation, predictor values,
  nearby alert/report/proxy context, and Copy Briefing
- finalized 2024–2025 Any Flood Proxy ETS, PP ETS,
  including/excluding-Marginal Any Flood Proxy Brier Score, and SHAP assets
  with machine-readable manifests
- pooled hit, false-alarm, and miss comparison excluding Local PMM, ensemble
  maximum, and r100kmV2
- running selected-risk case counts by ML/WPC product, with Moderate-or-greater
  defaults and no published Brier Skill Score
- daily and pooled weekly/monthly/seasonal issued-forecast verification JSON
- automated manifest/schema/unit validation and publisher integration
- responsive/mobile dashboard and briefing layouts
- a full Day-2 notebook viewer plus a resumable, bounded-memory builder for
  all four historical ML prediction caches
- corrected Day-2 event-date semantics: case V now uses RAP V-1 at 09Z while
  ML targets, UFVS, Practically Perfect, and WPC all use V 12Z through V+1 12Z
- explicit `Date` and `RAP_Init_Date` provenance plus a guarded
  `v33day2valid` namespace after purging the incompatible first-pass artifacts
- a responsive, weather-themed About the Creator page built from the project
  lead's CV, including research and operational achievements, education,
  publications and presentations, technical skills, a downloadable CV, and a
  direct authoritative-journal link for the published Gallus et al. paper
- larger outlined primary tabs with a sliding active highlight, directional
  page transitions, and reduced-motion support
- valid-period safeguards that suppress live radar, live LSRs, and active NWS
  flood alerts on archived cases while preserving archived flood proxies
- an ML-only continuous 2D probability display and dynamic 0–100% legend that
  retains WPC risk colors and fades probabilities above 70% into dark purple
- updated optimized XGBFFP artwork for the header and browser/mobile icons

## Human scientific and product decisions

Human-authored project logic remains authoritative for model targets,
predictors, risk thresholds, Practically Perfect construction, UFVS proxy
definitions, figure styling, and final test-set interpretation. The extension
does not retrain models or change those contracts.

## GPT-5.6 and Codex contribution

GPT-5.6 through Codex was used as the primary AI-assisted engineering
environment during OpenAI Build Week. Codex audited the existing
implementation, connected saved scientific outputs to the website, implemented
browser and publishing changes, improved memory-safe and row-sampled ML
workflows, expanded the real-time prediction/verification pipeline, and added
tests and documentation. Human scientific and product decisions remained
authoritative. See
[`OPENAI_BUILD_WEEK_README.md`](OPENAI_BUILD_WEEK_README.md) for the detailed
contribution record.
