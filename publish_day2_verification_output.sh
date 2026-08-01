#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="${SOURCE_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)}"
SITE_REPO="${SITE_REPO:-$SOURCE_DIR}"
PROJECT_DIR="/home/tyreekfrazier/ISU_Research_LOCAL_RUN/fall_2025_ml_proj"
ISSUE_DATE="${1:-$(date -u -d '2 days ago' +%Y%m%d)}"
VALID_DATE="$(date -u -d "${ISSUE_DATE} +1 day" +%Y%m%d)"
PUBLISH_GIT="${PUBLISH_GIT:-1}"
VERIFY_FORCE_UFVS="${VERIFY_FORCE_UFVS:-1}"
ARCHIVE_DIR="$SITE_REPO/docs/day2/archive/${VALID_DATE}"
STATUS_SRC="${PROJECT_DIR}/v33day2_realtime_radiusstats_forecasts/mcs_triggered_figures/status_day2_issue${ISSUE_DATE}_valid${VALID_DATE}.json"

if [[ "$PUBLISH_GIT" == "1" ]]; then
  git -C "$SITE_REPO" switch main
  git -C "$SITE_REPO" pull --ff-only origin main
fi
if [[ ! -s "$ARCHIVE_DIR/map.json" || ! -s "$ARCHIVE_DIR/status.json" ]]; then
  echo "ERROR: Issued Day-2 forecast archive is missing: $ARCHIVE_DIR" >&2
  exit 1
fi

UFVS_ARGS=()
if [[ "$VERIFY_FORCE_UFVS" == "1" ]]; then
  UFVS_ARGS+=(--force-ufvs)
fi
python "$SOURCE_DIR/realtime_day2_workflow.py" \
  --issue-date "$ISSUE_DATE" \
  --verification-only \
  "${UFVS_ARGS[@]}"

readarray -t OUTPUTS < <(python - "$STATUS_SRC" <<'PY'
import json, sys
status = json.load(open(sys.argv[1]))
if status.get("error"):
    raise SystemExit(status["error"])
print(status.get("data_path") or "")
print(status.get("plot_path") or "")
PY
)
DATA_SRC="${OUTPUTS[0]}"
PNG_SRC="${OUTPUTS[1]}"
if [[ ! -s "$DATA_SRC" || ! -s "$PNG_SRC" ]]; then
  echo "ERROR: Eligible Day-2 verification did not produce its parquet and PNG." >&2
  exit 1
fi

cp "$PNG_SRC" "$ARCHIVE_DIR/verification.png"
MPLCONFIGDIR="${MPLCONFIGDIR:-/tmp/matplotlib-cache}" python "$SOURCE_DIR/generate_interactive_map_data.py" \
  --date "$VALID_DATE" \
  --issue-date "$ISSUE_DATE" \
  --forecast-day 2 \
  --source realtime \
  --input-parquet "$DATA_SRC" \
  --output "$ARCHIVE_DIR/map.json"

python - "$ARCHIVE_DIR/map.json" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = json.loads(path.read_text())
required_layers = {"ml_r40", "ml_r60", "ml_r75", "ml_r100", "ml_mean", "wpc", "pp"}
required_truths = {"practically_perfect", "ufvs_40km"}
missing_layers = sorted(required_layers.difference(payload.get("layers", {})))
missing_truths = sorted(required_truths.difference(payload.get("verification_truths", {})))
if payload.get("schema_version") != 5 or payload.get("forecast_day") != 2 or missing_layers or missing_truths:
    raise SystemExit(
        f"ERROR: refusing to publish incomplete Day-2 verification map {path}: "
        f"schema={payload.get('schema_version')!r} forecast_day={payload.get('forecast_day')!r} "
        f"missing_layers={missing_layers} missing_truths={missing_truths}"
    )
print(f"Validated Day-2 verification map schema/layers/truths: {path}")
PY

python "$SOURCE_DIR/generate_dashboard_data.py" \
  --docs-dir "$SITE_REPO/docs" \
  --project-dir "$PROJECT_DIR" \
  --verification-only

python - "$ARCHIVE_DIR/status.json" "$SITE_REPO/docs/day2/archive/index.json" "$VALID_DATE" <<'PY'
import json, sys
from datetime import datetime, timezone
from pathlib import Path

status_path, index_path, valid = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
updated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
status = json.loads(status_path.read_text())
status.update({
    "verification_available": True,
    "verification_plot": "verification.png",
    "verification_updated_utc": updated,
    "map_updated_utc": updated,
})
status_path.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")
manifest = json.loads(index_path.read_text())
for entry in manifest.get("entries", []):
    if str(entry.get("date")) == valid:
        entry.update({
            "verification_available": True,
            "verification_plot_href": f"day2/archive/{valid}/verification.png",
            "verification_updated_utc": updated,
            "map_updated_utc": updated,
        })
index_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
PY

git -C "$SITE_REPO" add -f \
  "docs/day2/archive/${VALID_DATE}" \
  docs/day2/archive/index.json \
  docs/day2/verification
if git -C "$SITE_REPO" diff --cached --quiet -- docs/day2; then
  echo "No Day-2 verification website changes to commit."
elif [[ "$PUBLISH_GIT" != "1" ]]; then
  echo "PUBLISH_GIT=$PUBLISH_GIT; Day-2 verification changes are staged but not pushed."
else
  git -C "$SITE_REPO" commit -m "Verify Day-2 XGBFFP forecast issued ${ISSUE_DATE}" -- docs/day2
  git -C "$SITE_REPO" push origin main
fi
