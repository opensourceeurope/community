#!/usr/bin/env bash
# Runs check 10 of automation/scripts/check-workflows.py against a copy of the
# automation tree with the email copy changed in one place at a time, and checks
# that a change made to only one side of a pair is reported, and that the same
# change made to both passes.
#
#   automation/test/check-workflows-email.sh
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

failures=0

fresh() {  # a copy of what the check reads, so the script finds the copied emails
  rm -rf "$WORK/automation" && mkdir -p "$WORK/automation"
  cp -R "$ROOT/automation/scripts" "$ROOT/automation/n8n" "$ROOT/automation/emails" \
        "$ROOT/automation/infra" "$WORK/automation/"
  cp "$ROOT/automation/.env.example" "$WORK/automation/"
}

replace() {  # replace <file> <old> <new>: in an email file, or in every Code node of an export
  python3 - "$WORK/automation/$1" "$2" "$3" <<'PY'
import json, sys
path, old, new = sys.argv[1:]
if path.endswith(".md"):
    text = open(path).read()
    assert old in text, (path, old)
    open(path, "w").write(text.replace(old, new))
else:
    doc = json.load(open(path))
    hits = 0
    for node in doc["nodes"]:
        code = node["parameters"].get("jsCode", "")
        if old in code:
            node["parameters"]["jsCode"] = code.replace(old, new)
            hits += 1
    assert hits, (path, old)
    json.dump(doc, open(path, "w"), indent=2, ensure_ascii=False)
PY
}

expect() {  # expect <case> <text the output must contain, or "pass">
  local out rc=0
  out=$(python3 -B "$WORK/automation/scripts/check-workflows.py" 2>&1) || rc=$?
  if [ "$2" = pass ]; then
    [ "$rc" -eq 0 ] || { echo "FAIL [$1] expected a pass, got:"; printf '%s\n' "$out" | sed 's/^/    > /'; failures=$((failures + 1)); }
  elif [ "$rc" -eq 0 ] || [ "$(printf '%s\n' "$out" | grep -c -F -e "$2" || true)" -eq 0 ]; then
    echo "FAIL [$1] expected a failure mentioning: $2"
    printf '%s\n' "$out" | sed 's/^/    > /'
    failures=$((failures + 1))
  fi
}

OLD="when you get a chance"
NEW="whenever suits you"

fresh
expect "the committed copy" pass

fresh
replace emails/reminder.md "$OLD" "$NEW"
expect "the file changed, the node did not" "automation/emails/reminder.md: no Code node feeding an email send"

fresh
replace n8n/followup.json "$OLD" "$NEW"
expect "the node changed, the file did not" "Render reminder: the email whose subject line is"

fresh
replace emails/reminder.md "$OLD" "$NEW"
replace n8n/followup.json "$OLD" "$NEW"
expect "both changed the same way" pass

fresh
replace emails/decision-approved.md "Welcome on board!" "Welcome aboard!"
replace n8n/intake-sweep.json "Welcome on board!" "Welcome aboard!"
expect "one intake workflow left behind" "oc-events-intake.json: Render decision email: the email whose subject line is"

fresh
replace emails/decision-approved.md "Welcome on board!" "Welcome aboard!"
replace n8n/intake-sweep.json "Welcome on board!" "Welcome aboard!"
replace n8n/oc-events-intake.json "Welcome on board!" "Welcome aboard!"
expect "the Slack dialog's prefill left behind" "decide-from-slack.json: Render the decision dialog: the email whose subject line is"

fresh
replace n8n/decide-from-slack.json "Welcome on board!" "Welcome aboard!"
expect "only the Slack dialog's prefill changed" "decide-from-slack.json: Render the decision dialog: the email whose subject line is"

fresh
replace n8n/oc-events-intake.json "\`Subject: {{ collective_name }} is approved" "'Subject: ' + \`{{ collective_name }} is approved"
expect "a template split by concatenation, its file sent by the other workflow" \
  "oc-events-intake.json: Render decision email: check 10 finds 2 email template(s) it can read but 3 'Subject: ' line(s)"

fresh
replace emails/reminder.md "$OLD" "when you get a \`chance\`"
replace n8n/followup.json "$OLD" "when you get a \\\\\`chance\\\\\`"
expect "a backtick in the copy" "contains a backtick, which check 10 cannot read"

fresh
rm "$WORK/automation/emails/form-received.md"
expect "a file deleted" "Render confirmation email: the email whose subject line is"

if [ "$failures" -gt 0 ]; then
  echo "$failures case(s) failed"
  exit 1
fi
echo "check-workflows-email: all cases passed"
