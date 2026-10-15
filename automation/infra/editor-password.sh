#!/usr/bin/env bash
# Create the password Caddy asks for before anyone reaches the n8n editor.
#
#   editor-password.sh           create it if it does not exist yet
#   editor-password.sh --rotate  replace it with a new one
#
# Writes two files, both mode 600, and prints neither:
#
#   automation/infra/editor-auth.caddy  the bcrypt hash, which the Caddyfile
#                                       imports. Git ignores it.
#   ~/.n8n-editor-password              the password itself, for you to copy
#                                       into the vault and then delete.
#
# The user name is `ose`. Run it on the box as the login user. A rotation
# takes effect once Caddy restarts: docker compose restart caddy.
set -euo pipefail

REPO="${REPO:-$HOME/community}"
INFRA_DIR="$REPO/automation/infra"
AUTH="$INFRA_DIR/editor-auth.caddy"
PASSWORD_FILE="${EDITOR_PASSWORD_FILE:-$HOME/.n8n-editor-password}"
USER_NAME=ose

die() { echo "ERROR: $*" >&2; exit 1; }

rotate=false
case "${1:-}" in
  "") ;;
  --rotate) rotate=true ;;
  -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) die "unknown option $1" ;;
esac

if [ -f "$AUTH" ] && [ "$rotate" = false ]; then
  [ "$(grep -c -E '^[[:space:]]*basic_auth' "$AUTH" || true)" -gt 0 ] \
    || die "$AUTH exists but holds no basic_auth block. Run with --rotate to replace it"
  echo "the editor password already exists in $AUTH. --rotate replaces it"
  exit 0
fi

image=$(sed -n -E 's/^[[:space:]]*image:[[:space:]]*(caddy:[^[:space:]]+).*/\1/p' "$INFRA_DIR/docker-compose.yml" | head -n 1)
[ -n "$image" ] || die "no caddy image in $INFRA_DIR/docker-compose.yml"

umask 077
password=$(openssl rand -base64 24 | tr -d '\n')
hash=$(sudo docker run --rm "$image" caddy hash-password --plaintext "$password")
case "$hash" in
  \$2*) ;;
  *) die "caddy hash-password returned no bcrypt hash, so nothing was written" ;;
esac

printf '%s\n' "$password" > "$PASSWORD_FILE.new"
printf 'basic_auth {\n\t%s %s\n}\n' "$USER_NAME" "$hash" > "$AUTH.new"
chmod 600 "$PASSWORD_FILE.new" "$AUTH.new"
mv "$PASSWORD_FILE.new" "$PASSWORD_FILE"
mv "$AUTH.new" "$AUTH"
unset password hash

echo "wrote the hash to $AUTH and the password to $PASSWORD_FILE, both mode 600"
echo "copy the password into the vault as 'OSE automation — n8n editor gate', then: rm $PASSWORD_FILE"
if [ "$rotate" = true ]; then
  echo "the new password applies once Caddy restarts: cd $INFRA_DIR && docker compose restart caddy"
fi
