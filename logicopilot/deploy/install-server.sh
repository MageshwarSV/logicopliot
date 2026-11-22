#!/usr/bin/env bash
#
# One-time install of the app on a 4-server box (step 2.7). Run ON the server, as root:
#
#   bash install-server.sh app      cargora-app  - website, API, holds uploads/ (NFS server)
#   bash install-server.sh ai       cargora-ai   - mail, OCR, extraction (mounts uploads/)
#   bash install-server.sh erp      cargora-erp  - ERP entry, Chrome (mounts uploads/)
#
# Prepares everything but STARTS NOTHING: no pm2 app is created, no migration is run, no mail
# is read. Starting comes later (start-server.sh), after the data is loaded and .env is filled.
# Safe to run again - every step checks before it changes anything.
#
# Never prints a secret. A missing .env is created from a template with CHANGE_ME
# placeholders; an existing .env is never overwritten.
#
# Base software (WireGuard, firewall, Python 3.12, Node 20, pm2, Playwright system libs) was
# installed before this; this script adds only what the app itself needs.
set -euo pipefail

ROLE="${1:-}"
case "$ROLE" in
  app) PROCESS_ROLE=web;    WG_IP=10.50.0.2 ;;
  ai)  PROCESS_ROLE=worker; WG_IP=10.50.0.3 ;;
  erp) PROCESS_ROLE=erp;    WG_IP=10.50.0.4 ;;
  *) echo "usage: bash $0 app|ai|erp"; exit 2 ;;
esac

REPO_URL="${REPO_URL:-https://github.com/MageshwarSV/logicopliot.git}"
BRANCH="${BRANCH:-dev}"
DIR=/root/logicopilot
BACKEND="$DIR/logicopilot/backend"
UPLOADS=/srv/cargora/uploads
APP_WG=10.50.0.2
ERP_SESSIONS=/var/lib/cargora/erp_sessions

say() { printf '\n\033[1m[%s] %s\033[0m\n' "$ROLE" "$*"; }

# ---------------------------------------------------------------- guard: right server?
host="$(hostname)"
if [ "$host" != "server.cargora-$ROLE" ]; then
  echo "This is '$host', not server.cargora-$ROLE - refusing (wrong server or wrong role)."
  exit 1
fi
ip -4 addr show wg0 2>/dev/null | grep -q "inet $WG_IP/" \
  || { echo "wg0 does not have $WG_IP - is WireGuard up on this server?"; exit 1; }
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }

# ---------------------------------------------------------------- 1. packages
say "1/7 packages"
pkgs=(git python3-venv)
[ "$ROLE" = app ] && pkgs+=(nfs-kernel-server)
[ "$ROLE" != app ] && pkgs+=(nfs-common)
# Word/Excel -> PDF (app/core/office_convert.py): Smart Upload on app, mail attachments on ai.
[ "$ROLE" != erp ] && pkgs+=(libreoffice-core libreoffice-writer libreoffice-calc)
missing=()
for p in "${pkgs[@]}"; do dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p"); done
if [ "${#missing[@]}" -gt 0 ]; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "${missing[@]}"
fi
echo "     ok: ${pkgs[*]}"
[ "$ROLE" != erp ] && { command -v soffice >/dev/null && echo "     soffice: $(command -v soffice)"; }

# ---------------------------------------------------------------- 2. uploads/
say "2/7 uploads ($UPLOADS)"
mkdir -p "$UPLOADS"
if [ "$ROLE" = app ]; then
  chmod 700 "$UPLOADS"
  # Shared ONLY with ai and erp, over WireGuard. no_root_squash: the app runs as root on all
  # three, so files written by ai must stay root-owned for app to read and delete them.
  line="$UPLOADS 10.50.0.3(rw,sync,no_subtree_check,no_root_squash) 10.50.0.4(rw,sync,no_subtree_check,no_root_squash)"
  touch /etc/exports
  grep -v "^$UPLOADS " /etc/exports > /etc/exports.new || true
  echo "$line" >> /etc/exports.new
  mv /etc/exports.new /etc/exports
  systemctl enable --now nfs-server >/dev/null 2>&1
  exportfs -ra
  echo "     exported to ai (10.50.0.3) and erp (10.50.0.4):"
  exportfs -v | grep "$UPLOADS" | sed 's/^/       /'
else
  # hard: if the app server reboots, file reads WAIT for it instead of failing mid-job.
  # _netdev: mount only once the network (and WireGuard) is up at boot.
  fst="$APP_WG:$UPLOADS $UPLOADS nfs4 rw,hard,noatime,_netdev,x-systemd.after=wg-quick@wg0.service 0 0"
  grep -q " $UPLOADS nfs4 " /etc/fstab || echo "$fst" >> /etc/fstab
  systemctl daemon-reload
  mountpoint -q "$UPLOADS" || mount "$UPLOADS"
  t="$UPLOADS/.mount-test-$ROLE"
  date > "$t" && rm -f "$t" && echo "     mounted from app and writable"
fi

# ---------------------------------------------------------------- 3. erp-only local folder
if [ "$ROLE" = erp ]; then
  say "3/7 local ERP sessions folder ($ERP_SESSIONS)"
  mkdir -p "$ERP_SESSIONS" && chmod 700 "$ERP_SESSIONS"
  echo "     ok (cookies + lock files stay on this server, not on the share)"
else
  say "3/7 (erp-only step - skipped)"
fi

# ---------------------------------------------------------------- 4. code
say "4/7 code ($DIR, branch $BRANCH)"
if [ ! -d "$DIR/.git" ]; then
  git clone -q -b "$BRANCH" "$REPO_URL" "$DIR"
fi
cur="$(git -C "$DIR" rev-parse --abbrev-ref HEAD)"
[ "$cur" = "$BRANCH" ] || { echo "     checkout is on '$cur', expected '$BRANCH' - stopping"; exit 1; }
echo "     at $(git -C "$DIR" log --oneline -1)"

# ---------------------------------------------------------------- 5. python + browser
say "5/7 python venv"
[ -x "$BACKEND/venv/bin/python" ] || python3 -m venv "$BACKEND/venv"
"$BACKEND/venv/bin/pip" install -q --upgrade pip
"$BACKEND/venv/bin/pip" install -q -r "$BACKEND/requirements.txt"
echo "     ok"
if [ "$ROLE" = erp ]; then
  # The browser build must match the pinned playwright version in requirements.txt.
  "$BACKEND/venv/bin/playwright" install chromium >/dev/null
  echo "     chromium for playwright $("$BACKEND/venv/bin/pip" show playwright | awk '/^Version/{print $2}') ok"
fi

# ---------------------------------------------------------------- 6. per-server deploy.env
say "6/7 deploy.env"
if [ "$ROLE" = app ]; then
  rm -f "$DIR/deploy.env"
  echo "     none - this server runs the migrations and builds the frontend"
else
  printf 'RUN_MIGRATIONS=0\nBUILD_FRONTEND=0\n' > "$DIR/deploy.env"
  echo "     RUN_MIGRATIONS=0 BUILD_FRONTEND=0"
fi

# ---------------------------------------------------------------- 7. backend .env
say "7/7 backend .env"
ENVF="$BACKEND/.env"
if [ ! -f "$ENVF" ]; then
  umask 077
  {
    echo "# $ROLE server - created by install-server.sh. Fill every CHANGE_ME before starting."
    echo "PROCESS_ROLE=$PROCESS_ROLE"
    echo "UPLOADS_DIR=$UPLOADS"
    echo "DATABASE_URL=postgresql://cargora:CHANGE_ME@10.50.0.1:5432/cargora"
    echo "REDIS_URL=redis://:CHANGE_ME@10.50.0.1:6379/0"
    echo "EMAIL_POLL_MINUTES=0"
    echo "BROWSER_HEADLESS=true"
    [ "$ROLE" = erp ] && echo "ERP_SESSIONS_DIR=$ERP_SESSIONS"
    echo "# Copied from the source environment (SIT for the test, production at the switch):"
    echo "# JWT_SECRET_KEY OPENAI_API_KEY OPENAI_MODEL GOOGLE_APPLICATION_CREDENTIALS DOCAI_PROJECT_ID"
    echo "# DOCAI_LOCATION DOCAI_PROCESSOR_ID CORS_ORIGINS COOKIE_SECURE RAZORPAY_* PARALLEL_*_WORKERS"
  } > "$ENVF"
  echo "     created from template (chmod 600) - fill it before starting"
else
  echo "     exists - not touched"
fi
# Report the lines that decide this server's behaviour; never values of secrets.
for k in PROCESS_ROLE UPLOADS_DIR EMAIL_POLL_MINUTES ERP_SESSIONS_DIR; do
  v="$(grep -E "^$k=" "$ENVF" | tail -1 | cut -d= -f2- || true)"
  [ -n "$v" ] && echo "     $k=$v"
done
n="$(grep -c CHANGE_ME "$ENVF" || true)"
[ "$n" -gt 0 ] && echo "     $n line(s) still say CHANGE_ME"
if ! grep -qE "^PROCESS_ROLE=$PROCESS_ROLE$" "$ENVF"; then
  echo "     !! PROCESS_ROLE in .env is not '$PROCESS_ROLE' - fix before starting"
fi

say "installed - nothing started. Next: load data, fill .env, then start-server.sh $ROLE"
