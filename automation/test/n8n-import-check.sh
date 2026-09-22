#!/usr/bin/env bash
#
# Boot n8n in Docker, import every export in automation/n8n into it, and run
# the invariant check over the same files. One command, the same one locally
# and in CI:
#
#   automation/test/n8n-import-check.sh
#
# The image tag comes out of automation/infra/docker-compose.yml, so the check
# always runs the n8n build that production runs. With a second copy of the tag
# here, the check would go on passing after an upgrade moved the real one.
#
# Nothing here reaches automation.opensourceeurope.org. The container is new
# every run, keeps its state in SQLite rather than the production Postgres,
# holds no credential, and is removed when the script exits.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
COMPOSE="$ROOT/automation/infra/docker-compose.yml"
EXPORTS="$ROOT/automation/n8n"
CONTAINER="ose-n8n-import-check-$$"

# Public on purpose. It encrypts the credentials of a container that is deleted
# seconds later, and this check never puts one there.
THROWAWAY_KEY="ose-import-check-throwaway-encryption-key"

# n8n runs its database migrations at startup, so the first boot of a new image
# tag is much slower than the ones after it.
READY_TRIES=120
READY_SLEEP=2

WORK=$(mktemp -d)
STARTED=""

cleanup() {
  status=$?
  if [ -n "$STARTED" ]; then
    if [ "$status" -ne 0 ]; then
      echo
      echo "==> last 40 lines of the n8n container log"
      docker logs "$CONTAINER" 2>&1 | tail -40 || true
    fi
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT

# --- the invariants, which need neither Docker nor a network -----------------

echo "==> automation/scripts/check-workflows.py"
python3 "$ROOT/automation/scripts/check-workflows.py"

# --- the image tag production runs -------------------------------------------

PIN='^[[:space:]]*image:[[:space:]]*docker\.n8n\.io/n8nio/n8n:'
matches=$(grep -c -E "$PIN" "$COMPOSE" || true)
if [ "$matches" != "1" ]; then
  cat >&2 <<EOF

FAIL: cannot read the n8n image tag out of the production compose file.

  file:    $COMPOSE
  looking: image: docker.n8n.io/n8nio/n8n:<tag>
  found:   ${matches:-0} line(s), expected exactly 1

This check boots the tag production runs so that the two can never drift
apart. Point it at the new line rather than writing a tag into
automation/test/n8n-import-check.sh.
EOF
  exit 1
fi

IMAGE=$(grep -E "$PIN" "$COMPOSE" | sed -E 's/^[[:space:]]*image:[[:space:]]*//')
TAG=${IMAGE##*:}
case "$TAG" in
  [0-9]*.[0-9]*.[0-9]*) ;;
  *)
    cat >&2 <<EOF

FAIL: the n8n image in the production compose file is not pinned to a release.

  file:  $COMPOSE
  image: $IMAGE
  tag:   $TAG

A floating tag gives this check nothing to reproduce. Pin the compose file to
an x.y.z release.
EOF
    exit 1
    ;;
esac

echo
echo "==> $IMAGE (from automation/infra/docker-compose.yml)"
docker pull "$IMAGE"

# --- a throwaway instance -----------------------------------------------------

echo
echo "==> starting $CONTAINER"
docker run -d --name "$CONTAINER" \
  -p 127.0.0.1:0:5678 \
  -e DB_TYPE=sqlite \
  -e N8N_ENCRYPTION_KEY="$THROWAWAY_KEY" \
  -e GENERIC_TIMEZONE=Europe/Berlin \
  -e N8N_BLOCK_ENV_ACCESS_IN_NODE=false \
  -e N8N_DIAGNOSTICS_ENABLED=false \
  -e N8N_VERSION_NOTIFICATIONS_ENABLED=false \
  -e N8N_TEMPLATES_ENABLED=false \
  "$IMAGE" >/dev/null
STARTED=1

PORT=$(docker port "$CONTAINER" 5678/tcp | awk -F: 'NR == 1 { print $NF }')
ready=""
i=0
while [ "$i" -lt "$READY_TRIES" ]; do
  if curl -fsS -o /dev/null "http://127.0.0.1:$PORT/healthz" 2>/dev/null; then
    ready=1
    break
  fi
  i=$((i + 1))
  sleep "$READY_SLEEP"
done
if [ -z "$ready" ]; then
  echo "FAIL: n8n $TAG did not answer /healthz within $((READY_TRIES * READY_SLEEP))s" >&2
  exit 1
fi
echo "    healthy"

# --- import the exports, one at a time so a failure names the file ------------

echo
echo "==> importing automation/n8n"
for file in "$EXPORTS"/*.json; do
  name=$(basename "$file")
  docker exec "$CONTAINER" sh -c 'rm -rf /tmp/one && mkdir -p /tmp/one'
  docker cp "$file" "$CONTAINER:/tmp/one/$name" >/dev/null

  if ! output=$(docker exec "$CONTAINER" n8n import:workflow --separate --input=/tmp/one 2>&1); then
    printf '%s\n' "$output" >&2
    echo "FAIL: $name: n8n $TAG rejected this export" >&2
    exit 1
  fi

  # n8n reports a file it could not read as "Skipping invalid workflow file"
  # and still exits 0, so the count is what says the export went in.
  accepted=$(printf '%s\n' "$output" | grep -c 'Successfully imported 1 workflow' || true)
  if [ "$accepted" != "1" ]; then
    printf '%s\n' "$output" >&2
    echo "FAIL: $name: n8n $TAG read this export but imported no workflow from it" >&2
    exit 1
  fi
  echo "    $name"
done

# --- what the instance made of them -------------------------------------------

docker exec "$CONTAINER" n8n list:workflow >"$WORK/listing.txt"
docker cp "$ROOT/automation/test/n8n-node-registry.js" \
  "$CONTAINER:/tmp/n8n-node-registry.js" >/dev/null
docker exec "$CONTAINER" node /tmp/n8n-node-registry.js >"$WORK/registry.json"

echo
echo "==> automation/test/check-import.py"
python3 "$ROOT/automation/test/check-import.py" \
  --registry "$WORK/registry.json" \
  --listing "$WORK/listing.txt" \
  "$EXPORTS"

echo
echo "OK: the exports load on n8n $TAG"
