#!/usr/bin/env bash
# Runs automation/infra/deploy.sh against a throwaway git repository and an
# origin it can fetch from, with docker, sudo, the workflow sync and the backup
# replaced by stubs that only record how they were called. Each case pushes one
# kind of change to origin, runs a deploy, and checks what it ran.
#
# Nothing here touches Docker, systemd or an n8n instance.
#
#   automation/test/deploy-infra.sh
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
DEPLOY="${DEPLOY:-$(cd "$HERE/../infra" && pwd)/deploy.sh}"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

export STUB_LOG="$WORK/calls.log"
BIN="$WORK/bin"
mkdir -p "$BIN"

cat > "$BIN/docker" <<'EOF'
#!/usr/bin/env bash
echo "docker $*" >> "$STUB_LOG"
case "$*" in
  *"config --format json"*)
    printf '{"name":"infra","services":{"n8n":{"image":"%s"}}}\n' "${STUB_N8N_IMAGE:-n8n:1}" ;;
  *"config --quiet"*)
    if [ "${STUB_UNSET:-0}" = 1 ]; then
      echo 'WARN[0000] The "NEW_VAR" variable is not set. Defaulting to a blank string.' >&2
    fi ;;
  *"caddy validate"*) exit "${STUB_CADDY_RC:-0}" ;;
  *"ps -q n8n"*) echo cid ;;
  inspect*) echo "${STUB_RUNNING_IMAGE:-n8n:1}" ;;
esac
exit 0
EOF
cat > "$BIN/sudo" <<'EOF'
#!/usr/bin/env bash
echo "sudo $*" >> "$STUB_LOG"
EOF
# /healthz answers 502 for the first STUB_HEALTH_FAILS calls, then 200.
cat > "$BIN/curl" <<'EOF'
#!/usr/bin/env bash
echo "curl $*" >> "$STUB_LOG"
n=$(cat "$STUB_LOG.health" 2>/dev/null || echo 0)
echo $((n + 1)) > "$STUB_LOG.health"
if [ "$n" -lt "${STUB_HEALTH_FAILS:-0}" ]; then printf 502; else printf 200; fi
EOF
chmod +x "$BIN/docker" "$BIN/sudo" "$BIN/curl"

# --- the repository, as origin and as the box's checkout -------------------
git init -q --bare "$WORK/origin.git"
git -C "$WORK/origin.git" symbolic-ref HEAD refs/heads/main
git clone -q "$WORK/origin.git" "$WORK/dev" 2>/dev/null
dev() { git -C "$WORK/dev" "$@"; }
dev config user.email test@example.org
dev config user.name test
dev checkout -q -b main

mkdir -p "$WORK/dev/automation/infra/systemd" "$WORK/dev/automation/infra/public" \
  "$WORK/dev/automation/n8n" "$WORK/dev/automation/scripts"
echo ".env" > "$WORK/dev/.gitignore"
echo "services: {}" > "$WORK/dev/automation/infra/docker-compose.yml"
echo "example.org {}" > "$WORK/dev/automation/infra/Caddyfile"
echo "[Service]" > "$WORK/dev/automation/infra/systemd/ose-test.service"
echo "png" > "$WORK/dev/automation/infra/public/og_image.png"
echo "{}" > "$WORK/dev/automation/n8n/flow.json"
cat > "$WORK/dev/automation/infra/sync-workflows.py" <<'EOF'
import os, sys
open(os.environ["STUB_LOG"], "a").write("sync %s\n" % " ".join(sys.argv[1:]))
sys.exit(int(os.environ.get("STUB_SYNC_RC", "0")))
EOF
cat > "$WORK/dev/automation/scripts/check-workflows.py" <<'EOF'
import os, sys
open(os.environ["STUB_LOG"], "a").write("check %s %s\n" % (sys.argv[0], " ".join(sys.argv[1:])))
EOF
echo "DRY_RUN=true" > "$WORK/dev/automation/.env.example"
cat > "$WORK/dev/automation/infra/backup.sh" <<'EOF'
#!/usr/bin/env bash
echo "backup" >> "$STUB_LOG"
exit "${STUB_BACKUP_RC:-0}"
EOF
chmod +x "$WORK/dev/automation/infra/backup.sh"
dev add -A
dev commit -q -m seed
dev push -q origin main 2>/dev/null

git clone -q "$WORK/origin.git" "$WORK/box" 2>/dev/null
ENV_FILE="$WORK/box/automation/infra/.env"
write_env() {  # write_env [LINE ...]: a complete .env, each LINE replacing the default for its name
  local defaults=(
    "N8N_HOST=automation.example.org"
    "APPLY_HOST=apply.example.org"
    "POSTGRES_PASSWORD=pg"
    "N8N_ENCRYPTION_KEY=key"
    "AI_API_KEY=secret-ai-value"
    'SLACK_CHANNEL="#reviews"'
    "SMTP_FROM=Test <test@example.org>"
    "DRY_RUN=true"
    "DRY_RUN_RECIPIENT=test@example.org"
  )
  local line name override
  : > "$ENV_FILE"
  for line in "${defaults[@]}"; do
    name=${line%%=*}
    for override in "$@"; do
      [ "$(echo "$override" | sed -E 's/^export[[:space:]]+//; s/[[:space:]]*=.*//')" = "$name" ] && continue 2
    done
    echo "$line" >> "$ENV_FILE"
  done
  for override in "$@"; do echo "$override" >> "$ENV_FILE"; done
}
write_env

change() {  # change <path under the repo> <content>
  echo "$2" > "$WORK/dev/$1"
  dev add -A
  dev commit -q -m "change $1"
  dev push -q origin main 2>/dev/null
}

# --- running and asserting --------------------------------------------------
export PATH="$BIN:$PATH"
export REPO="$WORK/box" N8N_API_URL=https://automation.example.org N8N_API_KEY=stub
export OSE_DEPLOY_STATE_DIR="$WORK/state" OSE_UNIT_DIR="$WORK/units"
export OSE_HEALTH_TRIES=3 OSE_HEALTH_INTERVAL=0
MARKER="$WORK/state/infra-applied"

failures=0
case_name=""
rc=0

# A helper that dies under `set -e` would end the run silently, which reads as
# a pass. This makes any such exit loud.
finished=false
trap 'rc=$?; [ $rc -eq 0 ] || [ "$finished" = true ] || echo "deploy-infra: the test itself stopped with $rc in [$case_name]"; rm -rf "$WORK"' EXIT

run() {  # run <case name> [deploy.sh arguments]
  case_name="$1"; shift
  : > "$STUB_LOG"
  rm -f "$STUB_LOG.health"
  rc=0
  "$DEPLOY" "$@" > "$WORK/out.log" 2>&1 || rc=$?
}

fail() {
  echo "FAIL [$case_name] $*"
  sed 's/^/    | /' "$STUB_LOG"
  sed 's/^/    > /' "$WORK/out.log"
  failures=$((failures + 1))
}

count() { grep -c -E -e "$1" "$STUB_LOG" || true; }
ran() { [ "$(count "$1")" -gt 0 ] || fail "expected a call matching: $1"; }
not_ran() { [ "$(count "$1")" -eq 0 ] || fail "expected no call matching: $1"; }
exit_ok() { [ "$rc" -eq 0 ] || fail "expected exit 0, got $rc"; }
exit_failed() { [ "$rc" -ne 0 ] || fail "expected a non-zero exit"; }
marker_is() {
  local want
  want=$(git -C "$WORK/box" rev-parse "$1")
  [ "$(cat "$MARKER" 2>/dev/null)" = "$want" ] || fail "expected the marker at $1"
}
first_line() { { grep -n -E -e "$1" "$STUB_LOG" || true; } | head -n 1 | cut -d: -f1; }
before() {
  local a b
  a=$(first_line "$1"); b=$(first_line "$2")
  if [ -z "$a" ] || [ -z "$b" ] || [ "$a" -ge "$b" ]; then fail "expected '$1' before '$2'"; fi
}

UP_ALL='compose .* up -d$'
RECREATE_CADDY='up -d --no-deps --force-recreate caddy$'
VALIDATE='caddy validate'

# --- cases ------------------------------------------------------------------
run "no marker applies everything"
exit_ok
ran 'config --quiet'
ran "$VALIDATE"
ran "$UP_ALL"
ran "$RECREATE_CADDY"
ran 'sudo install -m 644 .*/systemd/ose-test.service'
ran 'sudo systemctl daemon-reload'
ran '^sync '
marker_is HEAD

run "nothing changed runs no docker action"
exit_ok
not_ran 'config --quiet'
not_ran ' up -d'
not_ran '^sudo '
ran '^sync '

change automation/infra/docker-compose.yml "services: {a: {}}"
run "a compose change runs preflight then up -d"
exit_ok
before 'config --quiet' "$UP_ALL"
not_ran "$RECREATE_CADDY"
not_ran "$VALIDATE"
marker_is HEAD

change automation/infra/Caddyfile "example.org { respond 200 }"
run "a Caddyfile change validates then recreates caddy"
exit_ok
before "$VALIDATE" "$RECREATE_CADDY"
not_ran "$UP_ALL"

change automation/infra/systemd/ose-test.service "[Service]
Type=oneshot"
run "a unit change installs it and reloads"
exit_ok
before 'sudo install -m 644 .*/ose-test.service' 'sudo systemctl daemon-reload'
not_ran ' up -d'

change automation/infra/public/og_image.png "png2"
run "a public/ change runs nothing"
exit_ok
not_ran ' up -d'
not_ran '^sudo '
marker_is HEAD

previous=$(cat "$MARKER")
change automation/infra/docker-compose.yml "services: {b: {}}"
STUB_UNSET=1 run "an unset variable stops the run before anything changes"
exit_failed
not_ran ' up -d'
not_ran '^sync '
[ "$(cat "$MARKER")" = "$previous" ] || fail "the marker moved"
grep -c 'missing from' "$WORK/out.log" >/dev/null || fail "the error does not name the missing variable"

run "the next run retries the apply once the variable exists"
exit_ok
ran "$UP_ALL"
marker_is HEAD

change automation/infra/docker-compose.yml "services: {c: {}}"
STUB_N8N_IMAGE=n8n:2 run "a new n8n image is backed up first"
exit_ok
before '^backup' "$UP_ALL"

change automation/infra/docker-compose.yml "services: {d: {}}"
previous=$(cat "$MARKER")
STUB_N8N_IMAGE=n8n:2 STUB_BACKUP_RC=1 run "a failed backup leaves n8n alone"
exit_failed
not_ran ' up -d'
[ "$(cat "$MARKER")" = "$previous" ] || fail "the marker moved"

STUB_N8N_IMAGE=n8n:1 run "same image, no backup"
exit_ok
not_ran '^backup'
ran "$UP_ALL"

change automation/infra/Caddyfile "broken {"
previous=$(cat "$MARKER")
STUB_CADDY_RC=1 run "an invalid Caddyfile is not deployed"
exit_failed
not_ran "$RECREATE_CADDY"
[ "$(cat "$MARKER")" = "$previous" ] || fail "the marker moved"
run "the fixed environment applies the pending Caddyfile"
exit_ok
ran "$RECREATE_CADDY"

change automation/infra/docker-compose.yml "services: {e: {}}"
STUB_SYNC_RC=1 run "a failed workflow sync still records the infra apply"
exit_failed
ran "$UP_ALL"
marker_is HEAD

change automation/infra/docker-compose.yml "services: {f: {}}"
previous=$(cat "$MARKER")
head_before=$(git -C "$WORK/box" rev-parse HEAD)
run "a dry run checks origin/main and changes nothing" --dry-run
exit_ok
ran 'config --quiet'
not_ran ' up -d'
not_ran '^sudo '
not_ran "-f $WORK/box/automation/infra/docker-compose.yml .*config --quiet"
ran "-f .*/automation/infra/docker-compose.yml .*config --quiet"
[ "$(cat "$MARKER")" = "$previous" ] || fail "the marker moved"
[ "$(git -C "$WORK/box" rev-parse HEAD)" = "$head_before" ] || fail "the checkout moved"
ran '^sync .*--dry-run'
not_ran "^check $WORK/box/"
ran '^check .*/automation/scripts/check-workflows.py .*/automation/n8n$'

change automation/infra/docker-compose.yml "services: {g: {}}"
previous=$(cat "$MARKER")
write_env "SMTP_FROM="
run "an empty required value stops the run before anything changes"
exit_failed
not_ran ' up -d'
not_ran '^sync '
[ "$(cat "$MARKER")" = "$previous" ] || fail "the marker moved"
[ "$(grep -c 'SMTP_FROM' "$WORK/out.log" || true)" -gt 0 ] || fail "the error does not name the empty variable"
[ "$(grep -c -e '#reviews' -e 'secret-ai-value' "$WORK/out.log" || true)" -eq 0 ] || fail "a value reached the output"

write_env 'SLACK_CHANNEL="" # set later'
run "an empty quoted value with a comment still counts as empty"
exit_failed
[ "$(grep -c 'SLACK_CHANNEL' "$WORK/out.log" || true)" -gt 0 ] || fail "the error does not name SLACK_CHANNEL"

write_env "export AI_API_KEY=other-secret" "DRY_RUN=false # live" "DRY_RUN_RECIPIENT="
run "an export prefix and a trailing comment read the way compose reads them"
exit_ok
ran "$UP_ALL"

write_env "DRY_RUN_RECIPIENT="
run "an empty recipient stops the run while mail is suppressed"
exit_failed
[ "$(grep -c 'DRY_RUN_RECIPIENT' "$WORK/out.log" || true)" -gt 0 ] || fail "the error does not name DRY_RUN_RECIPIENT"

write_env "DRY_RUN= FALSE " "DRY_RUN_RECIPIENT="
run "an empty recipient is fine once DRY_RUN is false"
exit_ok
ran '^sync '

write_env
change automation/infra/docker-compose.yml "services: {h: {}}"
STUB_HEALTH_FAILS=2 run "the sync waits for n8n to come back after a restart"
exit_ok
before "$UP_ALL" '/healthz'
before '/healthz' '^sync '
[ "$(count '/healthz')" -eq 3 ] || fail "expected two failed health checks and one good one"

change automation/infra/docker-compose.yml "services: {i: {}}"
STUB_HEALTH_FAILS=9 run "an n8n that never comes back stops before the sync, with the infra recorded"
exit_failed
not_ran '^sync '
marker_is HEAD
[ "$(grep -c 'did not answer' "$WORK/out.log" || true)" -gt 0 ] || fail "the error does not say n8n did not answer"

change automation/infra/public/og_image.png "png3"
run "a change that restarts nothing does not wait for n8n"
exit_ok
not_ran '/healthz'

finished=true
if [ "$failures" -gt 0 ]; then
  echo "$failures assertion(s) failed"
  exit 1
fi
echo "deploy-infra: all cases passed"
