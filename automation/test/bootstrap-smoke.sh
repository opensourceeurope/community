#!/usr/bin/env bash
# Runs automation/infra/bootstrap.sh twice inside a Debian 13 container, as a
# `debian` user with passwordless sudo, the way OVH's image provisions one.
# Packages, the clone, the .env and the unit files are real. systemd, ufw,
# sshd and the Docker engine cannot run in a container, so those are stubs that
# record their calls.
#
# It checks that the first run builds everything, that the second run changes
# nothing, that no generated secret reaches the output, and that the deploy
# timer is only enabled once an API key exists.
#
#   automation/test/bootstrap-smoke.sh
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
IMAGE="${IMAGE:-debian:trixie}"

docker run --rm -i -v "$ROOT/automation:/src/automation:ro" "$IMAGE" bash -s <<'INNER'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null
apt-get install -y -qq sudo git ca-certificates >/dev/null

useradd -m -s /bin/bash debian
echo 'debian ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/debian
groupadd -f docker
groupadd -f systemd-journal
mkdir -p /etc/ssh/sshd_config.d /etc/systemd/system ~debian/.ssh
echo 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAItest test@example.org' > ~debian/.ssh/authorized_keys
chown -R debian: ~debian/.ssh

# Stubs in /usr/local/sbin, which comes first on sudo's secure_path as well.
for cmd in systemctl ufw sshd; do
  printf '#!/bin/sh\necho "%s $*" >> /tmp/calls.log\n' "$cmd" > "/usr/local/sbin/$cmd"
done
printf '#!/bin/sh\necho "ufw $*" >> /tmp/calls.log\n[ "$1" = status ] && [ -f /tmp/ufw-on ] && echo "Status: active"\n[ "$1" = --force ] && touch /tmp/ufw-on\nexit 0\n' > /usr/local/sbin/ufw
printf '#!/bin/sh\necho "docker $*" >> /tmp/calls.log\n' > /usr/local/sbin/docker
chmod 755 /usr/local/sbin/*
touch /tmp/calls.log && chmod 666 /tmp/calls.log

# The repository the box clones: the working tree under test, as one commit.
cp -r /src /tmp/seed
git -C /tmp/seed init -q -b main
git -C /tmp/seed -c user.email=t@example.org -c user.name=t add -A
git -C /tmp/seed -c user.email=t@example.org -c user.name=t commit -q -m seed
chmod -R a+rX /tmp/seed
git config --system --add safe.directory '*'

run() {
  sudo -u debian -H bash /src/automation/infra/bootstrap.sh "$@" \
    --repo-url /tmp/seed 2>&1
}
fail() { echo "FAIL: $*"; exit 1; }
count() { grep -c -E -e "$1" "$2" || true; }

ENV=/home/debian/community/automation/infra/.env

run all --n8n-host admin.example.org --apply-host apply.example.org > /tmp/first.log \
  || { cat /tmp/first.log; fail "the first run failed"; }

[ -f "$ENV" ] || fail "no .env"
[ "$(stat -c %a "$ENV")" = 600 ] || fail ".env is mode $(stat -c %a "$ENV")"
[ "$(count '^N8N_HOST=admin\.example\.org$' "$ENV")" -eq 1 ] || fail "N8N_HOST not written"
[ "$(count '^FORM_URL_OSE=https://apply\.example\.org$' "$ENV")" -eq 1 ] || fail "FORM_URL_OSE not pointed at the apply host"
[ "$(count '^DRY_RUN=true$' "$ENV")" -eq 1 ] || fail "DRY_RUN is not left at true"
pg=$(sed -n 's/^POSTGRES_PASSWORD=//p' "$ENV")
key=$(sed -n 's/^N8N_ENCRYPTION_KEY=//p' "$ENV")
[ "${#pg}" -eq 48 ] || fail "POSTGRES_PASSWORD is ${#pg} characters"
[ "${#key}" -eq 64 ] || fail "N8N_ENCRYPTION_KEY is ${#key} characters"
[ "$(count "$pg" /tmp/first.log)" -eq 0 ] || fail "the Postgres password reached the output"
[ "$(count "$key" /tmp/first.log)" -eq 0 ] || fail "the encryption key reached the output"

for p in git ufw unattended-upgrades curl python3 age; do
  dpkg-query -W -f='${Status}' "$p" | grep -c 'install ok installed' >/dev/null || fail "$p not installed"
done
[ "$(id -nG debian | tr ' ' '\n' | grep -c -x -e docker -e systemd-journal)" -eq 2 ] || fail "debian is not in docker and systemd-journal"
[ -f /etc/ssh/sshd_config.d/99-ose-hardening.conf ] || fail "no SSH hardening drop-in"
[ "$(count 'origin=Docker' /etc/apt/apt.conf.d/52ose-unattended-upgrades)" -eq 1 ] || fail "unattended-upgrades does not cover Docker's packages"
[ "$(count 'Automatic-Reboot "true"' /etc/apt/apt.conf.d/52ose-unattended-upgrades)" -eq 1 ] || fail "no automatic reboot for kernel updates"
[ "$(count 'Unattended-Upgrade "1"' /etc/apt/apt.conf.d/20auto-upgrades)" -eq 1 ] || fail "unattended-upgrades is not switched on"
for u in ose-deploy.service ose-deploy.timer ose-backup.service ose-backup.timer ose-alert@.service; do
  [ -f "/etc/systemd/system/$u" ] || fail "$u not installed"
done
[ "$(count 'compose up -d' /tmp/calls.log)" -eq 1 ] || fail "the stack was not brought up"
[ "$(count 'enable --now ose-backup.timer' /tmp/calls.log)" -eq 1 ] || fail "the backup timer was not enabled"
[ "$(count 'enable --now ose-env-drift.timer' /tmp/calls.log)" -eq 1 ] || fail "the .env drift timer was not enabled"
[ "$(count 'enable --now ose-deploy.timer' /tmp/calls.log)" -eq 0 ] || fail "the deploy timer was enabled without an API key"
[ "$(cat /home/debian/.local/state/ose-deploy/infra-applied)" = "$(git -C /home/debian/community rev-parse HEAD)" ] \
  || fail "the infra marker does not name the deployed commit"
[ "$(count 'What is left' /tmp/first.log)" -eq 1 ] || fail "no list of the manual steps"

sum=$(sha256sum "$ENV")
: > /tmp/calls.log
run all --n8n-host other.example.org --apply-host other.example.org > /tmp/second.log \
  || { cat /tmp/second.log; fail "the second run failed"; }
[ "$(sha256sum "$ENV")" = "$sum" ] || fail "the second run changed .env"
[ "$(count 'left exactly as it is' /tmp/second.log)" -eq 1 ] || fail "the second run did not keep .env"
[ "$(count '^installed: ' /tmp/second.log)" -eq 0 ] || fail "the second run installed packages again"
[ "$(count 'ufw --force enable' /tmp/calls.log)" -eq 0 ] || fail "the second run enabled ufw again"
[ "$(count 'reload ssh' /tmp/calls.log)" -eq 0 ] || fail "the second run reloaded sshd again"
[ "$(count 'automatic security updates already configured' /tmp/second.log)" -eq 1 ] || fail "the second run rewrote the update settings"

: > /tmp/calls.log
sudo -u debian sh -c 'umask 077; echo key > /home/debian/.n8n-api-key'
sudo -u debian -H bash /src/automation/infra/bootstrap.sh units > /tmp/units.log 2>&1 \
  || { cat /tmp/units.log; fail "units failed"; }
[ "$(count 'enable --now ose-deploy.timer' /tmp/calls.log)" -eq 1 ] || fail "units did not enable the deploy timer once the key existed"
[ "$(count 'compose' /tmp/calls.log)" -eq 0 ] || fail "units touched the stack"

echo "bootstrap-smoke: all checks passed"
INNER
