#!/usr/bin/env bash
# Bring the checkout on this box up to origin/main, apply what changed in
# automation/infra, then push the workflow exports in automation/n8n into the
# running n8n.
#
# The box pulls. GitHub never pushes, so no n8n API key lives in a GitHub
# secret and nothing has to be opened inbound for a deploy.
#
# Run from anywhere. Installed as a timer by the runbook in README.md.
#
#   deploy.sh                 fast-forward, apply infra, then sync workflows
#   deploy.sh --dry-run       report what origin/main would change, change nothing
#   deploy.sh --no-pull       deploy the checkout as it stands, leave git alone
#   deploy.sh --allow-create  also add an export the instance does not have yet
set -euo pipefail

REPO="${REPO:-$HOME/community}"
BRANCH="${BRANCH:-main}"
KEY_FILE="${N8N_API_KEY_FILE:-$HOME/.n8n-api-key}"
STATE_DIR="${OSE_DEPLOY_STATE_DIR:-$HOME/.local/state/ose-deploy}"
UNIT_DIR="${OSE_UNIT_DIR:-/etc/systemd/system}"

EXPORTS="automation/n8n"
INFRA="automation/infra"
INFRA_DIR="$REPO/$INFRA"
SYNC="$INFRA_DIR/sync-workflows.py"
CHECK="$REPO/automation/scripts/check-workflows.py"
BACKUP="$INFRA_DIR/backup.sh"
MARKER="$STATE_DIR/infra-applied"

dry_run=false
pull=true
sync_args=()

die() { echo "ERROR: $*" >&2; exit 1; }
say() { echo "$(date -u +%FT%TZ) $*"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) dry_run=true; sync_args+=(--dry-run) ;;
    --no-pull) pull=false ;;
    --allow-create) sync_args+=(--allow-create) ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option $1" ;;
  esac
  shift
done

[ -d "$REPO/.git" ] || die "no checkout at $REPO. Set REPO to point at it"
[ -f "$SYNC" ] || die "no $SYNC in the checkout"
[ -f "$INFRA_DIR/.env" ] || die "no $INFRA_DIR/.env. The stack cannot run without it"
command -v python3 >/dev/null || die "python3 is missing: sudo apt-get install -y python3"

if [ -z "${N8N_API_URL:-}" ]; then
  host=$(sed -n 's/^[[:space:]]*N8N_HOST[[:space:]]*=[[:space:]]*//p' "$INFRA_DIR/.env" | tail -n 1 | tr -d "\"'")
  [ -n "$host" ] || die "no N8N_HOST in $INFRA_DIR/.env. Set N8N_API_URL instead"
  N8N_API_URL="https://$host"
fi
export N8N_API_URL

# The key stays in its file: this passes the path, so the value never reaches an
# argument list, an environment dump or this log.
if [ -z "${N8N_API_KEY:-}" ]; then
  [ -f "$KEY_FILE" ] || die "no API key at $KEY_FILE. See 'Deploying on merge' in README.md"
  mode=$(stat -c %a "$KEY_FILE" 2>/dev/null || stat -f %Lp "$KEY_FILE")
  case "$mode" in
    600|400) ;;
    *) die "$KEY_FILE is mode $mode, so others can read it: chmod 600 $KEY_FILE" ;;
  esac
  export N8N_API_KEY_FILE="$KEY_FILE"
fi

dir="$REPO/$EXPORTS"
src="$INFRA_DIR"
target=$(git -C "$REPO" rev-parse HEAD)
tmp=""
trap '[ -z "$tmp" ] || rm -rf "$tmp"' EXIT

if [ "$pull" = true ]; then
  current=$(git -C "$REPO" rev-parse --abbrev-ref HEAD)
  [ "$current" = "$BRANCH" ] || die "the checkout is on '$current', not '$BRANCH'. Deploying a branch is a manual decision: git -C $REPO checkout $BRANCH"
  [ -z "$(git -C "$REPO" status --porcelain)" ] || die "the checkout has local changes. Read 'git -C $REPO status' before deploying over them"

  git -C "$REPO" fetch --quiet origin "$BRANCH"
  before=$(git -C "$REPO" rev-parse HEAD)
  after=$(git -C "$REPO" rev-parse "origin/$BRANCH")
  target="$after"

  if [ "$before" = "$after" ]; then
    say "checkout already at ${after:0:8}"
  elif [ "$dry_run" = true ]; then
    say "would fast-forward ${before:0:8} to ${after:0:8}:"
    git -C "$REPO" log --oneline --no-decorate "$before..$after" | sed 's/^/  /'
    # A dry run leaves the checkout alone, so everything it checks has to come
    # out of origin/main rather than out of the working tree.
    tmp=$(mktemp -d)
    git -C "$REPO" archive "origin/$BRANCH" "$EXPORTS" "$INFRA" | tar -x -C "$tmp"
    dir="$tmp/$EXPORTS"
    src="$tmp/$INFRA"
  else
    git -C "$REPO" merge --ff-only --quiet "origin/$BRANCH"
    say "fast-forwarded ${before:0:8} to ${after:0:8}:"
    git -C "$REPO" log --oneline --no-decorate "$before..$after" | sed 's/^/  /'
  fi
fi

# ---------------------------------------------------------------------------
# Required values. The compose check further down catches a variable that is
# missing from .env, but an empty one passes it and fails later, inside a
# workflow. These are the ones the stack cannot work without, so an empty value
# stops the run before anything changes. Only names are printed, never values.
# ---------------------------------------------------------------------------
REQUIRED_VALUES=(N8N_HOST APPLY_HOST POSTGRES_PASSWORD N8N_ENCRYPTION_KEY AI_API_KEY SLACK_CHANNEL SMTP_FROM)

env_value() {
  sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$INFRA_DIR/.env" | tail -n 1 | tr -d "\"'" | sed 's/[[:space:]]*$//'
}

# DRY_RUN_RECIPIENT receives every suppressed applicant email, and the pipeline
# suppresses unless DRY_RUN is exactly false, the same test the render nodes use.
if [ "$(env_value DRY_RUN | tr '[:upper:]' '[:lower:]')" != false ]; then
  REQUIRED_VALUES+=(DRY_RUN_RECIPIENT)
fi

empty=()
for name in "${REQUIRED_VALUES[@]}"; do
  [ -n "$(env_value "$name")" ] || empty+=("$name")
done
if [ ${#empty[@]} -gt 0 ]; then
  die "empty or missing in $INFRA_DIR/.env: ${empty[*]}. Set them, then the next run deploys. Nothing was changed"
fi

# ---------------------------------------------------------------------------
# Infra. The marker names the last commit whose infra was applied, so a failed
# apply is retried on the next run instead of being skipped because the
# fast-forward already happened.
# ---------------------------------------------------------------------------

# Every compose call names the live project and the box's real .env, so a dry
# run against files extracted to a temporary directory still describes this
# stack and not a new one.
project=$(docker compose --project-directory "$INFRA_DIR" config --format json \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["name"])')

compose() {
  docker compose -p "$project" --project-directory "$src" \
    -f "$src/docker-compose.yml" --env-file "$INFRA_DIR/.env" "$@"
}

configured_image() {
  compose config --format json | python3 -c '
import json, sys
print(json.load(sys.stdin)["services"][sys.argv[1]]["image"])' "$1"
}

running_image() {
  local id
  id=$(compose ps -q "$1")
  [ -n "$id" ] || return 0
  docker inspect --format '{{.Config.Image}}' "$id"
}

applied=""
if [ -f "$MARKER" ]; then
  applied=$(tr -d '[:space:]' < "$MARKER")
  git -C "$REPO" cat-file -e "${applied}^{commit}" 2>/dev/null || {
    echo "WARNING: the infra marker names ${applied:0:8}, which this checkout does not have. Applying everything" >&2
    applied=""
  }
fi

compose_changed=false
caddy_changed=false
units=()
if [ -z "$applied" ]; then
  say "no infra marker, so this is a full apply"
  compose_changed=true
  caddy_changed=true
  for f in "$src"/systemd/*; do units+=("$(basename "$f")"); done
elif [ "$applied" != "$target" ]; then
  while IFS= read -r path; do
    case "$path" in
      "$INFRA/docker-compose.yml") compose_changed=true ;;
      "$INFRA/Caddyfile") caddy_changed=true ;;
      "$INFRA"/systemd/*)
        if [ -f "$src/systemd/$(basename "$path")" ]; then
          units+=("$(basename "$path")")
        else
          echo "WARNING: $path was deleted in git. Disable and remove it from $UNIT_DIR by hand" >&2
        fi ;;
    esac
  done < <(git -C "$REPO" diff --name-only "$applied" "$target" -- "$INFRA")
fi

if [ "$compose_changed" = true ] || [ "$caddy_changed" = true ]; then
  # Compose only warns about a variable missing from .env and substitutes an
  # empty string, which is how a new variable would reach production empty.
  err=$(mktemp)
  if ! compose config --quiet 2>"$err"; then
    cat "$err" >&2; rm -f "$err"
    die "docker compose config failed. Nothing was changed"
  fi
  unset_count=$(grep -c 'is not set' "$err" || true)
  if [ "$unset_count" -gt 0 ]; then
    cat "$err" >&2; rm -f "$err"
    die "$unset_count variable(s) missing from $INFRA_DIR/.env. Add them, then the next run applies this. Nothing was changed"
  fi
  rm -f "$err"
fi

if [ "$caddy_changed" = true ]; then
  compose run --rm --no-deps -T caddy \
    caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null \
    || die "the Caddyfile does not validate. Nothing was changed"
fi

n8n_upgrade=false
if [ "$compose_changed" = true ]; then
  want=$(configured_image n8n)
  have=$(running_image n8n)
  if [ "$want" != "$have" ]; then
    n8n_upgrade=true
    say "n8n image: running '${have:-none}', configured '$want'"
  fi
fi

if [ "$dry_run" = true ]; then
  [ "$compose_changed" = true ] && say "would run: docker compose up -d"
  [ "$n8n_upgrade" = true ] && say "would back up the database first, because the n8n image changes"
  [ "$caddy_changed" = true ] && say "would recreate caddy"
  for u in ${units[@]+"${units[@]}"}; do say "would install $u"; done
  if [ "$compose_changed" = false ] && [ "$caddy_changed" = false ] && [ ${#units[@]} -eq 0 ]; then
    say "no infra changes"
  fi
else
  if [ "$n8n_upgrade" = true ]; then
    say "backing up before the n8n image changes"
    "$BACKUP" || die "the backup failed, so the n8n image was not changed"
  fi
  if [ "$compose_changed" = true ]; then
    compose up -d
  fi
  if [ "$caddy_changed" = true ]; then
    # Git replaces a file with a new inode, and a single-file bind mount keeps
    # the old one, so only a new container sees the new Caddyfile.
    compose up -d --no-deps --force-recreate caddy
  fi
  if [ ${#units[@]} -gt 0 ]; then
    for u in "${units[@]}"; do
      sudo install -m 644 "$src/systemd/$u" "$UNIT_DIR/$u"
      say "installed $u"
    done
    sudo systemctl daemon-reload
  fi
  mkdir -p "$STATE_DIR"
  echo "$target" > "$MARKER"
  if [ "$compose_changed" = false ] && [ "$caddy_changed" = false ] && [ ${#units[@]} -eq 0 ]; then
    say "no infra changes"
  else
    say "infra applied at ${target:0:8}"
  fi
fi

# ---------------------------------------------------------------------------
# Workflows. The same eight checks CI runs on every pull request. An export
# that fails one is a defect that has reached main before, and it must not
# reach the instance.
# ---------------------------------------------------------------------------
if [ -f "$CHECK" ]; then
  python3 "$CHECK" "$dir"
else
  echo "WARNING: no $CHECK, so the export checks did not run" >&2
fi

python3 "$SYNC" --dir "$dir" ${sync_args[@]+"${sync_args[@]}"}
