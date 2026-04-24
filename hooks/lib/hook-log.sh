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
