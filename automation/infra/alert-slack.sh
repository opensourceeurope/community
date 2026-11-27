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

# The checks exit 2 when they could not run at all, such as on a rate limit.
# That is a different alert from what they found, so it gets its own wording
# and its own entry in the state file: a check that could not run and then
# finds a real problem alerts again. The state file holds the time of the
# alert and its kind. One holding only a time is from before kinds, a failure.
if [ "$recovered" = true ]; then
  [ -f "$state" ] || exit 0
  read -r _ kind < "$state" || true
  case "${kind:-}" in
    unchecked) kind="recovered-unchecked" ;;
    *) kind=recovered ;;
  esac
else
  status=$(systemctl show -p ExecMainStatus --value "$unit" 2>/dev/null || true)
  if [ "$status" = 2 ]; then kind=unchecked; else kind=failed; fi
  if [ -f "$state" ]; then
    read -r alerted alerted_kind < "$state" || true
    if [ "${alerted_kind:-failed}" = "$kind" ]; then
      echo "$unit is still failing. Alerted at ${alerted:-an unknown time}, not posting again"
      exit 0
    fi
  fi
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
else
  # Only the run that failed: its invocation id picks the unit's own output and
  # leaves out systemd's start and stop lines and any earlier run. A unit with
  # no id, or no output under it, falls back to the journal's last lines. A unit
  # the caller cannot read the journal of still gets an alert, just without
  # the lines.
  invocation=$(systemctl show -p InvocationID --value "$unit" 2>/dev/null || true)
  log=""
  if [ -n "$invocation" ]; then
    log=$(journalctl "_SYSTEMD_INVOCATION_ID=$invocation" -n "$LINES" --no-pager -o cat 2>/dev/null || true)
  fi
  if [ -z "$log" ]; then
    log=$(journalctl -u "$unit" -n "$LINES" --no-pager -o cat 2>/dev/null || true)
  fi
fi

body=$(mktemp)
response=$(mktemp)
trap 'rm -f "$body" "$response"' EXIT

# Unit names, the hostname and log lines are data, not Slack markup. `&` is
# escaped first, the same rule the workflows' escapeSlack helper follows.
LOG="$log" python3 - "$kind" "$unit" "$host" "$channel" > "$body" <<'PY'
import json, os, sys

kind, unit, host, channel = sys.argv[1:5]

# What a failure means to a reader who has never heard of the unit, and what
# to do about it, then the line its recovery posts. A check also has a name,
# for when it could not run at all. A unit missing here still alerts, under its
# own name.
MEANING = {
    "ose-n8n-advisories.service": {
        "failed": (
            "n8n needs a security upgrade",
            "A published security advisory affects the n8n version production runs. "
            "To upgrade, open a pull request that sets the n8n image in "
            "`automation/infra/docker-compose.yml` to the version below. "
            "The deploy upgrades the box within ten minutes of the merge.",
        ),
        "recovered": "The n8n security upgrade is live: no published advisory affects the version production runs",
        "check": "n8n security advisory check",
    },
    "ose-deploy.service": {
        "failed": (
            "Changes merged to main are not reaching production",
            "The deploy that copies main onto the box failed. It retries every ten "
            "minutes, but nothing merged since reaches production until the error "
            "below is fixed.",
        ),
        "recovered": "Deploys reach production again",
    },
    "ose-backup.service": {
        "failed": (
            "The daily database backup did not finish",
            "Either the backup on the box or its encrypted copy off the box failed. "
            "The error below says which. Until a backup succeeds, a restore falls "
            "back to an older one.",
        ),
        "recovered": "The database backup works again",
    },
    "ose-env-drift.service": {
        "failed": (
            "A setting on the box differs from what git requires",
            "A value in the box's `.env`, or in the running n8n, does not match what "
            "git marks as required. The lines below name each value and how to fix it.",
        ),
        "recovered": "The box's settings match git again",
        "check": "check of the box's settings",
    },
    "ose-inactive-workflows.service": {
        "failed": (
            "A workflow is switched off and waiting to be activated",
            "A workflow that git ships is inactive in n8n, so it does not run. "
            "Activate it in the n8n editor once you have reviewed it.",
        ),
        "recovered": "Every workflow is active again",
        "check": "check for switched-off workflows",
    },
    "ose-workflow-recovery.service": {
        "failed": (
            "The check that announces fixed workflows is failing",
            "When a workflow that failed starts working again, nobody is told until "
            "this check runs again. The error below says why.",
        ),
        "recovered": "The check that announces fixed workflows works again",
    },
}

def esc(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

meaning = MEANING.get(unit)
check = meaning.get("check") if meaning else None
source = "_From %s on %s._" % (esc(unit), esc(host))

if kind == "unchecked" and check:
    title = "The %s could not run" % check
    detail = ("So nothing is known yet about what it checks. The error below says "
              "why. A network or API hiccup clears on its own at the next run.")
elif kind in ("failed", "unchecked") and meaning:
    title, detail = meaning["failed"]
elif kind == "recovered-unchecked" and check:
    title, detail = "The %s runs again and finds nothing wrong" % check, None
elif kind.startswith("recovered") and meaning:
    title, detail = meaning["recovered"], None
else:
    title = detail = None

if kind in ("failed", "unchecked"):
    if title:
        text = ":rotating_light: *%s*\n%s" % (title, detail)
    else:
        text = ":rotating_light: *%s* failed on %s" % (esc(unit), esc(host))
    log = os.environ.get("LOG", "").strip()
    if log:
        text += "\n```\n%s\n```" % esc(log).replace("```", "'''")
    if title:
        text += "\n" + source
    text += "\nFull log: `journalctl -u %s`" % esc(unit)
elif title:
    text = ":white_check_mark: *%s*\n%s" % (title, source)
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
  echo "$(date -u +%FT%TZ) $kind" > "$state"
fi
echo "posted: $unit $kind"
