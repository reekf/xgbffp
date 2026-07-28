#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="${SOURCE_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)}"
SITE_REPO="${SITE_REPO:-$SOURCE_DIR}"
PROJECT_DIR="/home/tyreekfrazier/ISU_Research_LOCAL_RUN/fall_2025_ml_proj"
ISSUE_DATE="${1:-$(date -u +%Y%m%d)}"
VALID_DATE="$(date -u -d "${ISSUE_DATE} +1 day" +%Y%m%d)"
PUBLISH_GIT="${PUBLISH_GIT:-1}"
STATUS_SRC="${PROJECT_DIR}/v33day2_realtime_radiusstats_forecasts/mcs_triggered_figures/status_day2_issue${ISSUE_DATE}_valid${VALID_DATE}.json"

if [[ "$PUBLISH_GIT" == "1" ]]; then
  git -C "$SITE_REPO" switch main
  git -C "$SITE_REPO" pull --ff-only origin main
fi

python "$SOURCE_DIR/realtime_day2_workflow.py" --issue-date "$ISSUE_DATE"

if [[ ! -s "$STATUS_SRC" ]]; then
  echo "ERROR: Day-2 status was not produced: $STATUS_SRC" >&2
  exit 1
fi

readarray -t OUTPUTS < <(python - "$STATUS_SRC" <<'PY'
import json, sys
status = json.load(open(sys.argv[1]))
print(status.get("data_path") or "")
print(status.get("plot_path") or "")
print("1" if status.get("triggered") else "0")
PY
)
DATA_SRC="${OUTPUTS[0]}"
PNG_SRC="${OUTPUTS[1]}"
TRIGGERED="${OUTPUTS[2]}"

if [[ "$TRIGGERED" != "1" ]]; then
  echo "No qualifying Day-2 MCS forecast for issue ${ISSUE_DATE}; nothing will be published."
  exit 0
fi
if [[ ! -s "$DATA_SRC" || ! -s "$PNG_SRC" ]]; then
  echo "ERROR: Day-2 workflow reported a forecast but its parquet or PNG is missing." >&2
  exit 1
fi

ARCHIVE_DIR="$SITE_REPO/docs/day2/archive/${VALID_DATE}"
LATEST_DIR="$SITE_REPO/docs/day2/latest"
mkdir -p "$ARCHIVE_DIR" "$LATEST_DIR"
cp "$PNG_SRC" "$ARCHIVE_DIR/latest.png"
cp "$PNG_SRC" "$LATEST_DIR/latest.png"

MPLCONFIGDIR="${MPLCONFIGDIR:-/tmp/matplotlib-cache}" python "$SOURCE_DIR/generate_interactive_map_data.py" \
  --date "$VALID_DATE" \
  --issue-date "$ISSUE_DATE" \
  --forecast-day 2 \
  --source realtime \
  --input-parquet "$DATA_SRC" \
  --output "$ARCHIVE_DIR/map.json"
cp "$ARCHIVE_DIR/map.json" "$LATEST_DIR/map.json"

python - "$ARCHIVE_DIR" "$LATEST_DIR" "$ISSUE_DATE" "$VALID_DATE" <<'PY'
import json, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

archive = Path(sys.argv[1])
latest = Path(sys.argv[2])
issue = sys.argv[3]
valid = sys.argv[4]
start = datetime.strptime(valid + "12", "%Y%m%d%H").replace(tzinfo=timezone.utc)
end = start + timedelta(days=1)
updated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
status = {
    "published": True,
    "plot_available": True,
    "map_available": True,
    "forecast_day": 2,
    "mcs_eligible": True,
    "issue_date": issue,
    "date": valid,
    "valid_start_utc": start.isoformat().replace("+00:00", "Z"),
    "valid_end_utc": end.isoformat().replace("+00:00", "Z"),
    "valid_period_label": f"{start:%Y-%m-%d} 12Z to {end:%Y-%m-%d} 12Z",
    "latest_plot": "latest.png",
    "map_data": "map.json",
    "site_updated_utc": updated,
    "map_updated_utc": updated,
    "product_description": "XGBFFP Day-2 r40/r60/r75/r100 models, ensemble mean, and WPC Day-2 ERO.",
}
for directory in (archive, latest):
    (directory / "status.json").write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")
PY

python - "$SITE_REPO/docs/day2/archive" <<'PY'
import json, sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(sys.argv[1])
entries = []
for day in sorted((p for p in root.iterdir() if p.is_dir()), reverse=True):
    path = day / "status.json"
    if not path.exists():
        continue
    status = json.loads(path.read_text())
    entries.append({
        "date": status.get("date", day.name),
        "issue_date": status.get("issue_date", ""),
        "forecast_day": 2,
        "valid_period_label": status.get("valid_period_label", ""),
        "published": bool(status.get("published")),
        "plot_available": (day / "latest.png").exists(),
        "map_available": (day / "map.json").exists(),
        "verification_available": (day / "verification.png").exists(),
        "site_updated_utc": status.get("site_updated_utc", ""),
        "map_updated_utc": status.get("map_updated_utc", ""),
        "verification_updated_utc": status.get("verification_updated_utc", ""),
        "status_href": f"day2/archive/{day.name}/status.json",
        "plot_href": f"day2/archive/{day.name}/latest.png",
        "map_href": f"day2/archive/{day.name}/map.json",
        "verification_plot_href": f"day2/archive/{day.name}/verification.png" if (day / "verification.png").exists() else None,
    })
(root / "index.json").write_text(json.dumps({
    "forecast_day": 2,
    "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "entries": entries,
}, indent=2, sort_keys=True) + "\n")
PY

# Test-set figures and model feature importance are deterministic model assets.
# Refresh them only when absent; normal forecast publication stays lightweight.
if [[ ! -s "$SITE_REPO/docs/day2/model-skill/manifest.json" || ! -s "$SITE_REPO/docs/day2/explainability/manifest.json" ]]; then
  python "$SOURCE_DIR/generate_day2_website_assets.py" --docs-dir "$SITE_REPO/docs" --project-dir "$PROJECT_DIR"
fi

git -C "$SITE_REPO" add -f docs/day2
if git -C "$SITE_REPO" diff --cached --quiet -- docs/day2; then
  echo "No Day-2 website changes to commit."
elif [[ "$PUBLISH_GIT" != "1" ]]; then
  echo "PUBLISH_GIT=$PUBLISH_GIT; Day-2 website changes are staged but not pushed."
else
  git -C "$SITE_REPO" commit -m "Publish Day-2 XGBFFP forecast issued ${ISSUE_DATE}" -- docs/day2
  git -C "$SITE_REPO" push origin main
fi
