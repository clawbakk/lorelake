#!/bin/bash
# LoreLake shared hook logging helpers.
#
# Functions:
#   hook_start <name> <log_file> <config_file> <lib_dir>
#     — rotates the log if over logging.maxLines, marks prior unterminated line
#       as CRASHED, appends "TS | <name> | started" with no trailing newline.
#   hook_end <outcome> <log_file>
#     — appends " → <outcome>\n" to complete the open line.
#   hook_log_line <name> <outcome> <log_file>
#     — one-shot append "TS | <name> | <outcome>\n" (for hooks with no paired
#       start/end, e.g. the async-dispatch line from session-end foreground).
#
# Bash portability: targets macOS /bin/bash 3.2.

hook_start() {
  local name="$1"
  local log_file="$2"
  local config_file="$3"
  local lib_dir="$4"

  # Mark prior line CRASHED if the previous writer exited mid-line.
  [ -f "$log_file" ] && [ -n "$(tail -c 1 "$log_file")" ] && echo " → CRASHED" >> "$log_file"

  # Rotate if over cap.
  local max_lines
  local keep_lines
  max_lines=$(python3 "$lib_dir/read-config.py" "$config_file" "logging.maxLines")
  keep_lines=$(python3 "$lib_dir/read-config.py" "$config_file" "logging.rotateKeepLines")
  if [ -f "$log_file" ] && [ "$(wc -l < "$log_file")" -gt "$max_lines" ]; then
    tail -"$keep_lines" "$log_file" > "$log_file.tmp" && mv "$log_file.tmp" "$log_file"
  fi

  printf "%s | %-13s | started" "$(date '+%Y-%m-%d %H:%M:%S')" "$name" >> "$log_file"
}

hook_end() {
  local outcome="$1"
  local log_file="$2"
  echo " → $outcome" >> "$log_file"
}

hook_log_line() {
  local name="$1"
  local outcome="$2"
  local log_file="$3"
  printf "%s | %-13s | %s\n" "$(date '+%Y-%m-%d %H:%M:%S')" "$name" "$outcome" >> "$log_file"
}

# log_render_failure — appends a multi-line "=== <LABEL> RENDER FAILED ==="
# block to the agent log. Used by both post-merge.sh and the worker.
#
# Args:
#   $1 label      — empty or "TRIAGE" / "CAPTURE" (already uppercased)
#   $2 exit_code  — the renderer's exit code
#   $3 err_text   — captured stderr (may be empty)
#   $4 agent_log  — path to the agent log file
log_render_failure() {
  local label="$1"
  local exit_code="$2"
  local err_text="$3"
  local agent_log="$4"

  local header_prefix=""
  [ -n "$label" ] && header_prefix="$label "

  {
    echo ""
    echo "=== ${header_prefix}RENDER FAILED: exit $exit_code at $(date '+%Y-%m-%d %H:%M:%S') ==="
    if [ -n "$err_text" ]; then
      echo "$err_text"
    else
      echo "(renderer produced empty prompt with exit $exit_code)"
    fi
  } >> "$agent_log"
}

# render_err_summary — produces a sanitized one-line summary of renderer
# stderr suitable for inclusion in hooks.log. Strips '|' (the field
# separator), CR, LF, and tab. Caps at 200 chars. Returns "empty prompt"
# if the input is empty.
#
# Args:
#   $1 err_text — captured renderer stderr
# Outputs (stdout): the sanitized summary
render_err_summary() {
  local err_text="$1"
  local summary
  summary=$(printf '%s' "$err_text" | head -1 | tr -d '\r\n\t|' | cut -c1-200)
  [ -z "$summary" ] && summary="empty prompt"
  printf '%s' "$summary"
}
