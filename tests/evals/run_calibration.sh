#!/bin/zsh
# Re-judge the calibration sheets with the versioned rubric (rubric.py), then restore the generator.
#   ./run_calibration.sh            v2 on labels.csv, v1+v2 on holdout.csv, then v1 again on labels.csv
set -e; setopt pipefail
HERE="${0:A:h}"; STUDY="${HERE:h:h}"; cd "$HERE"
set -a; source "$STUDY/.env"; set +a; source "$STUDY/.venv-eval/bin/activate"
export USE_TF=0 DEEPEVAL_TELEMETRY_OPT_OUT=1 DEEPEVAL_LOCAL_STORE=json PYTHONPATH="$HERE:$STUDY"
if pgrep -x screenpipe >/dev/null; then echo "capture is running; not swapping models"; exit 3; fi
[[ -n "$EVAL_FORCE" ]] || python omlx_swap.py idle 60 || exit 3
restore() { echo "== restoring generator"; python omlx_swap.py generator || true; }
trap restore EXIT
python omlx_swap.py judge
CAL="$HERE/calibration"
echo "== v2 on labels";   python calibration.py rejudge --rubric v2 --sheet "$CAL/labels.csv"
echo "== v1 on holdout";  python calibration.py rejudge --rubric v1 --sheet "$CAL/holdout.csv"
echo "== v2 on holdout";  python calibration.py rejudge --rubric v2 --sheet "$CAL/holdout.csv"
echo "== v1 re-run on labels (same code path as v2)"; python calibration.py rejudge --rubric v1 --sheet "$CAL/labels.csv"
python calibration.py report "$CAL/labels.csv" > "$CAL/labels_report.json"
echo "== done"
