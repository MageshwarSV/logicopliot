#!/usr/bin/env bash
# Nightly database backup to S3 (prod: cargora-db, sit: the SIT server). Run by root's cron:
#   45 23 * * * /usr/local/bin/cargora-db-backup >> /var/log/cargora-backup.log 2>&1
#
# Settings live in /etc/cargora-backup.env (see cargora-backup.env.example) - never in git.
# Each run: pg_dump (custom format) -> check it can be read back -> upload to
#   s3://$S3_BUCKET/$S3_PREFIX/db/daily/...        (locked $DAILY_DAYS days)
#   s3://$S3_BUCKET/$S3_PREFIX/db/monthly/...      (on the 1st, locked $MONTHLY_DAYS days)
# -> confirm the size in S3 -> keep the last $KEEP_LOCAL dumps on disk -> tell the app (Redis key
# cargora:backup:$ENV_NAME, shown on Production Support > Application Health).
#   --dry-run   dump and check only, no upload
set -Eeuo pipefail

CONF="${CARGORA_BACKUP_CONF:-/etc/cargora-backup.env}"
# shellcheck disable=SC1090
source "$CONF"
: "${ENV_NAME:?}" "${DB_NAME:?}" "${S3_BUCKET:?}" "${S3_PREFIX:?}" "${DAILY_DAYS:?}"
MONTHLY_DAYS="${MONTHLY_DAYS:-0}"
KEEP_LOCAL="${KEEP_LOCAL:-3}"
LOCAL_DIR="${LOCAL_DIR:-/var/backups/cargora}"
AWS_PROFILE_NAME="${AWS_PROFILE_NAME:-cargora-backup}"
DRY=0
[[ "${1:-}" == "--dry-run" ]] && DRY=1

TS="$(date +%Y-%m-%d-%H%M)"
NAME="${ENV_NAME}-${DB_NAME}-${TS}.dump"
FILE="${LOCAL_DIR}/${NAME}"

log() { echo "$(date '+%F %T') [$ENV_NAME] $*"; }

report() {   # report <ok|failed> <detail>
  [[ -z "${REDIS_URL:-}" ]] && return 0
  local json
  json=$(printf '{"status":"%s","at":"%s","detail":"%s"}' "$1" "$(date -Iseconds)" "${2//\"/\'}")
  redis-cli -u "$REDIS_URL" --no-auth-warning SET "cargora:backup:${ENV_NAME}" "$json" EX 1209600 >/dev/null 2>&1 || true
}

fail() {
  log "FAILED: $*"
  report failed "$*"
  rm -f "${FILE}.part"
  exit 1
}
trap 'fail "stopped at line $LINENO"' ERR

mkdir -p "$LOCAL_DIR"
chmod 700 "$LOCAL_DIR"

log "dumping $DB_NAME"
sudo -u postgres pg_dump -Fc -Z 6 "$DB_NAME" > "${FILE}.part"
mv "${FILE}.part" "$FILE"
pg_restore --list "$FILE" > /dev/null || fail "the dump cannot be read back"
SIZE=$(stat -c %s "$FILE")
(( SIZE > 100000 )) || fail "the dump is only $SIZE bytes"
log "dump ok: $NAME ($SIZE bytes)"

upload() {   # upload <s3 key> <days locked>
  local key="$1" days="$2" until remote
  until=$(date -u -d "+${days} days" +%Y-%m-%dT%H:%M:%SZ)
  aws --profile "$AWS_PROFILE_NAME" s3api put-object \
    --bucket "$S3_BUCKET" --key "$key" --body "$FILE" \
    --checksum-algorithm SHA256 --server-side-encryption AES256 \
    --object-lock-mode GOVERNANCE --object-lock-retain-until-date "$until" > /dev/null
  remote=$(aws --profile "$AWS_PROFILE_NAME" s3api head-object --bucket "$S3_BUCKET" --key "$key" \
    --query ContentLength --output text)
  [[ "$remote" == "$SIZE" ]] || fail "size in S3 ($remote) differs from the dump ($SIZE) for $key"
  log "uploaded s3://$S3_BUCKET/$key (locked until $until)"
}

if (( DRY )); then
  log "dry run - not uploading"
else
  upload "${S3_PREFIX}/db/daily/${NAME}" "$DAILY_DAYS"
  if [[ "$(date +%d)" == "01" ]] && (( MONTHLY_DAYS > 0 )); then
    upload "${S3_PREFIX}/db/monthly/${NAME}" "$MONTHLY_DAYS"
  fi
fi

# keep only the newest KEEP_LOCAL dumps on this server
ls -1t "${LOCAL_DIR}/${ENV_NAME}-${DB_NAME}-"*.dump 2>/dev/null | tail -n +"$((KEEP_LOCAL + 1))" | xargs -r rm -f

(( DRY )) || report ok "$NAME, $SIZE bytes"
log "done"
