#!/bin/zsh
# Holdout check: old (v1) and candidate (v2.1) judge rules on the holdout rows, majority of 3 runs each.
# Resumable: rows already scored are skipped, so after each class only the 15 new rows are judged.
set -e; setopt pipefail
HERE="${0:A:h}"; STUDY="${HERE:h:h}"; cd "$HERE"
set -a; source "$STUDY/.env"; set +a; source "$STUDY/.venv-eval/bin/activate"
export USE_TF=0 DEEPEVAL_TELEMETRY_OPT_OUT=1 DEEPEVAL_LOCAL_STORE=json PYTHONPATH="$HERE:$STUDY"
if pgrep -x screenpipe >/dev/null; then echo "capture is running; not swapping models"; exit 3; fi
[[ -n "$EVAL_FORCE" ]] || python omlx_swap.py idle 60 || exit 3
restore() { echo "== restoring generator"; python omlx_swap.py generator || true; }
trap restore EXIT
python omlx_swap.py judge
SHEET="$HERE/calibration/holdout.csv"
echo "== v2.1 x3 on holdout"; python calibration.py rejudge --rubric v2.1 --runs 3 --sheet "$SHEET"
echo "== v1 x3 on holdout";   python calibration.py rejudge --rubric v1 --runs 3 --sheet "$SHEET"
echo "== done"
