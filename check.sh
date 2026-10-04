#!/bin/zsh
# Self-check for the study toolkit. Exits non-zero if something a user would
# hit is broken. Run after any change.
DIR="${0:A:h}"; fail=0
ok()   { print -P "%F{green}✓%f $1" }
bad()  { print -P "%F{red}✗%f $1"; fail=1 }
note() { print -P "%F{yellow}~%f $1" }

for f in sp-start sp-stop sp-mode sp-domains sp-autoswitch study lecture; do
  [[ -x "$DIR/$f" ]] && ok "$f executable" || bad "$f missing or not executable"
done
for f in study.py lecture_kit.py; do
  python3 -c "import ast,sys; ast.parse(open('$DIR/$f').read())" 2>/dev/null \
    && ok "$f parses" || bad "$f has a syntax error"
done
for f in sp-start sp-stop sp-mode sp-domains sp-autoswitch; do
  zsh -n "$DIR/$f" 2>/dev/null || bad "$f has a shell syntax error"
done
[[ -f "$DIR/.env" ]] && ok ".env present" || bad ".env missing"
grep -qE "^(OMLX_API_KEY|SCREENPIPE_API_KEY)=.+" "$DIR/.env" 2>/dev/null \
  && bad "a credential is stored in .env — keep keys out of the repo" || ok "no credentials in .env"
[[ -f "$DIR/allowed-domains.txt" ]] && ok "allowlist present ($(grep -cvE '^\s*(#|$)' $DIR/allowed-domains.txt) domains)" \
  || bad "allowed-domains.txt missing"
set -a; source "$DIR/.env" 2>/dev/null; set +a
judge_loaded() { curl -s -m 3 --noproxy '*' 127.0.0.1:8001/api/status 2>/dev/null | grep -q "\"${OMLX_JUDGE_MODEL:-__none__}\"" }
if judge_loaded; then   # model-calling checks would force oMLX to swap models mid-eval
  "$DIR/lecture" doctor --quick >/dev/null 2>&1 && ok "lecture doctor --quick passes (eval running: vision probe skipped)" || note "lecture doctor reports issues"
  note "study check skipped (judge model loaded — eval running)"
else
  "$DIR/lecture" doctor >/dev/null 2>&1 && ok "lecture doctor passes" || note "lecture doctor reports issues (run it for detail)"
  "$DIR/study" check >/dev/null 2>&1 && ok "study check passes" || note "study check reports issues (screenpipe may be stopped)"
fi
( cd "$DIR/eval" && [[ -d cases ]] || python3 gen_dataset.py >/dev/null ) 2>/dev/null
( cd "$DIR/eval" && python3 test_scoring.py >/dev/null 2>&1 ) && ok "eval scorer self-test (19 known answers)" || bad "eval scorer self-test failed — do not trust eval results"
( cd "$DIR/eval" && python3 player_crop.py >/dev/null 2>&1 ) && ok "player-crop self-test" || bad "player-crop self-test failed"
# unit tests: pure functions, no model, no capture (seconds)
PY=python3; for c in "$DIR/.venv/bin/python" "$DIR/.venv-eval/bin/python"; do [[ -x $c ]] && { PY=$c; break; }; done
"$PY" -m pytest "$DIR/tests/unit" -q >/dev/null 2>&1 && ok "unit tests pass" || bad "unit tests fail (run: $PY -m pytest tests/unit -q)"
python3 "$DIR/tools/content_guard.py" --all >/dev/null 2>&1 && ok "content guard clean" || bad "content guard has findings (run: tools/content_guard.py --all)"
[[ "$(git -C "$DIR" config core.hooksPath)" == ".githooks" ]] && ok "guard hooks enabled" || bad "guard hooks off (run: tools/setup_hooks.sh)"

# --json contract the native app depends on (docs/02-system-design.md §3.1)
json_ok() { python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if all(k in d for k in sys.argv[1:]) else 1)" "$@" }
"$DIR/sp-mode" --json 2>/dev/null | json_ok running mode autoswitch && ok "sp-mode --json contract" || bad "sp-mode --json broke the app contract"
"$DIR/lecture" doctor --json --quick 2>/dev/null | json_ok ok checks && ok "lecture doctor --json contract" || bad "lecture doctor --json broke the app contract"
"$DIR/lecture" list --json --since 1d 2>/dev/null | json_ok sessions && ok "lecture list --json contract" || bad "lecture list --json broke the app contract"
# ask calls the model: skip while an eval has swapped the judge in (it would force a swap back)
if judge_loaded; then
  note "study ask --json contract skipped (judge model loaded — eval running)"
else
  "$DIR/study" ask --json -- "contract check" 2>/dev/null | json_ok ok && ok "study ask --json contract" || bad "study ask --json broke the app contract"
fi
# a GUI app's minimal PATH must still find a python3 with Pillow
env -i HOME="$HOME" PATH=/usr/bin:/bin:/opt/homebrew/bin "$DIR/lecture" doctor --json --quick >/dev/null 2>&1 \
  && ok "toolkit runs under an app's minimal PATH" || bad "toolkit fails under an app's minimal PATH (set STUDY_PYTHON)"
for f in tests/evals/run.sh tests/evals/run_judge_bench.sh eval/run_eval.py; do
  [[ -x "$DIR/$f" ]] && ok "$f executable" || bad "$f not executable (the app launches it)"
done
[[ -f "$DIR/app/LectureLens/Package.swift" ]] && ok "native app sources present (app/build_app.sh --test)" || note "native app sources missing"
[[ -d "$DIR/.git" ]] && ok "toolkit is version controlled" || bad "no git repo here"
exit $fail
