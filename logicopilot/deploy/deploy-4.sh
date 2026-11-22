#!/usr/bin/env bash
#
# Deploy the 4-server setup (step 2.6 of the 4-server plan). Run from a Mac/PC that has the
# ssh aliases cargora-app, cargora-ai and cargora-erp (cargora-db runs no app code).
#
#   logicopilot/deploy/deploy-4.sh            check all three, then deploy app -> ai -> erp
#   logicopilot/deploy/deploy-4.sh --check    only show what WOULD change on each; touch nothing
#
# Each server runs the repo's own deploy.sh in its checkout (same fetch / server-edit check /
# fast-forward / pip / restart / health check as SIT and production). What differs per server
# lives in that server's gitignored deploy.env:
#   cargora-app  (nothing - runs migrations and builds the frontend, like a single server)
#   cargora-ai   RUN_MIGRATIONS=0  BUILD_FRONTEND=0  BACKEND_HOST=10.50.0.3
#   cargora-erp  RUN_MIGRATIONS=0  BUILD_FRONTEND=0  BACKEND_HOST=10.50.0.4
# and PROCESS_ROLE (web / worker / erp) lives in each server's backend .env.
#
# Order: every server is CHECKED first and nothing is deployed if any one has server-side edits
# or is on an unexpected branch. Then app first (it runs the migrations, so the schema is ready
# before the worker and ERP code that needs it restarts), then ai, then erp. Stops at the first
# failure - the servers after it keep running their previous version.
#
# No `#` inside commands you paste from here: zsh treats it literally.
set -euo pipefail

SERVERS=(cargora-app cargora-ai cargora-erp)
REMOTE_DIR="${REMOTE_DIR:-/root/logicopilot}"
EXPECT_BRANCH="${EXPECT_BRANCH:-dev}"     # dev until the 4-server code is merged into main
MODE="${1:-}"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
remote() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$1" "cd $REMOTE_DIR && $2"; }

case "$MODE" in
  ""|--check) ;;
  *) echo "usage: $0 [--check]"; exit 2 ;;
esac

say "preflight: branch and server-side edits on every server"
for s in "${SERVERS[@]}"; do
  branch=$(remote "$s" "git rev-parse --abbrev-ref HEAD") || { echo "$s: cannot reach it or no checkout at $REMOTE_DIR"; exit 1; }
  if [ "$branch" != "$EXPECT_BRANCH" ]; then
    echo "$s is on '$branch', expected '$EXPECT_BRANCH' - stopping, nothing deployed."
    exit 1
  fi
  echo "-- $s ($branch)"
  # deploy.sh --check exits non-zero when tracked files were edited on the server.
  if ! remote "$s" "./deploy.sh --check"; then
    echo "$s has server-side edits (above) - stopping, nothing deployed."
    exit 1
  fi
done

if [ "$MODE" = "--check" ]; then
  say "check only - nothing was deployed"
  exit 0
fi

for s in "${SERVERS[@]}"; do
  say "deploying $s"
  if ! remote "$s" "./deploy.sh"; then
    echo
    echo "*** $s FAILED - stopped here. Servers after it were not touched."
    exit 1
  fi
done

say "all three deployed: $(remote cargora-app 'git log --oneline -1')"
