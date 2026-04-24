#!/bin/bash
# Test helper: create a minimal LoreLake project in a tempdir.
# Usage (after sourcing):
#   PROJECT_DIR=$(mkproject)                # defaults: branch=main, min timeouts
#   PROJECT_DIR=$(mkproject other-branch)
#
# Produces:
#   $PROJECT_DIR/.git            — fresh git repo with one commit
#   $PROJECT_DIR/llake/config.json   — minimal config matching the shipped defaults
#                                      except for tight timeouts suitable for tests
#   $PROJECT_DIR/llake/last-ingest-sha — baseline (HEAD after first commit)
#   $PROJECT_DIR/llake/wiki/     — empty dir
#   $PROJECT_DIR/src/hello.txt   — a content file so `ingest.include: ["src/"]` matches
#
# Caller is responsible for `rm -rf "$PROJECT_DIR"` when done.

mkproject() {
  local branch="${1:-main}"
  local dir
  dir=$(mktemp -d -t llake-test-project.XXXXXX)

  (
    cd "$dir" || exit 1
    git init -q -b "$branch" 2>/dev/null || { git init -q; git checkout -q -b "$branch"; }
    git config user.email "test@example.com"
    git config user.name "Test"
    git config commit.gpgsign false

    mkdir -p llake/wiki src

    cat > llake/config.json <<'CFG'
{
  "_schemaVersion": 1,
  "sessionCapture": {
    "enabled": true,
    "minTurns": 2,
    "minWords": 50,
    "triageModel": "sonnet",
    "triageEffort": "high",
    "triageBudgetUsd": 0.50,
    "captureModel": "sonnet",
    "captureEffort": "high",
    "maxBudgetUsd": 5.00,
    "timeoutSeconds": 10,
    "allowedTools": ["Read", "Write"],
    "writableCategories": ["discussions"],
    "lockStalenessSeconds": 900
  },
  "ingest": {
    "enabled": true,
    "model": "opus",
    "effort": "high",
    "maxBudgetUsd": 10.00,
    "timeoutSeconds": 10,
    "branch": "main",
    "include": ["src/"],
    "allowedTools": ["Read", "Write"]
  },
  "logging": {
    "maxLines": 1000,
    "rotateKeepLines": 500
  },
  "transcript": {
    "maxMessageLength": 2000,
    "headSize": 10,
    "tailSize": 20,
    "middleMaxSize": 30,
    "middleScaleStart": 100
  }
}
CFG

    echo "hello world" > src/hello.txt
    git add -A
    git commit -q -m "initial commit"
    git rev-parse HEAD > llake/last-ingest-sha
  ) >/dev/null

  echo "$dir"
}

# Add a second commit touching src/ — used by tests that need new commits.
add_src_commit() {
  local dir="$1"
  local msg="${2:-second commit}"
  (
    cd "$dir" || exit 1
    echo "more" >> src/hello.txt
    git add -A
    git commit -q -m "$msg"
  ) >/dev/null
}

# Add a commit that does NOT touch src/ — used by "no relevant files" test.
add_nonsrc_commit() {
  local dir="$1"
  (
    cd "$dir" || exit 1
    echo "root" > ROOT.md
    git add -A
    git commit -q -m "root change"
  ) >/dev/null
}
