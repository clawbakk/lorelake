# LoreLake plugin — project root detection.
# Provides: detect_project_root <cwd> → echoes path, exits 0 on success, 1 on no-match.
#
# Resolution order:
#   1. $LLAKE_PROJECT_ROOT env override (always wins if set and non-empty)
#   2. Marker walk: ascend from $cwd looking for llake/config.json
#   3. Caller may fall back to git: `git -C "$cwd" rev-parse --show-toplevel`
#
# This file does NOT call git itself — that is the caller's choice (post-merge
# always uses git; CC hooks always use marker walk). Keeps this lib pure.

detect_project_root() {
  local cwd=$1

  if [ -n "${LLAKE_PROJECT_ROOT:-}" ]; then
    echo "$LLAKE_PROJECT_ROOT"
    return 0
  fi

  local dir=$cwd
  while [ -n "$dir" ] && [ "$dir" != "/" ]; do
    if [ -f "$dir/llake/config.json" ]; then
      echo "$dir"
      return 0
    fi
    dir=$(dirname "$dir")
  done

  return 1
}
