#!/usr/bin/env bash
# Turn a fresh Debian 13 VPS into this stack, or bring an existing box's
# systemd timers up to date. Every step checks first and does nothing when its
# work is already done, so running it twice changes nothing the second time.
#
#   bootstrap.sh all --n8n-host <name> --apply-host <name> [--repo-url <url>] [--branch <name>]
#   bootstrap.sh units
#
# `all` installs everything, from packages to a running stack. `units` only
# installs what the timers need: their packages, the journal group and the
# unit files. It is what an existing box runs.
#
# Run it as the login user, with passwordless sudo, never as root. It never
# prints a secret, and it never touches an existing .env.
set -euo pipefail

REPO="${REPO:-$HOME/community}"
REPO_URL="https://github.com/opensourceeurope/community.git"
BRANCH="main"
N8N_HOST=""
APPLY_HOST=""

die() { echo "ERROR: $*" >&2; exit 1; }
step() { printf '\n== %s\n' "$*"; }
note() { echo "   $*"; }

mode="${1:-}"
[ $# -gt 0 ] && shift
while [ $# -gt 0 ]; do
  case "$1" in
    --n8n-host) N8N_HOST="${2:-}"; shift ;;
    --apply-host) APPLY_HOST="${2:-}"; shift ;;
    --repo-url) REPO_URL="${2:-}"; shift ;;
    --branch) BRANCH="${2:-}"; shift ;;
    -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option $1" ;;
  esac
  shift
done
case "$mode" in
  all|units) ;;
  *) die "usage: bootstrap.sh all --n8n-host <name> --apply-host <name> | bootstrap.sh units" ;;
esac

[ "$(id -u)" -ne 0 ] || die "run this as the login user, not root. It uses sudo where it needs to"
sudo -n true 2>/dev/null || die "this needs passwordless sudo for $(id -un)"

INFRA_DIR="$REPO/automation/infra"

installed() {
  [ "$(dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -c 'install ok installed' || true)" -gt 0 ]
}

packages() {
  local missing=()
  for p in "$@"; do installed "$p" || missing+=("$p"); done
  if [ ${#missing[@]} -eq 0 ]; then
    note "already installed: $*"
    return
  fi
  sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${missing[@]}"
  note "installed: ${missing[*]}"
}

join_group() {
  if [ "$(id -nG "$(id -un)" | tr ' ' '\n' | grep -c -x "$1" || true)" -gt 0 ]; then
    note "$(id -un) is already in $1"
  else
    sudo usermod -aG "$1" "$(id -un)"
    note "added $(id -un) to $1. It applies from the next login"
  fi
}

firewall() {
  for port in 22 80 443; do sudo ufw allow "$port/tcp" >/dev/null; done
  if [ "$(sudo ufw status | grep -c 'Status: active' || true)" -gt 0 ]; then
    note "ufw already active, 22, 80 and 443 allowed"
  else
    sudo ufw --force enable >/dev/null
    note "ufw enabled with 22, 80 and 443 allowed"
  fi
}

ssh_hardening() {
  local keys dropin=/etc/ssh/sshd_config.d/99-ose-hardening.conf
  keys=$(grep -c -E '^(ssh-|ecdsa-|sk-)' "$HOME/.ssh/authorized_keys" 2>/dev/null || true)
  if [ "${keys:-0}" -eq 0 ]; then
    echo "WARNING: no key in ~/.ssh/authorized_keys, so password login stays on. Turning it off now would lock you out" >&2
    return
  fi
  local want
  want=$(printf '%s\n' 'PasswordAuthentication no' 'KbdInteractiveAuthentication no' 'PermitRootLogin prohibit-password')
  if [ "$(sudo cat "$dropin" 2>/dev/null || true)" = "$want" ]; then
    note "key-only SSH already in place"
    return
  fi
  printf '%s\n' "$want" | sudo tee "$dropin" >/dev/null
  sudo sshd -t || { sudo rm -f "$dropin"; die "sshd rejected the hardening drop-in, so it was removed"; }
  sudo systemctl reload ssh
  note "key-only SSH enabled"
}

auto_updates() {
  # The settings live in automation/infra/apt/, and deploy.sh installs them
  # again whenever they change in git. See the comments in those files.
  local f changed=0
  for f in "$INFRA_DIR"/apt/*; do
    if sudo cmp -s "$f" "/etc/apt/apt.conf.d/$(basename "$f")"; then
      continue
    fi
    sudo install -m 644 "$f" "/etc/apt/apt.conf.d/$(basename "$f")"
    changed=1
  done
  if [ "$changed" -eq 0 ]; then
    note "automatic security updates already configured"
  else
    note "daily security updates on, Docker's packages included, reboot at 04:30 when one is needed"
  fi
}

docker_engine() {
  if command -v docker >/dev/null; then
    note "docker already installed"
  else
    curl -fsSL https://get.docker.com | sudo sh
  fi
  join_group docker
}

checkout() {
  if [ -d "$REPO/.git" ]; then
    note "checkout already at $REPO"
  else
    git clone --quiet --branch "$BRANCH" "$REPO_URL" "$REPO"
    note "cloned $REPO_URL ($BRANCH) into $REPO"
  fi
}

env_file() {
  local env="$INFRA_DIR/.env"
  if [ -f "$env" ]; then
    note "$env exists and is left exactly as it is"
    return
  fi
  if [ -z "$N8N_HOST" ] || [ -z "$APPLY_HOST" ]; then
    die "a new .env needs --n8n-host and --apply-host"
  fi
  # Generated here, on the box, so the secrets never travel. umask keeps the
  # file unreadable to others from the moment it exists.
  (
    umask 077
    cp "$REPO/automation/.env.example" "$env"
    sed -i "s|^FORM_URL_OSE=.*|FORM_URL_OSE=https://$APPLY_HOST|" "$env"
    {
      echo
      echo "# --- infra (written by bootstrap.sh) ---"
      echo "N8N_HOST=$N8N_HOST"
      echo "APPLY_HOST=$APPLY_HOST"
      echo "EXECUTIONS_DATA_PRUNE=true"
      echo "EXECUTIONS_DATA_MAX_AGE=336"
      echo "POSTGRES_PASSWORD=$(openssl rand -hex 24)"
      echo "N8N_ENCRYPTION_KEY=$(openssl rand -hex 32)"
    } >> "$env"
  )
  chmod 600 "$env"
  note "wrote $env with generated secrets. DRY_RUN stays at its safe default"
}

stack() {
  local err count
  err=$(mktemp)
  ( cd "$INFRA_DIR" && sudo docker compose config --quiet ) 2>"$err" || { cat "$err" >&2; die "docker compose config failed"; }
  count=$(grep -c 'is not set' "$err" || true)
  if [ "$count" -gt 0 ]; then
    cat "$err" >&2
    die "$count variable(s) missing from $INFRA_DIR/.env"
  fi
  rm -f "$err"
  ( cd "$INFRA_DIR" && sudo docker compose up -d )
  # The stack now runs exactly this commit, so the first deploy has nothing
  # to re-apply.
  mkdir -p "$HOME/.local/state/ose-deploy"
  git -C "$REPO" rev-parse HEAD > "$HOME/.local/state/ose-deploy/infra-applied"
  note "stack up at $(git -C "$REPO" rev-parse --short HEAD)"
}

units() {
  # The unit files run as debian out of /home/debian/community. Another user or
  # path needs those lines changed in git first, not only on this box.
  if [ "$(id -un)" != debian ] || [ "$REPO" != /home/debian/community ]; then
    die "the units in $INFRA_DIR/systemd run as debian from /home/debian/community. Change them in git for $(id -un) at $REPO first"
  fi
  for f in "$INFRA_DIR"/systemd/*; do
    sudo install -m 644 "$f" "/etc/systemd/system/$(basename "$f")"
  done
  sudo systemctl daemon-reload
  note "installed: $(cd "$INFRA_DIR/systemd" && echo *)"

  # The old backup unit had systemd write this log, so it belongs to root, and
  # backup.sh, which now writes it itself, could not append to it.
  if [ -e "$HOME/backups/backup.log" ] && [ ! -w "$HOME/backups/backup.log" ]; then
    sudo chown "$(id -un):$(id -gn)" "$HOME/backups/backup.log"
    note "gave $(id -un) the backup log the old unit left behind"
  fi

  sudo systemctl enable --now ose-backup.timer >/dev/null 2>&1
  note "ose-backup.timer enabled"
  sudo systemctl enable --now ose-n8n-advisories.timer >/dev/null 2>&1
  note "ose-n8n-advisories.timer enabled"
  sudo systemctl enable --now ose-env-drift.timer >/dev/null 2>&1
  note "ose-env-drift.timer enabled"
  if [ -f "$HOME/.n8n-api-key" ]; then
    sudo systemctl enable --now ose-deploy.timer >/dev/null 2>&1
    note "ose-deploy.timer enabled"
  else
    note "ose-deploy.timer NOT enabled: there is no ~/.n8n-api-key yet, and every run would fail"
  fi
  [ -f "$HOME/.slack-bot-token" ] || note "no ~/.slack-bot-token yet, so a failure cannot alert Slack"
  [ -f "$HOME/.config/ose-backup/backup.env" ] || note "no ~/.config/ose-backup/backup.env yet, so backups stay on this box"
}

next_steps() {
  cat <<EOF

== What is left, by hand, in this order (each is a section of automation/infra/README.md)

  1. Copy N8N_ENCRYPTION_KEY from $INFRA_DIR/.env into the password vault.
     Losing it makes every stored credential unrecoverable.
  2. Open https://$N8N_HOST and create the owner account.
  3. Create an n8n API key with workflow:list, workflow:read, workflow:update,
     dataTable:read and dataTableColumn:create, into ~/.n8n-api-key, mode 600. Then: bootstrap.sh units
  4. Write the Slack bot token into ~/.slack-bot-token, mode 600, and set
     SLACK_CHANNEL in .env.
  5. Write the backup bucket settings into ~/.config/ose-backup/backup.env, mode 600.
  6. Create the n8n credentials: oc-host-admin, slack-bot, smtp-proton. Set
     AI_API_KEY, SMTP_FROM and DRY_RUN_RECIPIENT in .env. The deploy refuses
     to run while any required value is empty.
  7. First workflow deploy: $INFRA_DIR/deploy.sh --allow-create
  8. Register the Open Collective webhooks.
EOF
}

if [ "$mode" = all ]; then
  step "packages"
  packages git ufw unattended-upgrades ca-certificates curl openssl python3 age
  step "firewall"
  firewall
  step "ssh"
  ssh_hardening
  step "docker"
  docker_engine
  step "checkout"
  checkout
  step "automatic security updates"
  auto_updates
  step "config"
  env_file
  step "stack"
  stack
else
  step "packages"
  packages git curl python3 age unattended-upgrades
  step "automatic security updates"
  auto_updates
fi

step "journal access, for the failure alert"
join_group systemd-journal
step "systemd units"
units

if [ "$mode" = all ]; then
  next_steps
fi
