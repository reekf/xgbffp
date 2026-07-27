# Actual PyFLEXTRKR HRRR + RAP MCS classification

Realtime publication and the 2026 archive audit require independent HRRR and
RAP classifications from the upstream PyFLEXTRKR package, not an independent
lifecycle approximation. The adapter invokes these official package stages
for each model:

1. `idfeature_driver`
2. `tracksingle_driver`
3. `gettracknumbers`
4. `trackstats_driver`
5. `identifymcs_tb`
6. `match_tbpf_tracks`
7. `define_robust_mcs_radar`

The environment is pinned to PyFLEXTRKR 2026.7.0 at upstream commit
`6a3a6435ee6b3a64ec411b9f2af38226d6f32850`. Recreate it with
`install_pyflextrkr_env.sh`. Set `XGBFFP_PYFLEXTRKR_PYTHON` if the environment
interpreter is installed somewhere other than the default user Conda path.

## Modified dual-model MCS criteria

- Simulated brightness temperature cold-cloud area is greater than
  60,000 km2.
- The cold-cloud condition is present for at least six continuous hourly
  samples.
- A collocated precipitation feature has a major-axis length greater than
  100 km for at least four continuous hourly samples.
- The precipitation feature contains model composite simulated reflectivity
  greater than 45 dBZ for the same duration.

HRRR uses its 12Z f00-f24 forecast and RAP uses its 09Z f03-f27 forecast, so
both evaluate the same 12Z-to-12Z valid window. Both models must qualify for a
forecast to be MCS-eligible.

The adapter supplies model SBT as `tb`. A binary rain-rate compatibility field
defines precipitation features from connected REFC >=25 dBZ pixels. Because
REFC is already the maximum reflectivity anywhere in the model column, it is
repeated on PyFLEXTRKR compatibility height levels to express the required
"greater than 45 dBZ at any vertical level" condition. This is explicitly
marked in every input NetCDF file and is not represented as a reconstructed
physical vertical profile.

The actual robust-MCS result is the sole eligibility gate. The separately
calculated six-hour QPF threshold is retained only as a rainfall-only audit
diagnostic and cannot make a case eligible.

## Resumability and provenance

Prepared hourly NetCDF files, official PyFLEXTRKR outputs, configuration,
input manifest, and result JSON are stored below each model's trigger cache case.
Matching completed stages and result files are reused. Package version,
upstream commit, completed official stages, configuration path, and result path
are recorded in the trigger summary and public archive classification.

If the pinned environment or the official pipeline fails, publication fails
closed; there is no fallback custom tracker.

RAP normally contains SBT123/SBT124 and therefore uses the full cloud and
structural criteria. If a RAP product genuinely omits SBT, only the RAP IR
criterion is excluded and its precipitation/reflectivity structural criteria
remain mandatory. Partial SBT availability or download/read failures do not
activate that fallback; they fail closed. HRRR always requires SBT.
