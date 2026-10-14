#!/usr/bin/env bash
# The $env in the single-quoted strings below is JavaScript, planted literally.
# shellcheck disable=SC2016
# Runs check 8 of automation/scripts/check-workflows.py against copies of the
# real exports with one $env read planted in a Code node at a time, and checks
# that each planted read is reported, or passes when it is legitimate.
#
#   automation/test/check-workflows-env.sh
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
CHECK="$ROOT/automation/scripts/check-workflows.py"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

failures=0

plant() {  # plant <JS line>: copies of the exports with the line added to one Code node
  rm -rf "$WORK/n8n" && mkdir -p "$WORK/n8n"
  cp "$ROOT"/automation/n8n/*.json "$WORK/n8n/"
  python3 - "$WORK/n8n/summary.json" "$1" <<'PY'
import json, sys
path, line = sys.argv[1], sys.argv[2]
doc = json.load(open(path))
node = next(n for n in doc["nodes"] if n["type"].endswith(".code"))
node["parameters"]["jsCode"] += "\n" + line
json.dump(doc, open(path, "w"))
PY
}

expect() {  # expect <case> <JS line> <text the output must contain, or "pass">
  local out rc=0
  plant "$2"
  out=$(python3 -B "$CHECK" "$WORK/n8n" 2>&1) || rc=$?
  if [ "$3" = pass ]; then
    [ "$rc" -eq 0 ] || { echo "FAIL [$1] expected a pass, got:"; echo "$out" | sed 's/^/    > /'; failures=$((failures + 1)); }
  elif [ "$rc" -eq 0 ] || [ "$(printf '%s\n' "$out" | grep -c -F -e "$3" || true)" -eq 0 ]; then
    echo "FAIL [$1] expected a failure mentioning: $3"
    printf '%s\n' "$out" | sed 's/^/    > /'
    failures=$((failures + 1))
  fi
}

expect "a passed and documented variable"   'const a = $env.DRY_RUN;'           pass
expect "a variable compose does not pass"   'const a = $env.NEW_THING;'         'reads $env.NEW_THING, which docker-compose.yml does not pass'
expect "an undocumented variable"           'const a = $env.NEW_THING;'         'automation/.env.example does not document'
expect "optional chaining"                  'const a = $env?.OPTIONAL_ONE;'     'reads $env.OPTIONAL_ONE'
expect "brackets with spaces"               "const a = \$env[ 'SPACED_ONE' ];"  'reads $env.SPACED_ONE'
expect "double-quoted brackets"             'const a = $env["QUOTED_ONE"];'     'reads $env.QUOTED_ONE'
expect "a backtick key"                     'const a = $env[`TICKED_ONE`];'     'reads $env.TICKED_ONE'
expect "destructuring"                      'const { DRY_RUN } = $env;'         'cannot name'
expect "a computed key"                     'const a = $env[name];'             'cannot name'

if [ "$failures" -gt 0 ]; then
  echo "$failures case(s) failed"
  exit 1
fi
echo "check-workflows-env: all cases passed"
