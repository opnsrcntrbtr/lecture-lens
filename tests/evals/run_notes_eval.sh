#!/bin/zsh
# Judge lecture notes against the deck and transcript digest.
#   NOTES_EVAL='[{"label":"before","notes":"…/notes.v1.md","folder":"…"}, …]' ./run_notes_eval.sh
set -e; setopt pipefail
HERE="${0:A:h}"; STUDY="${HERE:h:h}"; cd "$HERE"
set -a; source "$STUDY/.env"; set +a; source "$STUDY/.venv-eval/bin/activate"
export USE_TF=0 DEEPEVAL_TELEMETRY_OPT_OUT=1 DEEPEVAL_LOCAL_STORE=json PYTHONPATH="$HERE:$STUDY"
export DEEPEVAL_PER_TASK_TIMEOUT_SECONDS_OVERRIDE=${DEEPEVAL_PER_TASK_TIMEOUT_SECONDS_OVERRIDE:-1800}
[[ -n "$EVAL_FORCE" ]] || python omlx_swap.py idle 60 || exit 3   # someone else is using the model server
TS=$(date +%Y%m%d-%H%M%S); RES="$STUDY/eval/results/$TS-notes"; mkdir -p "$RES"
export DEEPEVAL_RESULTS_FOLDER="$RES"
echo "$NOTES_EVAL" > "$RES/spec.json"
restore() { echo "== restoring generator"; python omlx_swap.py generator || true; }
trap restore EXIT
python omlx_swap.py judge
set +e; deepeval test run test_lecture_notes.py --identifier "notes-$TS" 2>&1 | tee "$RES/deepeval.log"; STATUS=${pipestatus[1]}; set -e
python summarize.py "$RES" || true
exit $STATUS
