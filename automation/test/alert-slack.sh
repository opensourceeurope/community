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
# The failed run's own lines come back under its invocation id. The unit's
# whole journal adds systemd's lines and an earlier run, which must not show.
cat > "$BIN/journalctl" <<'EOF'
#!/usr/bin/env bash
case "$*" in
  *_SYSTEMD_INVOCATION_ID=*)
    [ -z "${STUB_NO_INVOCATION_LINES:-}" ] || exit 0
    echo 'deploy <!channel> & friends'
    echo 'ERROR: no API key'
    ;;
  *)
    echo 'Starting the unit...'
    echo 'an earlier run that passed'
    echo 'deploy <!channel> & friends'
    echo 'ERROR: no API key'
    echo 'Main process exited, code=exited, status=1/FAILURE'
    ;;
esac
EOF
cat > "$BIN/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "${STUB_INVOCATION_ID-0123abcd}"
EOF
chmod +x "$BIN/curl" "$BIN/journalctl" "$BIN/systemctl"

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

body_has 'Changes merged to main are not reaching production'
body_has '_From ose-deploy.service on '
body_lacks 'an earlier run that passed'
body_lacks 'Main process exited'

run "the same failure does not post again" "$UNIT"
[ "$rc" -eq 0 ] || fail "expected exit 0, got $rc"
[ "$(posts)" -eq 0 ] || fail "posted a second time"

run "recovery posts once" --recovered "$UNIT"
[ "$rc" -eq 0 ] || fail "expected exit 0, got $rc"
[ "$(posts)" -eq 1 ] || fail "expected one recovery post"
body_has ':white_check_mark:'

body_has 'Deploys reach production again'

run "a recovery with no failure posts nothing" --recovered "$UNIT"
[ "$rc" -eq 0 ] || fail "expected exit 0, got $rc"
[ "$(posts)" -eq 0 ] || fail "posted a recovery for a unit that never failed"

run "the advisory alert says n8n needs an upgrade" ose-n8n-advisories.service
[ "$(posts)" -eq 1 ] || fail "expected one post"
body_has 'n8n needs a security upgrade'
body_has 'automation/infra/docker-compose.yml'
run "the advisory recovery says the upgrade is live" --recovered ose-n8n-advisories.service
body_has 'The n8n security upgrade is live'

run "a unit with no meaning alerts under its own name" ose-something-new.service
[ "$(posts)" -eq 1 ] || fail "expected one post"
body_has '*ose-something-new.service* failed on '
body_has 'ERROR: no API key'

STUB_INVOCATION_ID="" run "with no invocation id the journal's last lines show" ose-no-id.service
body_has 'an earlier run that passed'

STUB_NO_INVOCATION_LINES=1 run "a run with no lines of its own falls back to the journal" ose-no-lines.service
body_has 'Main process exited'

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
