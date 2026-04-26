#!/bin/bash
# Manual side-by-side comparison of ingest legacy vs v2.
# Not run in CI. Operator runs this on their own project after v2 is wired.
#
# Usage: bash tests/manual/benchmark_ingest.sh <project_root> <from_sha> <to_sha>
set -u

PROJECT="$1"
FROM="$2"
TO="$3"

if [ ! -d "$PROJECT/llake" ]; then
  echo "Not a LoreLake-installed project: $PROJECT" >&2; exit 2
fi

run_one() {
  local pipeline="$1"
  local proj_copy
  proj_copy=$(mktemp -d -t llake-bench.XXXXXX)
  cp -R "$PROJECT/." "$proj_copy/"
  python3 -c "
import json, sys
p = '$proj_copy/llake/config.json'
c = json.load(open(p))
c.setdefault('ingest', {})['pipeline'] = '$pipeline'
json.dump(c, open(p, 'w'), indent=2)
"
  echo "$FROM" > "$proj_copy/llake/last-ingest-sha"
  git -C "$proj_copy" reset --hard "$TO" >/dev/null 2>&1 || true
  echo "=== Running $pipeline on $FROM..$TO ==="
  local start_ts; start_ts=$(date +%s)
  LLAKE_POST_MERGE_SYNC=1 LLAKE_PROJECT_ROOT="$proj_copy" \
    bash "$(git -C "$proj_copy" rev-parse --show-toplevel)/lorelake/hooks/post-merge.sh"
  local end_ts; end_ts=$(date +%s)
  local agent_dir
  agent_dir=$(ls -td "$proj_copy/llake/.state/agents/"*/ | head -1)
  local cost
  cost=$(grep -oE 'cost=\$[0-9.]+' "$agent_dir/agent.log" | tail -1 || echo "cost=?")
  echo "  pipeline=$pipeline wall_clock=$((end_ts - start_ts))s $cost"
  echo "  agent_dir=$agent_dir"
}

run_one legacy
run_one v2
