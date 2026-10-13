#!/usr/bin/env bash
# Back up everything that cannot be recreated from git.
#
# One Postgres database holds n8n's workflows, its credentials, AND the
# ose_applications Data table with applicant state — so this single dump is the
# whole backup. Workflow JSON in automation/n8n/ is a copy for review, not a
# restore path: it carries no credentials and no applicant rows.
#
# The dump stays on this box for KEEP_DAYS. When ~/.config/ose-backup/backup.env
# exists, an age-encrypted copy also goes to the off-box bucket it names.
#
# Run from anywhere. Installed as a daily timer by the runbook in README.md.
set -euo pipefail

INFRA_DIR="${INFRA_DIR:-$HOME/community/automation/infra}"
DEST="${DEST:-$HOME/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
CONFIG="${BACKUP_CONFIG:-$HOME/.config/ose-backup/backup.env}"

mkdir -p "$DEST"
# The log file keeps the history, and stdout still reaches the journal, so
# `journalctl -u ose-backup.service` and the failure alert both see the run.
exec > >(tee -a "$DEST/backup.log") 2>&1

STAMP=$(date -u +%Y-%m-%dT%H%M%SZ)
OUT="$DEST/n8n-$STAMP.sql.gz"

cd "$INFRA_DIR"
docker compose exec -T postgres pg_dump -U n8n --clean --if-exists n8n | gzip > "$OUT"

# A dump that restores nothing is worse than no dump: fail loudly instead of
# leaving a plausible-looking empty file behind.
SIZE=$(stat -c %s "$OUT" 2>/dev/null || stat -f %z "$OUT")
if [ "$SIZE" -lt 10240 ]; then
  echo "ERROR: dump is only ${SIZE} bytes — refusing to treat this as a backup" >&2
  mv "$OUT" "$OUT.SUSPECT"
  exit 1
fi
if ! gzip -t "$OUT"; then
  echo "ERROR: dump failed gzip integrity check" >&2
  exit 1
fi
# NB: not `grep -q` — it exits on first match, gunzip then dies of SIGPIPE, and
# `set -o pipefail` reports the pipeline as failed precisely BECAUSE the string
# was found. `grep -c` reads to EOF, so there is no SIGPIPE to misread.
TABLES=$(gunzip -c "$OUT" | grep -c '^CREATE TABLE' || true)
if [ "$TABLES" -lt 1 ]; then
  echo "ERROR: dump contains no CREATE TABLE — schema missing" >&2
  exit 1
fi

find "$DEST" -name 'n8n-*.sql.gz' -mtime +"$KEEP_DAYS" -delete

echo "$(date -u +%FT%TZ) ok $OUT ($((SIZE/1024)) KiB, ${TABLES} tables, keeping ${KEEP_DAYS}d)"

# ---------------------------------------------------------------------------
# Off-box copy. The local dump above is already written and kept whatever
# happens here, so a failed upload costs the off-box copy and nothing else.
# ---------------------------------------------------------------------------
if [ ! -f "$CONFIG" ]; then
  echo "WARNING: no $CONFIG, so this dump exists only on this box" >&2
  exit 0
fi

mode=$(stat -c %a "$CONFIG" 2>/dev/null || stat -f %Lp "$CONFIG")
case "$mode" in
  600|400) ;;
  *) echo "ERROR: $CONFIG is mode $mode, so others can read it: chmod 600 $CONFIG" >&2; exit 1 ;;
esac

# Read, never sourced: the file holds values, not shell.
setting() {
  sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$CONFIG" | tail -n 1 | tr -d "\"'" | tr -d '[:space:]'
}
missing=""
for name in BACKUP_S3_BUCKET BACKUP_S3_ACCESS_KEY BACKUP_S3_SECRET_KEY BACKUP_AGE_RECIPIENT; do
  [ -n "$(setting "$name")" ] || missing="$missing $name"
done
BUCKET=$(setting BACKUP_S3_BUCKET)
ACCESS_KEY=$(setting BACKUP_S3_ACCESS_KEY)
SECRET_KEY=$(setting BACKUP_S3_SECRET_KEY)
RECIPIENT=$(setting BACKUP_AGE_RECIPIENT)
REGION=$(setting BACKUP_S3_REGION)
REGION="${REGION:-pl-waw}"
ENDPOINT="${BACKUP_S3_ENDPOINT:-https://$BUCKET.s3.$REGION.scw.cloud}"
if [ -n "$missing" ]; then
  echo "ERROR: $CONFIG is missing:$missing" >&2
  exit 1
fi
command -v age >/dev/null || { echo "ERROR: age is missing: sudo apt-get install -y age" >&2; exit 1; }

# The dump carries applicant addresses and form answers, so it leaves this box
# encrypted. The private key exists only in the password vault.
SEALED="$OUT.age"
trap 'rm -f "$SEALED"' EXIT
age -r "$RECIPIENT" -o "$SEALED" "$OUT"
OBJECT=$(basename "$SEALED")

# The key pair reaches curl on stdin, never as an argument.
if ! printf 'user = "%s:%s"\n' "$ACCESS_KEY" "$SECRET_KEY" \
  | curl -sS --fail --max-time 300 -K - \
      --aws-sigv4 "aws:amz:$REGION:s3" \
      -T "$SEALED" "$ENDPOINT/$OBJECT" -o /dev/null; then
  echo "ERROR: upload of $OBJECT to $BUCKET failed. The local dump $OUT is kept" >&2
  exit 1
fi

echo "$(date -u +%FT%TZ) ok uploaded $OBJECT to $BUCKET ($REGION)"
