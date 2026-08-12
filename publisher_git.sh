#!/usr/bin/env bash

# Shared Git coordination for every publisher that writes this checkout.

xgbffp_acquire_publish_lock() {
  if [[ "${XGBFFP_PUBLISH_LOCK_HELD:-0}" == "1" ]]; then
    return 0
  fi

  local lock_file="${XGBFFP_PUBLISH_LOCK_FILE:-/tmp/xgbffp-publish.lock}"
  exec {XGBFFP_PUBLISH_LOCK_FD}>"$lock_file"
  echo "Waiting for XGBFFP publish lock: $lock_file"
  flock "$XGBFFP_PUBLISH_LOCK_FD"
  export XGBFFP_PUBLISH_LOCK_HELD=1
}

xgbffp_sync_main() {
  local repo_dir="$1"
  local require_sync="${2:-0}"

  git -C "$repo_dir" switch main
  if ! git -C "$repo_dir" fetch origin main; then
    echo "WARNING: git fetch failed; continuing with the local checkout." >&2
    if [[ "$require_sync" == "1" ]]; then
      echo "ERROR: REQUIRE_GIT_SYNC=1 and git fetch failed." >&2
      return 1
    fi
    return 0
  fi

  if ! git -C "$repo_dir" rebase --autostash origin/main; then
    git -C "$repo_dir" rebase --abort >/dev/null 2>&1 || true
    echo "ERROR: local publisher commits conflict with origin/main; publishing stopped before generation." >&2
    return 1
  fi
}

xgbffp_push_main() {
  local repo_dir="$1"
  local attempt

  for attempt in 1 2; do
    if git -C "$repo_dir" push origin main; then
      return 0
    fi
    if [[ "$attempt" == "2" ]]; then
      break
    fi

    echo "WARNING: push was rejected; rebasing the publish commit onto the latest origin/main and retrying." >&2
    if ! git -C "$repo_dir" fetch origin main; then
      echo "ERROR: could not fetch origin/main for the push retry." >&2
      return 1
    fi
    if ! git -C "$repo_dir" rebase --autostash origin/main; then
      git -C "$repo_dir" rebase --abort >/dev/null 2>&1 || true
      echo "ERROR: the publish commit conflicts with the new remote tip; push retry aborted safely." >&2
      return 1
    fi
  done

  echo "ERROR: failed to push the XGBFFP publish commit after retrying." >&2
  return 1
}
