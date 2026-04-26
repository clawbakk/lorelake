#!/bin/bash
# LoreLake post-merge lock — ensures only one post-merge invocation runs at a
# time per project. Bash 3.2 portable; uses `mkdir` (atomic) instead of flock
# (not standard on macOS).
#
# Usage:
#   STATE_DIR=<...>; LOG_FILE=<...>; HOOK_NAME=<...>
#   source "$LIB_DIR/post-merge-lock.sh"
#   if ! acquire_post_merge_lock; then
#     hook_end "skipped: lock held by another post-merge" "$LOG_FILE"
#     exit 0
#   fi
#   trap 'release_post_merge_lock' EXIT
#   ... do work ...
#
# Stale lock policy: if the lock dir's mtime is older than 1 hour AND the
# recorded owner PID is no longer alive, the lock is reclaimed.

LLAKE_LOCK_STALE_SECONDS=3600

_llake_lock_dir() {
  echo "$STATE_DIR/post-merge.lock.d"
}

_llake_lock_age_seconds() {
  local dir="$1"
  local mtime now
  if [ ! -d "$dir" ]; then echo 0; return; fi
  # macOS `stat` and GNU `stat` differ; try macOS first, fall back to GNU.
  mtime=$(stat -f %m "$dir" 2>/dev/null) \
    || mtime=$(stat -c %Y "$dir" 2>/dev/null) \
    || mtime=$(date +%s)
  now=$(date +%s)
  echo $((now - mtime))
}

_llake_lock_owner_alive() {
  local dir="$1"
  local pid_file="$dir/owner.pid"
  [ -f "$pid_file" ] || return 1
  local pid
  pid=$(cat "$pid_file" 2>/dev/null)
  [ -n "$pid" ] || return 1
  kill -0 "$pid" 2>/dev/null
}

acquire_post_merge_lock() {
  local lockdir
  lockdir=$(_llake_lock_dir)
  if mkdir "$lockdir" 2>/dev/null; then
    echo $$ > "$lockdir/owner.pid"
    return 0
  fi
  # Lock exists. Stale?
  local age
  age=$(_llake_lock_age_seconds "$lockdir")
  if [ "$age" -ge "$LLAKE_LOCK_STALE_SECONDS" ] && ! _llake_lock_owner_alive "$lockdir"; then
    printf "%s | %-13s | reclaiming stale lock (age=%ss, owner=%s)\n" \
      "$(date '+%Y-%m-%d %H:%M:%S')" "$HOOK_NAME" \
      "$age" "$(cat "$lockdir/owner.pid" 2>/dev/null || echo unknown)" \
      >> "$LOG_FILE"
    rm -rf "$lockdir"
    if mkdir "$lockdir" 2>/dev/null; then
      echo $$ > "$lockdir/owner.pid"
      return 0
    fi
  fi
  return 1
}

release_post_merge_lock() {
  local lockdir
  lockdir=$(_llake_lock_dir)
  # Only remove if we own it (matches our PID)
  if [ -f "$lockdir/owner.pid" ] && [ "$(cat "$lockdir/owner.pid" 2>/dev/null)" = "$$" ]; then
    rm -rf "$lockdir"
  fi
}
