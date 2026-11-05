#!/usr/bin/env bash
#
# Deploy LogicoPilot from git.
#
# Replaces the old routine of building a tar locally, uploading it, unpacking it
# over the live directory and restarting by hand. That routine is why two real
# fixes - the OpenAI rate-limit retry in llm.py and extraction.py - existed only
# on one server and nowhere else: an unpack writes files without recording
# anything, so there was no way to tell edited-here from not-yet-deployed.
#
# IN THE REPO on purpose. It used to live loose on the UAT box, next to a pile of
# deployNNN.sh/deployNNN.tar.gz leftovers from before git - and during the move to
# the new box it was dropped along with them, as more of the same debris. It was
# not: it is the only thing that checks for server-side edits before overwriting
# them. Three deploys were then done by hand, each one a chance to miss a step.
# Here it cannot be lost in the next migration, every box runs the same sequence,
# and a change to it gets read like any other change.
#
#   ./deploy.sh            deploy origin/main
#   ./deploy.sh --check    show what WOULD change, touch nothing
#   ./deploy.sh --force    discard server-side edits and deploy anyway
#
set -euo pipefail

# Wherever this checkout happens to be, rather than a path baked in for one box.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$ROOT/logicopilot"
BRANCH="${DEPLOY_BRANCH:-main}"
# Same on UAT and SIT today. Overridable so a box that differs needs no edit here:
#   BACKEND_PORT=31299 ./deploy.sh
BACKEND_PORT="${BACKEND_PORT:-30299}"
FRONTEND_PORT="${FRONTEND_PORT:-30300}"
CHECK=${1:-}

cd "$ROOT"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

say "1/6  fetching origin/$BRANCH"
git fetch --quiet origin "$BRANCH"
echo "     remote is at: $(git log --oneline -1 "origin/$BRANCH")"
echo "     server is at: $(git log --oneline -1 HEAD 2>/dev/null || echo 'no HEAD yet')"

# Anything edited directly on the server and not committed. The whole point of
# moving to git is that this stops happening, but if it has happened the deploy
# must not silently overwrite it - that is exactly how the retry fix nearly went.
#
# Counted by CHANGED LINES, not by changed files. Every file here arrived by tar
# from a Windows machine and carries CRLF while the repo stores LF, so 175 files
# differ byte-for-byte with identical content - and --name-only lists them even
# when the diff itself comes back empty. numstat gives 0 added / 0 removed for
# those, which is the only reading that tells a real edit from a line ending.
DIRTY=$(git diff --ignore-cr-at-eol --ignore-all-space --numstat | awk '$1!=0 || $2!=0' | wc -l)
if [ "$DIRTY" -gt 0 ]; then
  say "!!  $DIRTY tracked file(s) were changed on the server and never committed"
  git diff --ignore-cr-at-eol --ignore-all-space --numstat | awk '$1!=0 || $2!=0' | head -20
  cat <<'WARN'

    These will be OVERWRITTEN by the deploy and lost.

    If they are real fixes, save them first:
        git diff > /root/server-changes-$(date +%F).patch

    Then send that patch to whoever maintains the repo, or commit and push it.
    Re-run with --force once you are sure they can go.
WARN
  [ "$CHECK" = "--force" ] || exit 1
fi

if [ "$CHECK" = "--check" ]; then
  say "what WOULD change (nothing has been touched)"
  git diff --stat "HEAD..origin/$BRANCH" || true
  echo
  echo "     run without --check to apply"
  exit 0
fi

if [ "$CHECK" = "--force" ]; then
  say "2a/6  clearing the way (--force)"
  # --force used to skip only the WARNING, leaving the files in place - so the
  # merge below still refused, and the deploy stopped with a wall of git errors
  # after the operator had already confirmed they were happy to lose them.
  # Saying "these will be overwritten" and then not overwriting them is the
  # worst of both: it reads as destructive and achieves nothing.
  #
  # Tracked files go back to what the last pull left. Untracked files that the
  # incoming commit also contains are removed, because git refuses to write over
  # a file it does not know about - these are almost always a previous tar
  # deploy's leftovers, which is exactly what this is replacing.
  BACKUP="$ROOT/../logicopilot-preforce-$(date +%Y%m%d-%H%M%S).patch"
  git diff > "$BACKUP" 2>/dev/null || true
  echo "     what was there is saved at: $BACKUP"
  git checkout -- . 2>/dev/null || true
  git fetch --quiet origin "$BRANCH"
  git diff --name-only --diff-filter=A "HEAD..origin/$BRANCH" | while read -r f; do
    if [ -f "$f" ] && ! git ls-files --error-unmatch "$f" >/dev/null 2>&1; then
      rm -f "$f"
      echo "     removed untracked leftover: $f"
    fi
  done
fi

say "2/6  updating the working tree"
# --ff-only refuses anything that is not a clean fast-forward, so a diverged
# server never gets silently rewritten.
git merge --ff-only "origin/$BRANCH"
echo "     now at: $(git log --oneline -1)"

say "3/6  backend dependencies"
cd "$APP/backend"
if [ -f requirements.txt ]; then
  ./venv/bin/pip install -q -r requirements.txt 2>&1 | tail -3 || true
  echo "     done"
fi

say "4/6  database migrations"
# Before the restart, never after: the new code must not start against an old schema.
PYTHONPATH=. ./venv/bin/python -m alembic upgrade head 2>&1 | tail -4

say "5/6  building the frontend"
cd "$APP/frontend"
npm ci --silent 2>&1 | tail -3 || npm install --silent 2>&1 | tail -3
npm run build 2>&1 | tail -5

say "6/6  restarting"
# Only these two. These boxes also run other apps under the same pm2 - meeting,
# oos, productflow, ql, vessel-orch, wb-leads, workbooster, unified-orchestrator -
# and none of them are ours to bounce.
pm2 restart logicopilot-backend logicopilot-frontend --update-env >/dev/null
sleep 4
pm2 list --no-color | grep -E "logicopilot|name" | sed 's/^/     /'

say "checking it answers"
for i in $(seq 1 15); do
  if curl -fsS -m 3 "http://127.0.0.1:$BACKEND_PORT/docs" >/dev/null 2>&1; then
    echo "     backend OK on :$BACKEND_PORT"; break
  fi
  [ "$i" = 15 ] && { echo "     *** backend NOT answering - check: pm2 logs logicopilot-backend"; exit 1; }
  sleep 2
done
curl -fsS -m 3 "http://127.0.0.1:$FRONTEND_PORT" >/dev/null 2>&1 \
  && echo "     frontend OK on :$FRONTEND_PORT" \
  || echo "     *** frontend not answering - check: pm2 logs logicopilot-frontend"

say "deployed: $(git log --oneline -1)"
