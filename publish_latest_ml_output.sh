#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)}"
PROJECT_DIR="/home/tyreekfrazier/ISU_Research_LOCAL_RUN/fall_2025_ml_proj"
OUT_DIR="${PROJECT_DIR}/v33_realtime_radiusstats_forecasts/mcs_triggered_figures"

DATE_ARG="${1:-$(date -u +%Y%m%d)}"
RADII="${RADII:-40 60 75 100}"
PUBLISH_GIT="${PUBLISH_GIT:-1}"
REQUIRE_GIT_SYNC="${REQUIRE_GIT_SYNC:-0}"
PUBLIC_PNG_NAME="realtime_ml_public_${DATE_ARG}_valid12to12_radii_wpc.png"
PUBLIC_PNG_SRC="${OUT_DIR}/${PUBLIC_PNG_NAME}"

cd "$REPO_DIR"

echo "======================================================================"
echo "Realtime ML site publish"
echo "Date: ${DATE_ARG}"
echo "Started UTC: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "Repo: ${REPO_DIR}"
echo "Output dir: ${OUT_DIR}"
echo "======================================================================"

# Pages deploys from main/docs. A transient remote/TLS failure should not stop
# local forecast generation; commit/push will still fail if publishing is
# requested and the network remains broken.
if [[ "$PUBLISH_GIT" == "1" ]]; then
  git switch main
  if ! git pull --ff-only origin main; then
    echo "WARNING: git pull failed; continuing with local checkout." >&2
    if [[ "$REQUIRE_GIT_SYNC" == "1" ]]; then
      echo "ERROR: REQUIRE_GIT_SYNC=1 and git pull failed." >&2
      exit 1
    fi
  fi
fi

# Prevent stale contour-era graphics from being copied if the plotter fails.
rm -f "${PUBLIC_PNG_SRC}"

echo
echo "Running realtime ML plotter..."
python realtime_mcs_trigger_plot.py \
  --date "$DATE_ARG" \
  --radii $RADII

echo
echo "Finding script outputs..."

STATUS_SRC="$(find "$OUT_DIR" -maxdepth 1 -type f -name "status_${DATE_ARG}_*.json" -printf "%T@ %p\n" 2>/dev/null | sort -nr | head -1 | cut -d' ' -f2- || true)"
PNG_SRC=""
if [[ -f "${PUBLIC_PNG_SRC}" ]]; then
  PNG_SRC="${PUBLIC_PNG_SRC}"
fi

mkdir -p docs/latest "docs/archive/${DATE_ARG}"

# Build a public status JSON from scratch. Do not expose internal generation-gate metadata.
write_public_status() {
  local dst="$1"
  local published="$2"
  local plot_available="$3"
  local message="${4:-}"
  python - "$DATE_ARG" "$dst" "$published" "$plot_available" "$message" "$STATUS_SRC" <<'PY'
import json
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

date, dst, published, plot_available, message, internal_status = sys.argv[1:7]
published = published.lower() == "true"
plot_available = plot_available.lower() == "true"

start = datetime.strptime(date + "12", "%Y%m%d%H").replace(tzinfo=timezone.utc)
end = start + timedelta(days=1)
status = {
    "published": published,
    "plot_available": plot_available,
    "date": date,
    "valid_start_utc": start.isoformat().replace("+00:00", "Z"),
    "valid_end_utc": end.isoformat().replace("+00:00", "Z"),
    "valid_period_label": f"{start:%Y-%m-%d} 12Z to {end:%Y-%m-%d} 12Z",
    "latest_plot": "latest.png" if plot_available else None,
    "site_updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "product_description": "Machine-learning radius products including r60kmV2, ensemble mean, and WPC ERO.",
}
if message:
    status["message"] = message
elif not plot_available:
    status["message"] = "No forecast graphic is available for this date."
internal_path = Path(internal_status) if internal_status else None
if internal_path and internal_path.is_file():
    internal = json.loads(internal_path.read_text())
    detection = internal.get("mcs_detection") or {}
    eligible = bool(detection.get("triggered", internal.get("triggered", False)))
    status.update({
        "mcs_eligible": eligible,
        "mcs_classification_label": (
            "MCS-associated precipitation"
            if eligible
            else "Non-MCS-associated precipitation"
        ),
        "mcs_classification": {
            "method": "HRRR-only actual PyFLEXTRKR gate",
            "pyflextrkr_package_version": detection.get("pyflextrkr_package_version"),
            "pyflextrkr_upstream_commit": detection.get("pyflextrkr_upstream_commit"),
            "official_steps_completed": detection.get("pyflextrkr_official_steps_completed", []),
            "cloud_shield": "HRRR SBT < 241 K with area >60000 km2 for at least 3 continuous hours",
            "precipitation_feature": "HRRR >=25 dBZ connected feature with major axis >100 km for at least 4 continuous hours",
            "convective_feature": "HRRR composite simulated reflectivity >45 dBZ within the precipitation feature for at least 4 continuous hours",
            "ir_duration_met": detection.get("ir_duration_met"),
            "structural_duration_met": detection.get("structural_duration_met"),
            "max_ir_duration_hours": detection.get("max_ir_duration_hours"),
            "max_joint_duration_hours": detection.get("max_joint_duration_hours"),
            "hrrr_criterion_met": detection.get("hrrr_triggered"),
            "model_results": detection.get("model_results", {}),
        },
    })
with open(dst, "w") as f:
    json.dump(status, f, indent=2, sort_keys=True)
    f.write("\n")
PY
}

if [[ -n "${PNG_SRC}" && -f "${PNG_SRC}" ]]; then
  echo "Public PNG source: ${PNG_SRC}"
  cp "${PNG_SRC}" docs/latest/latest.png
  cp "${PNG_SRC}" "docs/archive/${DATE_ARG}/latest.png"
  rm -f "docs/archive/${DATE_ARG}/map.json" docs/latest/map.json
  MPLCONFIGDIR="${MPLCONFIGDIR:-/tmp/matplotlib-cache}" python generate_interactive_map_data.py \
    --date "$DATE_ARG" \
    --source realtime \
    --output "docs/archive/${DATE_ARG}/map.json"
  python - "docs/archive/${DATE_ARG}/map.json" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = json.loads(path.read_text())
required = {"ml_r40", "ml_r60", "ml_r60v2", "ml_r75", "ml_r100", "ml_mean", "wpc"}
missing = sorted(required.difference(payload.get("layers", {})))
if payload.get("schema_version") != 5 or missing:
    raise SystemExit(
        f"ERROR: refusing to publish incomplete realtime map {path}: "
        f"schema={payload.get('schema_version')!r} missing_layers={missing}"
    )
print(f"Validated realtime map schema/layers: {path}")
PY
  cp "docs/archive/${DATE_ARG}/map.json" docs/latest/map.json
  write_public_status docs/latest/status.json true true ""
  python - docs/latest/status.json <<'PY'
import json
import sys
from pathlib import Path
path = Path(sys.argv[1])
status = json.loads(path.read_text())
status.update({"map_available": True, "map_data": "map.json", "map_updated_utc": status["site_updated_utc"]})
path.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")
PY
  cp docs/latest/status.json "docs/archive/${DATE_ARG}/status.json"
else
  echo "WARNING: Public PNG was not produced: ${PUBLIC_PNG_SRC}"
  rm -f docs/latest/latest.png docs/latest/map.json "docs/archive/${DATE_ARG}/latest.png" "docs/archive/${DATE_ARG}/map.json"
  write_public_status docs/latest/status.json false false "Realtime script ran, but no public forecast graphic was produced."
  cp docs/latest/status.json "docs/archive/${DATE_ARG}/status.json"
fi

MPING_TOKEN_FILE="${MPING_API_TOKEN_FILE:-${HOME}/.config/realtime-ml/mping-token}"
if [[ -n "${MPING_API_TOKEN:-}" || -s "$MPING_TOKEN_FILE" ]]; then
  if ! python fetch_mping_reports.py --date "$DATE_ARG" --output "docs/archive/${DATE_ARG}/mping.json"; then
    echo "WARNING: mPING report refresh failed; preserving any existing public mPING file." >&2
  fi
else
  echo "No mPING API token is configured; skipping mPING report refresh."
fi

# Build archive manifest from all public archived status files.
python - <<'PY'
import json
from pathlib import Path
from datetime import datetime, timezone

archive_root = Path("docs/archive")
entries = []
if archive_root.exists():
    for day_dir in sorted([p for p in archive_root.iterdir() if p.is_dir()], reverse=True):
        status_path = day_dir / "status.json"
        if not status_path.exists():
            continue
        try:
            status = json.loads(status_path.read_text())
        except Exception:
            status = {}
        date = status.get("date") or day_dir.name
        plot_exists = (day_dir / "latest.png").exists()
        map_exists = (day_dir / "map.json").exists()
        verification_exists = (day_dir / "verification.png").exists()
        verification_embedded = bool(status.get("verification_embedded_in_forecast", False)) or (
            "practically perfect verification" in str(status.get("product_description", "")).lower()
        )
        entries.append({
            "date": str(date),
            "valid_period_label": status.get("valid_period_label", ""),
            "published": bool(status.get("published", False)),
            "plot_available": bool(plot_exists and status.get("plot_available", False)),
            "site_updated_utc": status.get("site_updated_utc", ""),
            "status_href": f"archive/{day_dir.name}/status.json",
            "plot_href": f"archive/{day_dir.name}/latest.png" if plot_exists else None,
            "map_available": bool(map_exists),
            "map_href": f"archive/{day_dir.name}/map.json" if map_exists else None,
            "map_updated_utc": status.get("map_updated_utc", status.get("site_updated_utc", "")),
            "verification_available": bool(verification_exists or (verification_embedded and plot_exists)),
            "verification_plot_href": (
                f"archive/{day_dir.name}/verification.png" if verification_exists
                else (f"archive/{day_dir.name}/latest.png" if verification_embedded and plot_exists else None)
            ),
            "verification_embedded_in_forecast": bool(verification_embedded and not verification_exists),
            "verification_updated_utc": status.get("verification_updated_utc", status.get("site_updated_utc", "")),
            "mcs_eligible": status.get("mcs_eligible", True),
            "mcs_classification_label": status.get("mcs_classification_label", "MCS classification not audited"),
            "verification_included": status.get("mcs_eligible", True),
        })

out = {
    "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "entries": entries,
}
Path("docs/archive").mkdir(parents=True, exist_ok=True)
Path("docs/archive/index.json").write_text(json.dumps(out, indent=2, sort_keys=True))
PY

echo
echo "Committing website update if changed..."

git add -f docs/latest/status.json docs/archive/index.json
if [[ -f docs/latest/latest.png ]]; then
  git add -f docs/latest/latest.png
else
  git rm -f --ignore-unmatch docs/latest/latest.png >/dev/null 2>&1 || true
fi
if [[ -f docs/latest/map.json ]]; then
  git add -f docs/latest/map.json
else
  git rm -f --ignore-unmatch docs/latest/map.json >/dev/null 2>&1 || true
fi
git add -f "docs/archive/${DATE_ARG}/status.json"
if [[ -f "docs/archive/${DATE_ARG}/latest.png" ]]; then
  git add -f "docs/archive/${DATE_ARG}/latest.png"
else
  git rm -f --ignore-unmatch "docs/archive/${DATE_ARG}/latest.png" >/dev/null 2>&1 || true
fi
if [[ -f "docs/archive/${DATE_ARG}/map.json" ]]; then
  git add -f "docs/archive/${DATE_ARG}/map.json"
else
  git rm -f --ignore-unmatch "docs/archive/${DATE_ARG}/map.json" >/dev/null 2>&1 || true
fi
if [[ -f "docs/archive/${DATE_ARG}/mping.json" ]]; then
  git add -f "docs/archive/${DATE_ARG}/mping.json"
fi

PUBLISH_PATHS=(docs/latest/status.json docs/latest/map.json docs/archive/index.json docs/latest/latest.png "docs/archive/${DATE_ARG}/status.json" "docs/archive/${DATE_ARG}/latest.png" "docs/archive/${DATE_ARG}/map.json")
if [[ -f "docs/archive/${DATE_ARG}/mping.json" ]]; then
  PUBLISH_PATHS+=("docs/archive/${DATE_ARG}/mping.json")
fi
if git diff --cached --quiet -- "${PUBLISH_PATHS[@]}"; then
  echo "No website changes to commit."
else
  if [[ "$PUBLISH_GIT" != "1" ]]; then
    echo "PUBLISH_GIT=${PUBLISH_GIT}; leaving website changes staged without committing or pushing."
  else
    git commit -m "Publish realtime ML forecast for ${DATE_ARG}" -- "${PUBLISH_PATHS[@]}"
    git push origin main
  fi
fi

echo
echo "Done UTC: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
