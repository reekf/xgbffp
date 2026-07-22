#!/usr/bin/env bash
set -euo pipefail

SOURCE_REMOTE_URL="${SOURCE_REMOTE_URL:-https://github.com/reekf/hrrr-mcs-realtime-ml.git}"
TARGET_REMOTE_URL="${TARGET_REMOTE_URL:-https://github.com/reekf/xgbffp.git}"
PUBLISH_REPO="${PUBLISH_REPO:-/home/tyreekfrazier/ISU_Research_LOCAL_RUN/mesoanalysis/xgbffp-publisher}"
LOCK_FILE="${LOCK_FILE:-/tmp/xgbffp-daily-sync.lock}"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "Another XGBFFP daily sync is already running; exiting." >&2
  exit 1
fi

echo "======================================================================"
echo "XGBFFP dual-repository daily sync"
echo "Started UTC: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "Build Week source: ${SOURCE_REMOTE_URL}"
echo "Continued-development target: ${TARGET_REMOTE_URL}"
echo "Publisher checkout: ${PUBLISH_REPO}"
echo "======================================================================"

if [[ ! -d "${PUBLISH_REPO}/.git" ]]; then
  if [[ -e "$PUBLISH_REPO" ]]; then
    echo "ERROR: publisher path exists but is not a Git checkout: ${PUBLISH_REPO}" >&2
    exit 1
  fi
  git clone "$TARGET_REMOTE_URL" "$PUBLISH_REPO"
fi

if [[ -n "$(git -C "$PUBLISH_REPO" status --porcelain)" ]]; then
  echo "ERROR: refusing to use a dirty publisher checkout: ${PUBLISH_REPO}" >&2
  exit 1
fi

git -C "$PUBLISH_REPO" switch main
git -C "$PUBLISH_REPO" pull --ff-only origin main

if git -C "$PUBLISH_REPO" remote get-url build-week >/dev/null 2>&1; then
  git -C "$PUBLISH_REPO" remote set-url build-week "$SOURCE_REMOTE_URL"
else
  git -C "$PUBLISH_REPO" remote add build-week "$SOURCE_REMOTE_URL"
fi
git -C "$PUBLISH_REPO" fetch build-week main

SOURCE_HEAD="$(git -C "$PUBLISH_REPO" rev-parse build-week/main)"
TARGET_HEAD="$(git -C "$PUBLISH_REPO" rev-parse HEAD)"
echo "Source HEAD: ${SOURCE_HEAD}"
echo "Target HEAD before sync: ${TARGET_HEAD}"

if git -C "$PUBLISH_REPO" merge-base --is-ancestor build-week/main HEAD; then
  echo "The new repository already contains the latest Build Week repository commit."
  exit 0
fi

if ! git -C "$PUBLISH_REPO" merge --no-edit build-week/main; then
  git -C "$PUBLISH_REPO" merge --abort || true
  echo "ERROR: automatic daily merge conflicted; target repository was not pushed." >&2
  exit 1
fi

git -C "$PUBLISH_REPO" push origin main
echo "Target HEAD after sync: $(git -C "$PUBLISH_REPO" rev-parse HEAD)"
echo "Completed UTC: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
