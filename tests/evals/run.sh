#!/bin/zsh
# Two-phase DeepEval run: generator writes outputs, judge scores them, generator restored.
#   ./run.sh                 full run (goldens are generated once if missing)
#   ./run.sh --regen-goldens regenerate the dataset first
#   ./run.sh --judge-only    re-score the cached .outputs.json (no generation)
#   ./run.sh --smoke         6 fixed cases, about 5-10 minutes
set -e
setopt pipefail   # `python x | tee` must fail when python fails
HERE="${0:A:h}"; STUDY="${HERE:h:h}"
cd "$HERE"
set -a; source "$STUDY/.env"; set +a
source "$STUDY/.venv-eval/bin/activate"
export USE_TF=0 DEEPEVAL_TELEMETRY_OPT_OUT=1 DEEPEVAL_LOCAL_STORE=json PYTHONPATH="$HERE:$STUDY"
# DeepEval's default 180 s budget per metric is sized for cloud judges; the local
# 27B judges at ~10 tok/s, and Faithfulness on a page of notes is dozens of calls.
# Timed-out metrics are recorded as failures with no score (baseline 2026-09-30).
export DEEPEVAL_PER_TASK_TIMEOUT_SECONDS_OVERRIDE=${DEEPEVAL_PER_TASK_TIMEOUT_SECONDS_OVERRIDE:-1200}
SUITE=deepeval
if [[ "$1" == "--smoke" ]]; then   # 6 fixed cases (2 ask, 2 notes, 2 cards): a quick gate, comparable run to run
  SUITE=smoke; export EVAL_DATASET=smoke_dataset.json EVAL_CONTEXTS=smoke_contexts.json EVAL_SESSIONS=0 EVAL_SUITE=smoke
fi
# Never swap models under a live class: the lecture build needs the generator.
if [[ "$(cat "$HOME/.screenpipe/sp-mode.current" 2>/dev/null)" == live ]] && pgrep -f "screenpipe record" >/dev/null && [[ -z "$EVAL_FORCE" ]]; then
  echo "capture is running in live mode; not starting an eval (set EVAL_FORCE=1 to override)"; exit 3
fi
[[ -n "$EVAL_FORCE" ]] || python omlx_swap.py idle 60 || exit 3   # someone else is using the model server
TS=$(date +%Y%m%d-%H%M%S); RES="$STUDY/eval/results/$TS-$SUITE"; mkdir -p "$RES"
export DEEPEVAL_RESULTS_FOLDER="$RES"   # DeepEval writes test_run_<ts>.json here (local only)

# Always leave the daily model loaded, even if something fails.
restore() { echo "== restoring generator"; python omlx_swap.py generator || true; }
trap restore EXIT

if [[ "$1" == "--regen-goldens" || ( "$SUITE" != smoke && ! -f .dataset.json ) ]]; then
  echo "== goldens: judge generates them from contexts.json"
  python omlx_swap.py judge
  python generate_goldens.py | tee "$RES/goldens.json"
fi

if [[ "$1" == "--judge-only" && -f .outputs.json ]]; then
  echo "== phase G skipped: re-scoring cached .outputs.json"
else
  echo "== phase G: generator produces answers, notes, cards"
  python omlx_swap.py generator
  python generate_outputs.py | tee "$RES/generate.json"
fi

echo "== phase J: judge scores them"
python omlx_swap.py judge
set +e
deepeval test run test_study.py --identifier "$SUITE-$TS" 2>&1 | tee "$RES/deepeval.log"
STATUS=${pipestatus[1]}
set -e

python summarize.py "$RES"
exit $STATUS
