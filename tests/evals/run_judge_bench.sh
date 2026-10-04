#!/bin/zsh
# Judge benchmark: Laya (System-1) vs the oMLX 27B judge. Swaps the judge in,
# runs judge_bench.py, always swaps the generator back.
#   ./run_judge_bench.sh              full (Laya + LLM judge + FaithfulnessMetric modes)
#   ./run_judge_bench.sh --skip-llm   Laya only (no model swap, ~1 min)
set -e
setopt pipefail
HERE="${0:A:h}"; STUDY="${HERE:h:h}"
cd "$HERE"
set -a; source "$STUDY/.env"; set +a
source "$STUDY/.venv-eval/bin/activate"
export USE_TF=0 DEEPEVAL_TELEMETRY_OPT_OUT=1 DEEPEVAL_LOCAL_STORE=json PYTHONPATH="$HERE:$STUDY"

# One eval at a time: they share oMLX's single model slot.
if pgrep -f "generate_goldens.py|generate_outputs.py|deepeval test run|judge_bench.py" >/dev/null; then
  echo "✗ another eval is running — try again when it finishes" >&2; exit 1
fi

TS=$(date +%Y%m%d-%H%M%S); RES="$STUDY/eval/results/$TS-judgebench"; mkdir -p "$RES"

if [[ "$1" != "--skip-llm" ]]; then
  restore() { echo "== restoring generator"; python omlx_swap.py generator || true; }
  trap restore EXIT
  echo "== loading judge"
  python omlx_swap.py judge
fi

python judge_bench.py "$RES" "$@" 2>&1 | tee "$RES/bench.log"
exit ${pipestatus[1]}
