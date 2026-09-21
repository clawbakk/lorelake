#!/bin/bash
# LoreLake ingest cursor — advances the ingest cursor and its clock together.
#
# Required environment (set by the caller):
#   LLAKE_ROOT  — <project>/llake
#   STATE_DIR   — <project>/llake/.state
#
# Usage:
#   advance_ingest_cursor "$CURRENT_SHA"
#
# The two files are meant to move together via advance_ingest_cursor.
# `last-ingest-sha` is the commit-range cursor; `.state/last-ingest-at` is
# the clock the batching gate's age arm reads. If they drift, the gate
# measures age from the wrong moment. Bootstrap and doctor's repair still
# write the SHA alone (schema/operations.md, skills/llake-doctor/SKILL.md);
# both are self-healed by ensure_ingest_clock on the next post-merge.
#
# `last-ingest-at` means "the wiki is known-current as of T", which is why an
# empty-pile skip resets it too — after that skip the wiki genuinely is current.
#
# Bash 3.2 portable.

advance_ingest_cursor() {
  local sha="$1"
  [ -n "$sha" ] || return 1
  mkdir -p "$STATE_DIR" 2>/dev/null
  echo "$sha" > "$LLAKE_ROOT/last-ingest-sha"
  date +%s > "$STATE_DIR/last-ingest-at"
}

# Seed the clock if it is absent, so the gate always reads a real timestamp.
#
# `.state/` is gitignored and does not survive a fresh clone. Without this, a
# missing clock would read as "overdue" and force a full ingest on the first
# merge in every clone, on every machine. Seeding starts that clone's own
# window instead. Never overwrites an existing clock.
ensure_ingest_clock() {
  mkdir -p "$STATE_DIR" 2>/dev/null
  [ -f "$STATE_DIR/last-ingest-at" ] && return 0
  date +%s > "$STATE_DIR/last-ingest-at"
}
