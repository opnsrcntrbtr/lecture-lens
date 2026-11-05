#!/bin/zsh
# After class: judge the drafted questions and follow-ups, then rewrite qa.md without the pruned ones.
#   ./run_live_scores.sh <lectures/live/YYYYMMDD>
set -e; setopt pipefail
HERE="${0:A:h}"; STUDY="${HERE:h:h}"
LIVE="${1:A}"; DAY="${LIVE:t}"
cd "$HERE"
set -a; source "$STUDY/.env"; set +a; source "$STUDY/.venv-eval/bin/activate"
export USE_TF=0 DEEPEVAL_TELEMETRY_OPT_OUT=1 DEEPEVAL_LOCAL_STORE=json PYTHONPATH="$HERE:$STUDY"
if pgrep -x screenpipe >/dev/null; then echo "capture is running; not swapping models"; exit 3; fi
[[ -n "$EVAL_FORCE" ]] || python omlx_swap.py idle 60 || exit 3
restore() { echo "== restoring generator"; python omlx_swap.py generator || true; }
trap restore EXIT
python omlx_swap.py judge
python score_live.py "$LIVE" --runs 3
LECT=$(ls -d "$STUDY"/lectures/${DAY}-* 2>/dev/null | head -1)
[[ -n "$LECT" ]] && "$STUDY/lecture" qa "${LECT:t}" || echo "no built lecture for $DAY yet; qa.md is rewritten at the next build"
