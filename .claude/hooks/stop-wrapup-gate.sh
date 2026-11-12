#!/usr/bin/env bash
# Stop hook: a wrap-up gate that checks docs and automation still match the code.
#
# It blocks a stop only when something changed since the last time it looked. The
# first comparison is against the baseline session-baseline.sh records at
# SessionStart. Every time the gate fires it moves that baseline to the current
# state, so the next stop is quiet unless more files changed in between. Without
# that it fired at every stop for the rest of a session after a single edit. It is deliberately NOT the branch diff against main: that reports the
# same long file list at every stop — in sessions that only read files too — which is
# noise rather than a review, and it buries the answer the user actually asked for.
#
# - Silent when nothing changed since the session began.
# - Silent when there is no baseline yet; one is written on the way out, so a resumed
#   session is quiet once and correct afterwards.
# - Reads stop_hook_active so the block cannot loop: the gate costs one pass.
# - A clean result is reported by finishing silently, not by narrating the check.

set -u

input=$(cat 2>/dev/null || true)
stop_active=$(printf '%s' "$input" | jq -r '.stop_hook_active // false' 2>/dev/null || echo false)
[ "$stop_active" = "true" ] && exit 0

session=$(printf '%s' "$input" | jq -r '.session_id // empty' 2>/dev/null || true)

project=$(cd "$(dirname "$0")/../.." 2>/dev/null && pwd)
[ -z "$project" ] && exit 0
git -C "$project" rev-parse --git-dir >/dev/null 2>&1 || exit 0

head_now=$(git -C "$project" rev-parse HEAD 2>/dev/null || echo none)
tree_now=$(git -C "$project" status --porcelain 2>/dev/null | shasum | cut -c1-40)

dir="${TMPDIR:-/tmp}/claude-wrapup-gate"
key=$(printf '%s' "$project" | shasum | cut -c1-12)
baseline="$dir/baseline-$key-$session"

# No baseline (session predates this hook, or SessionStart never ran): record the
# current state and stay quiet. Anything changed from here on is caught normally.
if [ -z "$session" ] || [ ! -f "$baseline" ]; then
  mkdir -p "$dir" 2>/dev/null && printf '%s %s\n' "$head_now" "$tree_now" > "$baseline" 2>/dev/null
  exit 0
fi

read -r head_was tree_was < "$baseline" || exit 0

# Nothing happened this session. Nothing to review, and nothing to say about it.
[ "$head_now" = "$head_was" ] && [ "$tree_now" = "$tree_was" ] && exit 0

changed=$(
  {
    [ "$head_now" != "$head_was" ] && git -C "$project" diff --name-only "$head_was" HEAD 2>/dev/null
    git -C "$project" status --porcelain 2>/dev/null | awk '{print $NF}'
  } | sort -u | grep -v '^$'
)
[ -z "$changed" ] && exit 0

CODE='^automation/'
AUTOMATION='^(\.github/|\.claude/)'
DOCS='^(docs/|README\.md|AGENTS\.md|automation/.*\.md|automation/\.env\.example)'

code_changed=$(printf '%s\n' "$changed" | grep -E "$CODE" | grep -vE "$DOCS")
docs_changed=$(printf '%s\n' "$changed" | grep -E "$DOCS")
automation_changed=$(printf '%s\n' "$changed" | grep -E "$AUTOMATION")

[ -z "$code_changed" ] && [ -z "$docs_changed" ] && [ -z "$automation_changed" ] && exit 0

# The next stop compares against what is reviewed now, so the gate fires once per
# batch of changes rather than at every stop that follows.
printf '%s %s\n' "$head_now" "$tree_now" > "$baseline" 2>/dev/null

count=$(printf '%s\n' "$changed" | grep -c .)
reason="Wrap-up gate: new changes since the last check. Files changed this session ($count), e.g.
$(printf '%s\n' "$changed" | head -8)
Check the docs that describe them (READMEs, automation/docs/, .env.example, AGENTS.md) against the diff: env and credential names, DRY_RUN, what sends an email or Slack message, the advisory-only AI review. Fix any drift and name it in one sentence. If none, finish without mentioning this check."

jq -n --arg r "$reason" '{ decision: "block", reason: $r }'
