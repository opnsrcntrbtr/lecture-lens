#!/bin/sh
# One-time setup for a clone: turn on the content guard hooks and make a plain
# `git push` impossible, so every push goes through tools/publish.sh.
set -e
cd "$(git rev-parse --show-toplevel)"
git config core.hooksPath .githooks
chmod +x .githooks/* tools/*.py tools/*.sh tools/guard/*.py 2>/dev/null || true
if git remote get-url origin >/dev/null 2>&1; then
  git config remote.origin.pushurl "disabled://use-tools-publish.sh"
fi
mkdir -p .guard
[ -f .guard/terms.local.txt ] || printf '# One blocked word or phrase per line. This file is never committed.\n' > .guard/terms.local.txt
echo "hooks on (core.hooksPath=.githooks); plain 'git push' disabled; use tools/publish.sh"
