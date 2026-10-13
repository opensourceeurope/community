#!/usr/bin/env bash
# Runs automation/infra/backup.sh with docker, age and curl replaced by stubs,
# and checks the off-box copy: skipped without a config, encrypted before it is
# uploaded, credentials never in an argument list, and a failed upload that
# fails the run while the local dump stays. Nothing here reaches a bucket.
#
#   automation/test/backup-upload.sh
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
BACKUP="${BACKUP:-$(cd "$HERE/../infra" && pwd)/backup.sh}"
WORK=$(mktemp -d)
finished=false
case_name="setup"
trap 'rc=$?; [ $rc -eq 0 ] || [ "$finished" = true ] || echo "backup-upload: the test itself stopped with $rc in [$case_name]"; rm -rf "$WORK"' EXIT

BIN="$WORK/bin"
mkdir -p "$BIN" "$WORK/infra"
export STUB_LOG="$WORK/calls.log"

# pg_dump output that survives backup.sh's checks: a schema, and enough
# incompressible bytes to clear the 10 KiB floor once gzipped.
cat > "$BIN/docker" <<'EOF'
#!/usr/bin/env bash
echo "docker $*" >> "$STUB_LOG"
echo "CREATE TABLE workflow_entity (id text);"
head -c 30000 /dev/urandom | base64 | sed 's/^/-- /'
EOF
cat > "$BIN/age" <<'EOF'
#!/usr/bin/env bash
echo "age $*" >> "$STUB_LOG"
out=""; in=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift ;;
    -r) shift ;;
    *) in="$1" ;;
  esac
  shift
done
cp "$in" "$out"
EOF
cat > "$BIN/curl" <<'EOF'
#!/usr/bin/env bash
echo "curl $*" >> "$STUB_LOG"
cat > "$(dirname "$STUB_LOG")/curl-config"
for a in "$@"; do
  case "$a" in *.age) [ -f "$a" ] && echo "uploaded-existing $a" >> "$STUB_LOG" ;; esac
done
exit "${STUB_CURL_RC:-0}"
EOF
chmod +x "$BIN/docker" "$BIN/age" "$BIN/curl"

SECRET="scw-secret-5678"
export PATH="$BIN:$PATH" INFRA_DIR="$WORK/infra" DEST="$WORK/backups" \
  BACKUP_CONFIG="$WORK/backup.env"

write_config() {
  cat > "$WORK/backup.env" <<EOF
BACKUP_S3_BUCKET=ose-n8n-backups
BACKUP_S3_ACCESS_KEY=SCWTESTACCESS
BACKUP_S3_SECRET_KEY=$SECRET
BACKUP_AGE_RECIPIENT=age1testrecipient
EOF
  chmod 600 "$WORK/backup.env"
}

failures=0
rc=0
run() {
  case_name="$1"; shift
  : > "$STUB_LOG"
  rm -rf "$DEST"
  rc=0
  "$BACKUP" > "$WORK/out.log" 2>&1 || rc=$?
}
fail() {
  echo "FAIL [$case_name] $*"
  sed 's/^/    | /' "$STUB_LOG"
  sed 's/^/    > /' "$WORK/out.log"
  failures=$((failures + 1))
}
count() { grep -c -E -e "$1" "$STUB_LOG" || true; }
dumps() { find "$DEST" -name 'n8n-*.sql.gz' | wc -l | tr -d ' '; }
sealed() { find "$DEST" -name '*.age' | wc -l | tr -d ' '; }

run "no config keeps the dump on the box"
[ "$rc" -eq 0 ] || fail "expected exit 0, got $rc"
[ "$(count '^(age|curl) ')" -eq 0 ] || fail "encrypted or uploaded without a config"
[ "$(dumps)" -eq 1 ] || fail "expected one local dump"
[ "$(grep -c 'only on this box' "$WORK/out.log" || true)" -gt 0 ] || fail "no warning that the dump stayed local"
[ "$(grep -c 'ok ' "$DEST/backup.log" || true)" -gt 0 ] || fail "backup.log did not record the run"

write_config
run "a config uploads an encrypted copy"
[ "$rc" -eq 0 ] || fail "expected exit 0, got $rc"
[ "$(count '^age -r age1testrecipient ')" -eq 1 ] || fail "not encrypted to the recipient"
[ "$(count '^uploaded-existing .*\.sql\.gz\.age$')" -eq 1 ] || fail "the upload was not the encrypted file"
[ "$(count 'aws-sigv4 aws:amz:pl-waw:s3 ')" -eq 1 ] || fail "not signed for pl-waw"
[ "$(count 'https://ose-n8n-backups\.s3\.pl-waw\.scw\.cloud/n8n-.*\.sql\.gz\.age')" -eq 1 ] || fail "wrong object URL"
[ "$(count "$SECRET")" -eq 0 ] || fail "the secret key reached an argument list"
[ "$(grep -c -F "$SECRET" "$WORK/curl-config" || true)" -eq 1 ] || fail "the secret key did not reach curl on stdin"
[ "$(sealed)" -eq 0 ] || fail "the encrypted copy was left behind"
[ "$(dumps)" -eq 1 ] || fail "the local dump is gone"

STUB_CURL_RC=22 run "a failed upload fails the run and keeps the dump"
[ "$rc" -ne 0 ] || fail "expected a non-zero exit"
[ "$(dumps)" -eq 1 ] || fail "the local dump is gone"
[ "$(sealed)" -eq 0 ] || fail "the encrypted copy was left behind"

chmod 644 "$WORK/backup.env"
run "a readable config is refused"
[ "$rc" -ne 0 ] || fail "expected a non-zero exit"
[ "$(count '^curl ')" -eq 0 ] || fail "uploaded with a world-readable config"

write_config
sed -i.bak '/BACKUP_AGE_RECIPIENT/d' "$WORK/backup.env"
run "a missing setting is named"
[ "$rc" -ne 0 ] || fail "expected a non-zero exit"
[ "$(grep -c 'missing: BACKUP_AGE_RECIPIENT' "$WORK/out.log" || true)" -eq 1 ] || fail "the error does not name the setting"
[ "$(count '^curl ')" -eq 0 ] || fail "uploaded without a recipient"

finished=true
if [ "$failures" -gt 0 ]; then
  echo "$failures assertion(s) failed"
  exit 1
fi
echo "backup-upload: all cases passed"
