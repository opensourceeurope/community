#!/usr/bin/env bash
# Bring the checkout on this box up to origin/main and push the workflow
# exports in automation/n8n into the running n8n.
#
# The box pulls. GitHub never pushes, so no n8n API key lives in a GitHub
# secret and nothing has to be opened inbound for a deploy.
#
# Run from anywhere. Installed as a timer by the runbook in README.md.
#
#   deploy-workflows.sh                 fast-forward, then sync
#   deploy-workflows.sh --dry-run       report what origin/main would change, change nothing
#   deploy-workflows.sh --no-pull       sync the checkout as it stands, leave git alone
#   deploy-workflows.sh --allow-create  also add an export the instance does not have yet
set -euo pipefail

REPO="${REPO:-$HOME/community}"
BRANCH="${BRANCH:-main}"
KEY_FILE="${N8N_API_KEY_FILE:-$HOME/.n8n-api-key}"

EXPORTS="automation/n8n"
INFRA_DIR="$REPO/automation/infra"
SYNC="$INFRA_DIR/sync-workflows.py"
CHECK="$REPO/automation/scripts/check-workflows.py"

dry_run=false
pull=true
sync_args=()

die() { echo "ERROR: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) dry_run=true; sync_args+=(--dry-run) ;;
    --no-pull) pull=false ;;
    --allow-create) sync_args+=(--allow-create) ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option $1" ;;
  esac
  shift
done

[ -d "$REPO/.git" ] || die "no checkout at $REPO. Set REPO to point at it"
[ -f "$SYNC" ] || die "no $SYNC in the checkout"
command -v python3 >/dev/null || die "python3 is missing: sudo apt-get install -y python3"

if [ -z "${N8N_API_URL:-}" ]; then
  [ -f "$INFRA_DIR/.env" ] || die "no $INFRA_DIR/.env to read N8N_HOST from. Set N8N_API_URL instead"
  host=$(sed -n 's/^[[:space:]]*N8N_HOST[[:space:]]*=[[:space:]]*//p' "$INFRA_DIR/.env" | tail -n 1 | tr -d "\"'")
  [ -n "$host" ] || die "no N8N_HOST in $INFRA_DIR/.env. Set N8N_API_URL instead"
  N8N_API_URL="https://$host"
fi
export N8N_API_URL

# The key stays in its file: this passes the path, so the value never reaches an
# argument list, an environment dump or this log.
if [ -z "${N8N_API_KEY:-}" ]; then
  [ -f "$KEY_FILE" ] || die "no API key at $KEY_FILE. See 'Deploying the workflows on merge' in README.md"
  mode=$(stat -c %a "$KEY_FILE" 2>/dev/null || stat -f %Lp "$KEY_FILE")
  case "$mode" in
    600|400) ;;
    *) die "$KEY_FILE is mode $mode, so others can read it: chmod 600 $KEY_FILE" ;;
  esac
  export N8N_API_KEY_FILE="$KEY_FILE"
fi

dir="$REPO/$EXPORTS"

if [ "$pull" = true ]; then
  current=$(git -C "$REPO" rev-parse --abbrev-ref HEAD)
  [ "$current" = "$BRANCH" ] || die "the checkout is on '$current', not '$BRANCH'. Deploying a branch is a manual decision: git -C $REPO checkout $BRANCH"
  [ -z "$(git -C "$REPO" status --porcelain)" ] || die "the checkout has local changes. Read 'git -C $REPO status' before deploying over them"

  git -C "$REPO" fetch --quiet origin "$BRANCH"
  before=$(git -C "$REPO" rev-parse HEAD)
  after=$(git -C "$REPO" rev-parse "origin/$BRANCH")

  if [ "$before" = "$after" ]; then
    echo "$(date -u +%FT%TZ) checkout already at ${after:0:8}"
  elif [ "$dry_run" = true ]; then
    echo "$(date -u +%FT%TZ) would fast-forward ${before:0:8} to ${after:0:8}:"
    git -C "$REPO" log --oneline --no-decorate "$before..$after" | sed 's/^/  /'
    # A dry run leaves the checkout alone, so the exports it compares have to
    # come out of origin/main rather than out of the working tree.
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT
    git -C "$REPO" archive "origin/$BRANCH" "$EXPORTS" | tar -x -C "$tmp"
    dir="$tmp/$EXPORTS"
  else
    git -C "$REPO" merge --ff-only --quiet "origin/$BRANCH"
    echo "$(date -u +%FT%TZ) fast-forwarded ${before:0:8} to ${after:0:8}:"
    git -C "$REPO" log --oneline --no-decorate "$before..$after" | sed 's/^/  /'
  fi
fi

# The same seven checks CI runs on every pull request. An export that fails one
# is a defect that has reached main before, and it must not reach the instance.
if [ -f "$CHECK" ]; then
  python3 "$CHECK" "$dir"
else
  echo "WARNING: no $CHECK, so the export checks did not run" >&2
fi

python3 "$SYNC" --dir "$dir" ${sync_args[@]+"${sync_args[@]}"}
