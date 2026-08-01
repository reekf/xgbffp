#!/usr/bin/env bash
set -uo pipefail

SOURCE_DIR="${SOURCE_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)}"
SITE_REPO="${SITE_REPO:-$SOURCE_DIR}"
VERIFY_LOOKBACK_DAYS="${VERIFY_LOOKBACK_DAYS:-7}"
PUBLISH_GIT="${PUBLISH_GIT:-1}"
LOCK_FILE="${DAY2_VERIFY_CATCHUP_LOCK_FILE:-/tmp/xgbffp-day2-verification-catchup.lock}"

if ! [[ "$VERIFY_LOOKBACK_DAYS" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: VERIFY_LOOKBACK_DAYS must be a positive integer, got ${VERIFY_LOOKBACK_DAYS}" >&2
  exit 2
fi

cd "$SITE_REPO"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "Another Day-2 verification publish is already running; skipping catch-up."
  exit 0
fi

verification_is_eligible() {
  local archive_dir="$1"
  python - "$archive_dir/status.json" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    status = json.loads(Path(sys.argv[1]).read_text())
    valid_end = datetime.fromisoformat(status["valid_end_utc"].replace("Z", "+00:00"))
except (KeyError, OSError, ValueError):
    raise SystemExit(1)
raise SystemExit(0 if datetime.now(timezone.utc) >= valid_end else 1)
PY
}

verification_is_complete() {
  local archive_dir="$1"
  python - "$archive_dir" <<'PY'
import json
import sys
from pathlib import Path

archive = Path(sys.argv[1])
try:
    status = json.loads((archive / "status.json").read_text())
    map_payload = json.loads((archive / "map.json").read_text())
except (OSError, ValueError):
    raise SystemExit(1)

required_layers = {"ml_r40", "ml_r60", "ml_r75", "ml_r100", "ml_mean", "wpc", "pp"}
required_truths = {"practically_perfect", "ufvs_40km"}
verification_path = archive / "verification.png"
complete = (
    verification_path.is_file()
    and verification_path.stat().st_size > 0
    and status.get("forecast_day") == 2
    and status.get("verification_available") is True
    and status.get("verification_plot") == "verification.png"
    and map_payload.get("schema_version") == 5
    and map_payload.get("forecast_day") == 2
    and required_layers.issubset(map_payload.get("layers", {}))
    and required_truths.issubset(map_payload.get("verification_truths", {}))
)
raise SystemExit(0 if complete else 1)
PY
}

missing_issue_dates=()
missing_valid_dates=()
for ((offset = VERIFY_LOOKBACK_DAYS; offset >= 1; offset--)); do
  valid_date="$(date -u -d "${offset} days ago" +%Y%m%d)"
  archive_dir="docs/day2/archive/${valid_date}"
  if [[ ! -s "${archive_dir}/latest.png" || ! -s "${archive_dir}/status.json" ]]; then
    continue
  fi
  if verification_is_eligible "$archive_dir" && ! verification_is_complete "$archive_dir"; then
    issue_date="$(date -u -d "${valid_date:0:4}-${valid_date:4:2}-${valid_date:6:2} 1 day ago" +%Y%m%d)"
    missing_issue_dates+=("$issue_date")
    missing_valid_dates+=("$valid_date")
  fi
done

if (( ${#missing_issue_dates[@]} == 0 )); then
  echo "No eligible missing Day-2 verification outputs in the last ${VERIFY_LOOKBACK_DAYS} days."
  exit 0
fi

echo "Backfilling missing Day-2 verification valid dates: ${missing_valid_dates[*]}"
failed_valid_dates=()
for index in "${!missing_issue_dates[@]}"; do
  issue_date="${missing_issue_dates[$index]}"
  valid_date="${missing_valid_dates[$index]}"
  if ! PUBLISH_GIT="$PUBLISH_GIT" "$SOURCE_DIR/publish_day2_verification_output.sh" "$issue_date"; then
    echo "ERROR: Day-2 verification backfill failed for valid date ${valid_date}; continuing." >&2
    failed_valid_dates+=("$valid_date")
  fi
done

if (( ${#failed_valid_dates[@]} > 0 )); then
  echo "ERROR: Day-2 verification backfill failures: ${failed_valid_dates[*]}" >&2
  exit 1
fi

echo "Day-2 verification catch-up completed for valid dates: ${missing_valid_dates[*]}"
