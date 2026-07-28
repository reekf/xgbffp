#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)}"
PROJECT_DIR="/home/tyreekfrazier/ISU_Research_LOCAL_RUN/fall_2025_ml_proj"
OUT_DIR="${PROJECT_DIR}/v33_realtime_radiusstats_forecasts/mcs_triggered_figures"

DATE_ARG="${1:-$(date -u -d 'yesterday' +%Y%m%d)}"
RADII="${RADII:-40 60 75 100}"
PUBLISH_GIT="${PUBLISH_GIT:-1}"
VERIFY_FORCE_UFVS="${VERIFY_FORCE_UFVS:-1}"
VERIFICATION_NAME="realtime_ml_verification_${DATE_ARG}_valid12to12_radii_pp.png"
VERIFICATION_SRC="${OUT_DIR}/${VERIFICATION_NAME}"
ARCHIVE_DIR="docs/archive/${DATE_ARG}"

cd "$REPO_DIR"

echo "Publishing ML verification for forecast date ${DATE_ARG}"
if [[ "$PUBLISH_GIT" == "1" ]]; then
  git switch main
  if ! git pull --ff-only origin main; then
    echo "WARNING: git pull failed; continuing with local checkout." >&2
    if [[ "${REQUIRE_GIT_SYNC:-0}" == "1" ]]; then
      echo "ERROR: REQUIRE_GIT_SYNC=1 and git pull failed." >&2
      exit 1
    fi
  fi
fi

if [[ ! -f "${ARCHIVE_DIR}/latest.png" || ! -f "${ARCHIVE_DIR}/status.json" ]]; then
  echo "ERROR: Forecast archive is missing for ${DATE_ARG}: ${ARCHIVE_DIR}" >&2
  exit 1
fi

# Remove the expected output first so a failed run cannot republish a stale image.
rm -f "$VERIFICATION_SRC"
UFVS_ARGS=()
if [[ "$VERIFY_FORCE_UFVS" == "1" ]]; then
  UFVS_ARGS+=(--force-ufvs)
fi
python realtime_mcs_trigger_plot.py \
  --date "$DATE_ARG" \
  --radii $RADII \
  --verification-only \
  "${UFVS_ARGS[@]}"

if [[ ! -s "$VERIFICATION_SRC" ]]; then
  echo "ERROR: Verification graphic was not produced: ${VERIFICATION_SRC}" >&2
  exit 1
fi

cp "$VERIFICATION_SRC" "${ARCHIVE_DIR}/verification.png"
MPLCONFIGDIR="${MPLCONFIGDIR:-/tmp/matplotlib-cache}" python generate_interactive_map_data.py \
  --date "$DATE_ARG" \
  --source realtime \
  --output "${ARCHIVE_DIR}/map.json"
python - "${ARCHIVE_DIR}/map.json" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = json.loads(path.read_text())
required = {"ml_r40", "ml_r60", "ml_r75", "ml_r100", "ml_mean", "wpc", "pp"}
missing = sorted(required.difference(payload.get("layers", {})))
if payload.get("schema_version") != 5 or missing:
    raise SystemExit(
        f"ERROR: refusing to publish incomplete verification map {path}: "
        f"schema={payload.get('schema_version')!r} missing_layers={missing}"
    )
print(f"Validated verification map schema/layers: {path}")
PY

# Refresh machine-readable issued-forecast verification after the verified map
# is in place. This pools contingency counts across dates and does not retrain.
python generate_dashboard_data.py --verification-only

MPING_TOKEN_FILE="${MPING_API_TOKEN_FILE:-${HOME}/.config/realtime-ml/mping-token}"
if [[ -n "${MPING_API_TOKEN:-}" || -s "$MPING_TOKEN_FILE" ]]; then
  if ! python fetch_mping_reports.py --date "$DATE_ARG" --output "${ARCHIVE_DIR}/mping.json"; then
    echo "WARNING: mPING report refresh failed; preserving any existing public mPING file." >&2
  fi
else
  echo "No mPING API token is configured; skipping mPING report refresh."
fi

python - "$DATE_ARG" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

date = sys.argv[1]
archive_root = Path("docs/archive")
day_dir = archive_root / date
status_path = day_dir / "status.json"
status = json.loads(status_path.read_text())
updated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
status.update({
    "verification_available": True,
    "verification_plot": "verification.png",
    "verification_updated_utc": updated,
    "map_available": True,
    "map_data": "map.json",
    "map_updated_utc": updated,
})
status_path.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")

entries = []
for candidate in sorted((p for p in archive_root.iterdir() if p.is_dir()), reverse=True):
    candidate_status_path = candidate / "status.json"
    if not candidate_status_path.exists():
        continue
    try:
        candidate_status = json.loads(candidate_status_path.read_text())
    except Exception:
        candidate_status = {}
    forecast_exists = (candidate / "latest.png").exists()
    map_exists = (candidate / "map.json").exists()
    verification_exists = (candidate / "verification.png").exists()
    verification_embedded = bool(candidate_status.get("verification_embedded_in_forecast", False)) or (
        "practically perfect verification" in str(candidate_status.get("product_description", "")).lower()
    )
    entries.append({
        "date": str(candidate_status.get("date") or candidate.name),
        "valid_period_label": candidate_status.get("valid_period_label", ""),
        "published": bool(candidate_status.get("published", False)),
        "plot_available": bool(forecast_exists and candidate_status.get("plot_available", False)),
        "site_updated_utc": candidate_status.get("site_updated_utc", ""),
        "status_href": f"archive/{candidate.name}/status.json",
        "plot_href": f"archive/{candidate.name}/latest.png" if forecast_exists else None,
        "map_available": bool(map_exists),
        "map_href": f"archive/{candidate.name}/map.json" if map_exists else None,
        "map_updated_utc": candidate_status.get("map_updated_utc", candidate_status.get("site_updated_utc", "")),
        "verification_available": bool(verification_exists or (verification_embedded and forecast_exists)),
        "verification_plot_href": (
            f"archive/{candidate.name}/verification.png" if verification_exists
            else (f"archive/{candidate.name}/latest.png" if verification_embedded and forecast_exists else None)
        ),
        "verification_embedded_in_forecast": bool(verification_embedded and not verification_exists),
        "verification_updated_utc": candidate_status.get("verification_updated_utc", candidate_status.get("site_updated_utc", "")),
        "mcs_eligible": candidate_status.get("mcs_eligible", True),
        "mcs_classification_label": candidate_status.get("mcs_classification_label", "MCS classification not audited"),
        "verification_included": candidate_status.get("mcs_eligible", True),
    })

manifest = {"generated_utc": updated, "entries": entries}
(archive_root / "index.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
PY

git add -f "${ARCHIVE_DIR}/verification.png" "${ARCHIVE_DIR}/map.json" "${ARCHIVE_DIR}/status.json" docs/archive/index.json docs/verification
if [[ -f "${ARCHIVE_DIR}/mping.json" ]]; then
  git add -f "${ARCHIVE_DIR}/mping.json"
fi
PUBLISH_PATHS=("${ARCHIVE_DIR}/verification.png" "${ARCHIVE_DIR}/map.json" "${ARCHIVE_DIR}/status.json" docs/archive/index.json docs/verification)
if [[ -f "${ARCHIVE_DIR}/mping.json" ]]; then
  PUBLISH_PATHS+=("${ARCHIVE_DIR}/mping.json")
fi
if git diff --cached --quiet -- "${PUBLISH_PATHS[@]}"; then
  echo "No verification website changes to commit."
elif [[ "$PUBLISH_GIT" != "1" ]]; then
  echo "PUBLISH_GIT=${PUBLISH_GIT}; leaving verification website changes staged without committing or pushing."
else
  git commit -m "Publish ML verification for ${DATE_ARG}" -- "${PUBLISH_PATHS[@]}"
  git push origin main
fi
