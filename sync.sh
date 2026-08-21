#!/usr/bin/env bash
# Push local work, resolving the recurring conflict on generated data.
#
# docs/data/*.json is rebuilt by the daily bot AND by every local run, so a
# local commit almost always collides with a bot commit. The files are build
# output: take the local side, finish the rebase, regenerate so the committed
# JSON matches the current engine, then push.
#
# Verifying by "does HEAD match origin" alone is NOT enough — during a stopped
# rebase HEAD sits on the upstream commit and that check reports a false
# success. Check the rebase state too.
set -euo pipefail
cd "$(dirname "$0")"

git fetch -q origin main
if ! git rebase origin/main 2>/dev/null; then
  echo "resolving generated-data conflict ..."
  git checkout --theirs docs/data/latest.json docs/data/history.json 2>/dev/null || true
  git add docs/data/*.json 2>/dev/null || true
  GIT_EDITOR=true git rebase --continue
fi

if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; then
  echo "ERROR: still mid-rebase, resolve by hand" >&2; exit 1
fi

python3 -m engine.run --no-refresh --days 400 >/dev/null
if ! git diff --quiet -- docs/data; then
  git add docs/data
  git commit -q -m "data: regenerate after rebase [skip ci]"
fi

git push origin main
remote=$(git ls-remote origin refs/heads/main | cut -f1)
local=$(git rev-parse HEAD)
[ "$remote" = "$local" ] || { echo "ERROR: remote=$remote local=$local" >&2; exit 1; }
echo "pushed and verified: $local"
