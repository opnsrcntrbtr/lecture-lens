#!/bin/zsh
# Judge a class's live questions and follow-ups.  ./run_live_eval.sh <lectures/live/YYYYMMDD> [n]
set -e; setopt pipefail
HERE="${0:A:h}"; STUDY="${HERE:h:h}"; cd "$HERE"
set -a; source "$STUDY/.env"; set +a; source "$STUDY/.venv-eval/bin/activate"
export USE_TF=0 DEEPEVAL_TELEMETRY_OPT_OUT=1 DEEPEVAL_LOCAL_STORE=json PYTHONPATH="$HERE:$STUDY"
export DEEPEVAL_PER_TASK_TIMEOUT_SECONDS_OVERRIDE=${DEEPEVAL_PER_TASK_TIMEOUT_SECONDS_OVERRIDE:-1200}
if [[ "$(cat "$HOME/.screenpipe/sp-mode.current" 2>/dev/null)" == live ]] && pgrep -f "screenpipe record" >/dev/null; then
  echo "capture is running in live mode; not starting an eval"; exit 3; fi
TS=$(date +%Y%m%d-%H%M%S); RES="$STUDY/eval/results/$TS-livequestions"; mkdir -p "$RES"
export DEEPEVAL_RESULTS_FOLDER="$RES"
python live_questions_dataset.py "$1" --n "${2:-24}" | tee "$RES/dataset.json"
restore() { echo "== restoring generator"; python omlx_swap.py generator || true; }
trap restore EXIT
python omlx_swap.py judge
set +e; deepeval test run test_live_questions.py --identifier "livequestions-$TS" 2>&1 | tee "$RES/deepeval.log"; STATUS=${pipestatus[1]}; set -e
python summarize.py "$RES" || true
exit $STATUS
