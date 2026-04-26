#!/bin/bash
# LoreLake ingest v2 — orchestrator function.
# Sourced by hooks/post-merge.sh when ingest.pipeline == "v2".
#
# Pipeline:
#   1. Stage 1: Python pre-processor (build_ingest_context.py) → context dir
#   2. Stage 2: Planner agent (claude -p) → plan.json
#   3. Stage 3: Applier (apply_ingest_plan.py) → applied.json + failed.json
#   4. Optional: Fixer agent + second applier pass on failed.json
#   5. Finalize: log line, advance SHA cursor (best-effort policy)
#
# Inputs (from caller's scope):
#   $PROJECT_ROOT $LLAKE_ROOT $WIKI_ROOT $STATE_DIR $AGENTS_DIR
#   $CONFIG_FILE $LIB_DIR $PROMPTS_DIR $TEMPLATES_DIR $SCHEMA_DIR
#   $LAST_SHA $CURRENT_SHA $COMMIT_RANGE $LOG_FILE
#   $INCLUDE_PATHS (bash array)

run_ingest_v2() {
  # First three positional args are pre-computed by the caller (post-merge.sh)
  # so the watchdog has visible AGENT_LOG before run_ingest_v2 starts.
  local AGENT_ID="$1"
  local AGENT_DIR="$2"
  local AGENT_LOG="$3"

  # --- Read v2 config ---
  local plan_model plan_effort plan_budget plan_tools_json plan_tools
  local fix_model fix_effort fix_budget fix_tools_json fix_tools
  local max_retries diff_chunk_bytes v2_timeout
  plan_model=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "ingest.v2.plannerModel")
  plan_effort=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "ingest.v2.plannerEffort")
  plan_budget=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "ingest.v2.plannerBudgetUsd")
  plan_tools_json=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "ingest.v2.plannerAllowedTools")
  plan_tools=$(python3 -c "import json,sys; print(','.join(json.loads(sys.argv[1])))" "$plan_tools_json")
  if [ -z "$plan_tools" ]; then
    echo "ingest-v2: ingest.v2.plannerAllowedTools is missing or malformed" >&2
    echo "ingest-v2: cannot proceed without a known tool allowlist (refusing to run with empty)" >&2
    return 1
  fi
  fix_model=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "ingest.v2.fixerModel")
  fix_effort=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "ingest.v2.fixerEffort")
  fix_budget=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "ingest.v2.fixerBudgetUsd")
  fix_tools_json=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "ingest.v2.fixerAllowedTools")
  fix_tools=$(python3 -c "import json,sys; print(','.join(json.loads(sys.argv[1])))" "$fix_tools_json")
  if [ -z "$fix_tools" ]; then
    echo "ingest-v2: ingest.v2.fixerAllowedTools is missing or malformed" >&2
    return 1
  fi
  max_retries=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "ingest.v2.maxFixerRetries")
  diff_chunk_bytes=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "ingest.v2.diffChunkBytes")
  v2_timeout=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "ingest.v2.timeoutSeconds")

  # --- Stale tempfile sweep ---
  # _atomic_write leaves .<page>.md.tmp on SIGTERM mid-write. Clean these up
  # before starting so a previous kill doesn't leave noise in `git status`.
  # Match dotfile-prefixed names ending .md.tmp; only inside WIKI_ROOT.
  if [ -d "$WIKI_ROOT" ]; then
    find "$WIKI_ROOT" -type f -name '.*.md.tmp' -delete 2>/dev/null || true
  fi

  # AGENT_DIR already exists — caller did mkdir -p before spawning the subshell.
  local CONTEXT_DIR PLAN_FILE
  CONTEXT_DIR="$AGENT_DIR/context"
  PLAN_FILE="$AGENT_DIR/plan.json"

  cat > "$AGENT_LOG" << EOF
=== LoreLake Ingest v2 Agent: $AGENT_ID ===
Commits: ${LAST_SHA}..${CURRENT_SHA}
Started: $(date '+%Y-%m-%d %H:%M:%S')
Timeout: ${v2_timeout}s
Planner: $plan_model ($plan_effort, \$$plan_budget)
Fixer:   $fix_model ($fix_effort, \$$fix_budget), retries=$max_retries
---
EOF

  export IS_LLAKE_AGENT=true
  export LLAKE_AGENT_ID="$AGENT_ID"

  # --- Stage 1: Python pre-processor ---
  local include_args=()
  for p in "${INCLUDE_PATHS[@]}"; do include_args+=(--include "$p"); done
  if ! python3 "$LIB_DIR/build_ingest_context.py" \
      --project-root "$PROJECT_ROOT" \
      --wiki-root "$WIKI_ROOT" \
      --last-sha "$LAST_SHA" \
      --current-sha "$CURRENT_SHA" \
      "${include_args[@]}" \
      --out-dir "$CONTEXT_DIR" \
      --diff-chunk-bytes "$diff_chunk_bytes" >> "$AGENT_LOG" 2>&1; then
    echo "" >> "$AGENT_LOG"
    echo "=== STAGE 1 FAILED ===" >> "$AGENT_LOG"
    printf "%s | %-13s | failed: stage1 (agent %s)\n" \
      "$(date '+%Y-%m-%d %H:%M:%S')" "agent-done" "$AGENT_ID" >> "$LOG_FILE"
    return 1
  fi

  # --- Stage 2: Planner ---
  local PLANNER_RENDER_ERR PLANNER_PROMPT
  PLANNER_RENDER_ERR="$AGENT_DIR/planner-render.err"
  PLANNER_PROMPT=$(python3 "$LIB_DIR/render-prompt.py" \
    --templates-dir "$TEMPLATES_DIR" \
    "$PROMPTS_DIR/ingest.v2.md.tmpl" \
    "$CONFIG_FILE" \
    "AGENT_ID=$AGENT_ID" \
    "PROJECT_ROOT=$PROJECT_ROOT" \
    "LLAKE_ROOT=$LLAKE_ROOT" \
    "WIKI_ROOT=$WIKI_ROOT" \
    "COMMIT_RANGE=$COMMIT_RANGE" \
    "CONTEXT_DIR=$CONTEXT_DIR" 2>"$PLANNER_RENDER_ERR")
  local PLANNER_RENDER_EXIT=$?
  if [ "$PLANNER_RENDER_EXIT" -ne 0 ] || [ -z "$PLANNER_PROMPT" ]; then
    log_render_failure "PLANNER" "$PLANNER_RENDER_EXIT" "$(cat "$PLANNER_RENDER_ERR")" "$AGENT_LOG"
    rm -f "$PLANNER_RENDER_ERR"
    printf "%s | %-13s | render-failed: planner (agent %s)\n" \
      "$(date '+%Y-%m-%d %H:%M:%S')" "agent-done" "$AGENT_ID" >> "$LOG_FILE"
    return 1
  fi
  rm -f "$PLANNER_RENDER_ERR"

  echo "" >> "$AGENT_LOG"
  echo "=== INGEST V2 PLANNER (${AGENT_ID}_planner) ===" >> "$AGENT_LOG"

  local PLAN_MODEL_FLAG=""
  if [ -n "$plan_model" ]; then PLAN_MODEL_FLAG="--model $plan_model"; fi
  local PLAN_EFFORT_FLAG=""
  if [ -n "$plan_effort" ]; then PLAN_EFFORT_FLAG="--effort $plan_effort"; fi

  IS_LLAKE_AGENT=true LLAKE_AGENT_ID="${AGENT_ID}_planner" \
    claude $PLAN_MODEL_FLAG $PLAN_EFFORT_FLAG \
    -p "$PLANNER_PROMPT" \
    --tools "$plan_tools" \
    --allowedTools "$plan_tools" \
    --strict-mcp-config \
    --max-budget-usd "$plan_budget" \
    --output-format stream-json --verbose 2>&1 \
    | python3 "$LIB_DIR/format-agent-log.py" --extract-result "$PLAN_FILE" >> "$AGENT_LOG" 2>&1
  local _pstat=("${PIPESTATUS[@]}")
  local PLANNER_EXIT="${_pstat[0]}"
  local PLANNER_FORMATTER_EXIT="${_pstat[1]:-0}"
  if [ "$PLANNER_FORMATTER_EXIT" -ne 0 ]; then
    echo "$PLANNER_FORMATTER_EXIT" > "$AGENT_DIR/formatter-exit"
  fi

  if [ "$PLANNER_EXIT" -ne 0 ] || [ ! -s "$PLAN_FILE" ]; then
    echo "" >> "$AGENT_LOG"
    echo "=== PLANNER FAILED: exit $PLANNER_EXIT ===" >> "$AGENT_LOG"
    printf "%s | %-13s | failed: planner (agent %s, exit %s)\n" \
      "$(date '+%Y-%m-%d %H:%M:%S')" "agent-done" "$AGENT_ID" "$PLANNER_EXIT" >> "$LOG_FILE"
    return 1
  fi

  # --- Stage 3: Applier (first pass) ---
  echo "" >> "$AGENT_LOG"
  echo "=== APPLIER (first pass) ===" >> "$AGENT_LOG"
  local APPLIED="$AGENT_DIR/applied.json"
  local FAILED="$AGENT_DIR/failed.json"
  local TODAY
  TODAY=$(date '+%Y-%m-%d')

  if ! python3 "$LIB_DIR/apply_ingest_plan.py" \
      --plan "$PLAN_FILE" \
      --wiki-root "$WIKI_ROOT" \
      --llake-root "$LLAKE_ROOT" \
      --applied-out "$APPLIED" \
      --failed-out "$FAILED" \
      --today "$TODAY" >> "$AGENT_LOG" 2>&1; then
    # Schema-invalid plan → cursor held
    echo "" >> "$AGENT_LOG"
    echo "=== APPLIER FAILED (schema-invalid plan; cursor held) ===" >> "$AGENT_LOG"
    printf "%s | %-13s | failed: applier (agent %s, schema-invalid)\n" \
      "$(date '+%Y-%m-%d %H:%M:%S')" "agent-done" "$AGENT_ID" >> "$LOG_FILE"
    return 1
  fi

  # --- Optional Stage 4: Fixer ---
  local has_failures
  has_failures=$(python3 -c "import json,sys; print(len(json.load(open(sys.argv[1]))))" "$FAILED")
  if [ "$has_failures" -gt 0 ] && [ "$max_retries" -gt 0 ]; then
    _run_ingest_v2_fixer "$AGENT_ID" "$AGENT_DIR" "$AGENT_LOG" \
      "$PLAN_FILE" "$FAILED" "$TODAY" \
      "$fix_model" "$fix_effort" "$fix_budget" "$fix_tools"
  fi

  # --- Finalize ---
  # If the applier was killed before writing its outputs, the cursor must NOT
  # advance — the next post-merge re-runs the same range. Spec line 508.
  if [ ! -f "$APPLIED" ] || [ ! -f "$FAILED" ]; then
    echo "" >> "$AGENT_LOG"
    echo "=== APPLIER KILLED OR ABORTED MID-PASS (cursor held) ===" >> "$AGENT_LOG"
    printf "%s | %-13s | aborted: agent %s (no applied/failed; cursor held)\n" \
      "$(date '+%Y-%m-%d %H:%M:%S')" "agent-done" "$AGENT_ID" >> "$LOG_FILE"
    return 1
  fi

  local n_applied n_failed
  n_applied=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(len(d['updates'])+len(d['creates'])+len(d['deletes']))" "$APPLIED")
  n_failed=$(python3 -c "import json,sys; print(len(json.load(open(sys.argv[1]))))" "$FAILED")

  echo "$CURRENT_SHA" > "$LLAKE_ROOT/last-ingest-sha"

  echo "" >> "$AGENT_LOG"
  if [ "$n_failed" -eq 0 ]; then
    echo "=== COMPLETED: applied=$n_applied at $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$AGENT_LOG"
    printf "%s | %-13s | completed: agent %s (applied: %s, sha: %s)\n" \
      "$(date '+%Y-%m-%d %H:%M:%S')" "agent-done" "$AGENT_ID" "$n_applied" "${CURRENT_SHA:0:7}" >> "$LOG_FILE"
  else
    echo "=== COMPLETED (PARTIAL): applied=$n_applied failed=$n_failed at $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$AGENT_LOG"
    printf "%s | %-13s | partial: agent %s (applied: %s, failed: %s, sha: %s) — see %s\n" \
      "$(date '+%Y-%m-%d %H:%M:%S')" "agent-done" "$AGENT_ID" "$n_applied" "$n_failed" "${CURRENT_SHA:0:7}" "$FAILED" >> "$LOG_FILE"
  fi
  return 0
}

# Internal helper: run the fixer once.
_run_ingest_v2_fixer() {
  local AGENT_ID="$1" AGENT_DIR="$2" AGENT_LOG="$3"
  local PLAN_FILE="$4" FAILED="$5" TODAY="$6"
  local fix_model="$7" fix_effort="$8" fix_budget="$9" fix_tools="${10}"

  echo "" >> "$AGENT_LOG"
  echo "=== INGEST V2 FIXER (${AGENT_ID}_fixer) ===" >> "$AGENT_LOG"

  # Embed failed page bodies as a slot. Python builds the slot text.
  local FAILED_BODIES_FILE
  FAILED_BODIES_FILE="$AGENT_DIR/failed-bodies.txt"
  python3 -c "
import json, sys
from pathlib import Path
failed = json.load(open(sys.argv[1]))
wiki = Path(sys.argv[2])
out = []
for f in failed:
    slug = f['slug']
    matches = list(wiki.rglob(slug + '.md'))
    if matches:
        out.append('### ' + slug + '\n\`\`\`\n' + matches[0].read_text() + '\n\`\`\`\n')
print('\n'.join(out))
" "$FAILED" "$WIKI_ROOT" > "$FAILED_BODIES_FILE"

  local FIXER_PROMPT FIXER_RENDER_ERR
  FIXER_RENDER_ERR="$AGENT_DIR/fixer-render.err"
  FIXER_PROMPT=$(python3 "$LIB_DIR/render-prompt.py" \
    --templates-dir "$TEMPLATES_DIR" \
    "$PROMPTS_DIR/ingest.v2.fix.md.tmpl" \
    "$CONFIG_FILE" \
    "AGENT_ID=${AGENT_ID}_fixer" \
    "ORIGINAL_PLAN=$(cat "$PLAN_FILE")" \
    "FAILURES_JSON=$(cat "$FAILED")" \
    "FAILED_PAGE_BODIES=$(cat "$FAILED_BODIES_FILE")" \
    "CONTEXT_DIR=$AGENT_DIR/context" 2>"$FIXER_RENDER_ERR")
  local FIXER_RENDER_EXIT=$?
  if [ "$FIXER_RENDER_EXIT" -ne 0 ] || [ -z "$FIXER_PROMPT" ]; then
    log_render_failure "FIXER" "$FIXER_RENDER_EXIT" "$(cat "$FIXER_RENDER_ERR")" "$AGENT_LOG"
    rm -f "$FIXER_RENDER_ERR"
    return 0  # Non-fatal; first-pass results stand.
  fi
  rm -f "$FIXER_RENDER_ERR"

  local FIX_MODEL_FLAG=""
  if [ -n "$fix_model" ]; then FIX_MODEL_FLAG="--model $fix_model"; fi
  local FIX_EFFORT_FLAG=""
  if [ -n "$fix_effort" ]; then FIX_EFFORT_FLAG="--effort $fix_effort"; fi

  local FIX_PLAN="$AGENT_DIR/fix-plan.json"
  IS_LLAKE_AGENT=true LLAKE_AGENT_ID="${AGENT_ID}_fixer" \
    claude $FIX_MODEL_FLAG $FIX_EFFORT_FLAG \
    -p "$FIXER_PROMPT" \
    --tools "$fix_tools" \
    --allowedTools "$fix_tools" \
    --strict-mcp-config \
    --max-budget-usd "$fix_budget" \
    --output-format stream-json --verbose 2>&1 \
    | python3 "$LIB_DIR/format-agent-log.py" --extract-result "$FIX_PLAN" >> "$AGENT_LOG" 2>&1
  local FIXER_EXIT="${PIPESTATUS[0]}"
  if [ "$FIXER_EXIT" -ne 0 ] || [ ! -s "$FIX_PLAN" ]; then
    echo "=== FIXER FAILED: exit $FIXER_EXIT (first-pass results stand) ===" >> "$AGENT_LOG"
    return 0
  fi

  echo "" >> "$AGENT_LOG"
  echo "=== APPLIER (fix pass) ===" >> "$AGENT_LOG"
  local FINAL_APPLIED="$AGENT_DIR/final-applied.json"
  local FINAL_FAILED="$AGENT_DIR/final-failed.json"
  if python3 "$LIB_DIR/apply_ingest_plan.py" \
      --plan "$FIX_PLAN" \
      --wiki-root "$WIKI_ROOT" \
      --llake-root "$LLAKE_ROOT" \
      --applied-out "$FINAL_APPLIED" \
      --failed-out "$FINAL_FAILED" \
      --today "$TODAY" \
      --no-log-entry >> "$AGENT_LOG" 2>&1; then
    # Merge final-failed.json back into FAILED — that becomes the canonical
    # remaining-failures list for the orchestrator's finalize step.
    cp "$FINAL_FAILED" "$FAILED"
  fi
}
