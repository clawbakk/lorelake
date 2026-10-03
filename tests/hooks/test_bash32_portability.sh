#!/bin/bash
# Static bash 3.2 portability check (macOS /bin/bash) for every shell file the plugin ships or tests with:
# no bash-4 constructs outside comments, and every file parses under the system bash.
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SELF="$SCRIPT_DIR/$(basename "$0")"
BASH_BIN=/bin/bash
[ -x "$BASH_BIN" ] || BASH_BIN=$(command -v bash)

PASS=0; FAIL=0
BASH4='declare -A|local -A|mapfile|readarray|\$\{[A-Za-z_][A-Za-z0-9_]*(,,|\^\^)|BASHPID|wait -n|&>>|\|&|coproc|declare -n|local -n'

for f in "$REPO_ROOT"/hooks/*.sh "$REPO_ROOT"/hooks/lib/*.sh "$REPO_ROOT"/tests/hooks/*.sh \
         "$REPO_ROOT"/tests/hooks/fixtures/*.sh; do
  [ -f "$f" ] || continue
  [ "$f" = "$SELF" ] && continue
  hits=$(grep -nE "$BASH4" "$f" | grep -vE '^[0-9]+:[[:space:]]*#')
  if [ -n "$hits" ]; then
    FAIL=$((FAIL+1)); echo "  FAIL [bash-4 construct] $f"; echo "$hits" | sed 's/^/    /'
  else
    PASS=$((PASS+1))
  fi
  if out=$("$BASH_BIN" -n "$f" 2>&1); then
    PASS=$((PASS+1))
  else
    FAIL=$((FAIL+1)); echo "  FAIL [syntax under $BASH_BIN] $f"; echo "$out" | sed 's/^/    /'
  fi
done

if [ -f "$REPO_ROOT/hooks/lib/ingest-v3.sh" ]; then PASS=$((PASS+1))
else FAIL=$((FAIL+1)); echo "  FAIL [missing] hooks/lib/ingest-v3.sh"; fi

echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
