#!/usr/bin/env bash
# Tell the reviewers' Slack channel that a systemd unit on this box failed, or
# that it works again.
#
#   alert-slack.sh <unit>              post that <unit> failed, once per failure
#   alert-slack.sh --recovered <unit>  post that <unit> recovered, if it had failed
#
# ose-alert@.service runs the first form through OnFailure=, and the deploy and
# backup units run the second after every successful run. The deploy timer
# fires every ten minutes, so a failure that lasts until a person acts would
# otherwise post 144 times a day: a state file per unit remembers that the
# alert was delivered, and the recovery clears it.
#
# The bot token is read from ~/.slack-bot-token (mode 600) and reaches curl on
# stdin, never as an argument. The channel is SLACK_CHANNEL from the compose
# .env, the same channel the application threads use.
set -euo pipefail

INFRA_DIR="${INFRA_DIR:-$HOME/community/automation/infra}"
TOKEN_FILE="${SLACK_TOKEN_FILE:-$HOME/.slack-bot-token}"
STATE_DIR="${OSE_ALERT_STATE_DIR:-$HOME/.local/state/ose-alert}"
SLACK_API="${SLACK_API:-https://slack.com/api/chat.postMessage}"
LINES="${ALERT_LOG_LINES:-15}"

die() { echo "ERROR: $*" >&2; exit 1; }

recovered=false
if [ "${1:-}" = "--recovered" ]; then
  recovered=true
  shift
fi
unit="${1:-}"
[ -n "$unit" ] || die "usage: alert-slack.sh [--recovered] <unit>"

state="$STATE_DIR/$unit.failing"
host=$(hostname)

if [ "$recovered" = true ] && [ ! -f "$state" ]; then
  exit 0
fi
if [ "$recovered" = false ] && [ -f "$state" ]; then
  echo "$unit is still failing. Alerted at $(cat "$state"), not posting again"
  exit 0
fi

[ -f "$TOKEN_FILE" ] || die "no Slack bot token at $TOKEN_FILE, so this alert was not sent"
mode=$(stat -c %a "$TOKEN_FILE" 2>/dev/null || stat -f %Lp "$TOKEN_FILE")
case "$mode" in
  600|400) ;;
  *) die "$TOKEN_FILE is mode $mode, so others can read it: chmod 600 $TOKEN_FILE" ;;
esac
token=$(tr -d '[:space:]' < "$TOKEN_FILE")
[ -n "$token" ] || die "$TOKEN_FILE is empty"

channel=$(sed -n 's/^[[:space:]]*SLACK_CHANNEL[[:space:]]*=[[:space:]]*//p' "$INFRA_DIR/.env" 2>/dev/null | tail -n 1 | tr -d "\"'")
[ -n "$channel" ] || die "no SLACK_CHANNEL in $INFRA_DIR/.env"

if [ "$recovered" = true ]; then
  log=""
  kind=recovered
else
  # A unit the caller cannot read the journal of still gets an alert, just
  # without the lines.
  log=$(journalctl -u "$unit" -n "$LINES" --no-pager -o cat 2>/dev/null || true)
  kind=failed
fi

body=$(mktemp)
response=$(mktemp)
trap 'rm -f "$body" "$response"' EXIT

# Unit names, the hostname and log lines are data, not Slack markup. `&` is
# escaped first, the same rule the workflows' escapeSlack helper follows.
LOG="$log" python3 - "$kind" "$unit" "$host" "$channel" > "$body" <<'PY'
import json, os, sys

kind, unit, host, channel = sys.argv[1:5]

def esc(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

if kind == "failed":
    text = ":rotating_light: *%s* failed on %s" % (esc(unit), esc(host))
    log = os.environ.get("LOG", "").strip()
    if log:
        text += "\n```\n%s\n```" % esc(log).replace("```", "'''")
    text += "\nRead the rest with `journalctl -u %s`" % esc(unit)
else:
    text = ":white_check_mark: *%s* recovered on %s" % (esc(unit), esc(host))

json.dump({"channel": channel, "text": text, "unfurl_links": False}, sys.stdout)
PY

printf 'header = "Authorization: Bearer %s"\n' "$token" \
  | curl -sS --max-time 20 -K - \
      -H 'Content-Type: application/json; charset=utf-8' \
      --data-binary "@$body" -o "$response" "$SLACK_API" \
  || die "could not reach Slack"

ok=$(python3 -c '
import json, sys
try:
    data = json.load(open(sys.argv[1]))
except ValueError:
    print("unreadable response"); sys.exit()
print("ok" if data.get("ok") else data.get("error", "unknown error"))' "$response")
[ "$ok" = "ok" ] || die "Slack refused the message: $ok"

if [ "$recovered" = true ]; then
  rm -f "$state"
else
  mkdir -p "$STATE_DIR"
  date -u +%FT%TZ > "$state"
fi
echo "posted: $unit $kind"
