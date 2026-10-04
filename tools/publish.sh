#!/bin/sh
# The only supported way to push. Runs the content guard on the full tree and on
# every commit the remote does not have, then pushes. The pre-push hook runs the
# same checks again; this script exists so that `git push --no-verify` on the
# default remote does nothing (its push URL is disabled by tools/setup_hooks.sh).
#
#   tools/publish.sh              push the current branch
#   tools/publish.sh --dry-run    run the checks only
set -e
cd "$(git rev-parse --show-toplevel)"
py="${GUARD_PYTHON:-python3}"
branch="$(git rev-parse --abbrev-ref HEAD)"
url="$(git remote get-url origin)"          # fetch URL; the push URL is deliberately disabled
[ "$(git config core.hooksPath)" = ".githooks" ] || { echo "hooks are off: run tools/setup_hooks.sh"; exit 2; }
[ -z "$(git status --porcelain)" ] || { echo "working tree not clean: commit or stash first"; exit 2; }
"$py" tools/content_guard.py --all
if git rev-parse -q --verify "refs/remotes/origin/$branch" >/dev/null; then
  "$py" tools/content_guard.py --range "origin/$branch..HEAD"
else
  "$py" tools/content_guard.py --range HEAD
fi
[ "$1" = "--dry-run" ] && { echo "dry run: checks passed, nothing pushed"; exit 0; }
git push "$url" "HEAD:refs/heads/$branch"
git fetch -q origin "$branch" || true
