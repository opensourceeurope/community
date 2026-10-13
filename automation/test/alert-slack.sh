#!/usr/bin/env bash
# Runs automation/infra/alert-slack.sh with curl and journalctl replaced by
# stubs, and checks what it would post: escaped text, a token that never
# reaches an argument list, one post per failure, and a recovery only after a
# failure. Nothing here reaches Slack.
#
#   automation/test/alert-slack.sh
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
ALERT="${ALERT:-$(cd "$HERE/../infra" && pwd)/alert-slack.sh}"
WORK=$(mktemp -d)
finished=false
case_name="setup"
trap 'rc=$?; [ $rc -eq 0 ] || [ "$finished" = true ] || echo "alert-slack: the test itself stopped with $rc in [$case_name]"; rm -rf "$WORK"' EXIT

BIN="$WORK/bin"
mkdir -p "$BIN" "$WORK/infra"
export STUB_DIR="$WORK"

cat > "$BIN/curl" <<'EOF'
#!/usr/bin/env bash
echo "curl $*" >> "$STUB_DIR/argv.log"
cat > "$STUB_DIR/curl-config"
out=""
while [ $# -gt 0 ]; do
  case "$1" in
    --data-binary) cp "${2#@}" "$STUB_DIR/body.json"; shift ;;
    -o) out="$2"; shift ;;
  esac
  shift
done
response='{"ok":true}'
[ -z "${STUB_SLACK_RESPONSE:-}" ] || response="$STUB_SLACK_RESPONSE"
printf '%s\n' "$response" > "$out"
echo post >> "$STUB_DIR/posts.log"
EOF
cat > "$BIN/journalctl" <<'EOF'
#!/usr/bin/env bash
echo 'deploy <!channel> & friends'
echo 'ERROR: no API key'
EOF
chmod +x "$BIN/curl" "$BIN/journalctl"

TOKEN="xoxb-test-token-1234"
printf '%s\n' "$TOKEN" > "$WORK/token"
chmod 600 "$WORK/token"
echo "SLACK_CHANNEL=#ops-test" > "$WORK/infra/.env"

export PATH="$BIN:$PATH" INFRA_DIR="$WORK/infra" SLACK_TOKEN_FILE="$WORK/token" \
  OSE_ALERT_STATE_DIR="$WORK/state"
UNIT=ose-deploy.service

failures=0
rc=0
run() {  # run <case name> [alert-slack.sh arguments]
  case_name="$1"; shift
  : > "$WORK/posts.log"
  rm -f "$WORK/body.json"
  rc=0
  "$ALERT" "$@" > "$WORK/out.log" 2>&1 || rc=$?
}
fail() {
  echo "FAIL [$case_name] $*"
  sed 's/^/    > /' "$WORK/out.log"
  failures=$((failures + 1))
}
posts() { local n; n=$(grep -c post "$WORK/posts.log" 2>/dev/null || true); echo "${n:-0}"; }
body_has() { [ "$(grep -c -F -e "$1" "$WORK/body.json" 2>/dev/null || true)" -gt 0 ] || fail "the message lacks: $1"; }
body_lacks() { [ "$(grep -c -F -e "$1" "$WORK/body.json" 2>/dev/null || true)" -eq 0 ] || fail "the message contains: $1"; }

run "a failure posts once, escaped" "$UNIT"
[ "$rc" -eq 0 ] || fail "expected exit 0, got $rc"
[ "$(posts)" -eq 1 ] || fail "expected one post"
body_has ':rotating_light:'
body_has '&lt;!channel&gt; &amp; friends'
body_lacks '<!channel>'
body_has '"channel": "#ops-test"'
[ "$(grep -c -F -e "$TOKEN" "$WORK/argv.log" || true)" -eq 0 ] || fail "the token reached curl's argument list"
[ "$(grep -c -F -e "$TOKEN" "$WORK/curl-config" || true)" -eq 1 ] || fail "the token did not reach curl on stdin"

run "the same failure does not post again" "$UNIT"
[ "$rc" -eq 0 ] || fail "expected exit 0, got $rc"
[ "$(posts)" -eq 0 ] || fail "posted a second time"

run "recovery posts once" --recovered "$UNIT"
[ "$rc" -eq 0 ] || fail "expected exit 0, got $rc"
[ "$(posts)" -eq 1 ] || fail "expected one recovery post"
body_has ':white_check_mark:'

run "a recovery with no failure posts nothing" --recovered "$UNIT"
[ "$rc" -eq 0 ] || fail "expected exit 0, got $rc"
[ "$(posts)" -eq 0 ] || fail "posted a recovery for a unit that never failed"

STUB_SLACK_RESPONSE='{"ok":false,"error":"not_in_channel"}' run "a refused alert fails" "$UNIT"
[ "$rc" -ne 0 ] || fail "expected a non-zero exit"
[ "$(grep -c not_in_channel "$WORK/out.log" || true)" -gt 0 ] || fail "the error does not name Slack's reason"

run "an alert that was not delivered is retried" "$UNIT"
[ "$(posts)" -eq 1 ] || fail "the undelivered alert was not retried"

chmod 644 "$WORK/token"
run "a readable token file is refused" --recovered "$UNIT"
[ "$rc" -ne 0 ] || fail "expected a non-zero exit"
[ "$(posts)" -eq 0 ] || fail "posted with a world-readable token"

finished=true
if [ "$failures" -gt 0 ]; then
  echo "$failures assertion(s) failed"
  exit 1
fi
echo "alert-slack: all cases passed"
