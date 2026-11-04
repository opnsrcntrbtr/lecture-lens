#!/usr/bin/env bash
# Run the research-backed improvement loop: the same prompt, fresh context each
# time, until the agent reports the plan is empty or the iteration cap is hit.
#
#   ralph/loop.sh                    # on the Mac, up to 10 iterations
#   RALPH_MAX=3 ralph/loop.sh
#   RALPH_AGENT='my-agent --stdin' ralph/loop.sh
#
# RALPH_AGENT is any command that reads a prompt on stdin and works in this
# repository. The default is Claude Code in print mode. The loop sends only the
# prompt and the tracked code; it never sends lectures/, notes/ or private/.
set -euo pipefail
cd "$(dirname "$0")/.."

MAX="${RALPH_MAX:-10}"
HOST="${RALPH_HOST:-mac}"
AGENT="${RALPH_AGENT:-claude -p --permission-mode acceptEdits}"
LOG_DIR="ralph/.logs"; mkdir -p "$LOG_DIR"

if [[ "$(git config core.hooksPath || true)" != ".githooks" ]]; then
  echo "guard hooks are off: run tools/setup_hooks.sh first" >&2; exit 1
fi

for ((i = 1; i <= MAX; i++)); do
  log="$LOG_DIR/$(date +%Y%m%d-%H%M%S)-$i.log"
  echo "== iteration $i/$MAX ($HOST) -> $log"
  { printf 'RALPH_HOST=%s\n\n' "$HOST"; cat ralph/PROMPT.md; } | $AGENT | tee "$log"
  if tail -n 3 "$log" | grep -q "RALPH: plan empty"; then
    echo "== plan empty after $i iteration(s)"; exit 0
  fi
  python3 tools/content_guard.py --all >/dev/null || { echo "== guard finding: stopping" >&2; exit 1; }
done
echo "== stopped at the iteration cap ($MAX); see ralph/fix_plan.md"
