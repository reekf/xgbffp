#!/usr/bin/env bash
set -uo pipefail

REPO_DIR="${REPO_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)}"
VERIFY_LOOKBACK_DAYS="${VERIFY_LOOKBACK_DAYS:-7}"
PUBLISH_GIT="${PUBLISH_GIT:-1}"
LOCK_FILE="${VERIFY_CATCHUP_LOCK_FILE:-/tmp/xgbffp-verification-catchup.lock}"

. "$REPO_DIR/publisher_git.sh"
xgbffp_acquire_publish_lock

if ! [[ "$VERIFY_LOOKBACK_DAYS" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: VERIFY_LOOKBACK_DAYS must be a positive integer, got ${VERIFY_LOOKBACK_DAYS}" >&2
  exit 2
fi

cd "$REPO_DIR"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "Another verification publish is already running; skipping catch-up."
  exit 0
fi

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
verification_path = archive / "verification.png"
complete = (
    verification_path.is_file()
    and verification_path.stat().st_size > 0
    and status.get("verification_available") is True
    and status.get("verification_plot") == "verification.png"
    and map_payload.get("schema_version") == 5
    and required_layers.issubset(map_payload.get("layers", {}))
)
raise SystemExit(0 if complete else 1)
PY
}

missing_dates=()
for ((offset = VERIFY_LOOKBACK_DAYS; offset >= 1; offset--)); do
  candidate="$(date -u -d "${offset} days ago" +%Y%m%d)"
  archive_dir="docs/archive/${candidate}"
  if [[ -s "${archive_dir}/latest.png" && -s "${archive_dir}/status.json" ]] && ! verification_is_complete "$archive_dir"; then
    missing_dates+=("$candidate")
  fi
done

if (( ${#missing_dates[@]} == 0 )); then
  echo "No missing verification outputs in the last ${VERIFY_LOOKBACK_DAYS} days."
  exit 0
fi

echo "Backfilling missing verification dates: ${missing_dates[*]}"
failed_dates=()
for candidate in "${missing_dates[@]}"; do
  if ! PUBLISH_GIT="$PUBLISH_GIT" "$REPO_DIR/publish_verification_output.sh" "$candidate"; then
    echo "ERROR: verification backfill failed for ${candidate}; continuing." >&2
    failed_dates+=("$candidate")
  fi
done

if (( ${#failed_dates[@]} > 0 )); then
  echo "ERROR: verification backfill failures: ${failed_dates[*]}" >&2
  exit 1
fi

echo "Verification catch-up completed for: ${missing_dates[*]}"
