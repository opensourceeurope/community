#!/usr/bin/env bash
# Runs the production Caddyfile in the production Caddy image, in front of a
# stub that stands in for n8n and answers with the path it was asked for. Then
# checks, path by path, what each hostname lets through: the applicant host
# only the form, the admin host the machine endpoints openly and everything
# else only with the editor password.
#
# Needs Docker. Nothing here reaches the box.
#
#   automation/test/caddy-routes.sh
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
INFRA=$(cd "$HERE/../infra" && pwd)
IMAGE=$(sed -n -E 's/^[[:space:]]*image:[[:space:]]*(caddy:[^[:space:]]+).*/\1/p' "$INFRA/docker-compose.yml" | head -n 1)
[ -n "$IMAGE" ] || { echo "FAIL: no caddy image in docker-compose.yml"; exit 1; }

RUN="ose-caddy-routes-$$"
WORK=$(mktemp -d)
cleanup() {
  docker rm -f "$RUN-proxy" "$RUN-n8n" >/dev/null 2>&1 || true
  docker network rm "$RUN" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

PASSWORD=route-test-password
docker network create "$RUN" >/dev/null

# The stand-in for n8n echoes the request line, so a test can see both that the
# request got through and what the rewrite turned it into.
printf ':5678 {\n\trespond "upstream {method} {uri}" 200\n}\n' > "$WORK/upstream.caddy"
docker run -d --name "$RUN-n8n" --network "$RUN" --network-alias n8n \
  -v "$WORK/upstream.caddy:/etc/caddy/Caddyfile:ro" "$IMAGE" >/dev/null

hash=$(docker run --rm "$IMAGE" caddy hash-password --plaintext "$PASSWORD")
printf 'basic_auth {\n\tose %s\n}\n' "$hash" > "$WORK/editor-auth.caddy"

docker run -d --name "$RUN-proxy" --network "$RUN" -p 127.0.0.1::443 \
  -e APPLY_HOST=apply.localhost -e N8N_HOST=admin.localhost \
  -v "$INFRA/Caddyfile:/etc/caddy/Caddyfile:ro" \
  -v "$WORK/editor-auth.caddy:/etc/caddy/editor-auth.caddy:ro" \
  -v "$INFRA/public:/srv/public:ro" "$IMAGE" >/dev/null
PORT=$(docker port "$RUN-proxy" 443/tcp | head -n 1 | sed 's/.*://')

failures=0
fail() { echo "FAIL: $*"; failures=$((failures + 1)); }

# fetch <host> <method> <path> [curl args...]: prints "<status> <body>"
fetch() {
  local host=$1 method=$2 path=$3
  shift 3
  curl -sk --max-time 10 -X "$method" --resolve "$host.localhost:$PORT:127.0.0.1" \
    -o "$WORK/body" -w '%{http_code}' "$@" "https://$host.localhost:$PORT$path" > "$WORK/code" || true
  printf '%s %s' "$(cat "$WORK/code")" "$(cat "$WORK/body" 2>/dev/null)"
}

# Caddy issues its internal certificates on the first start, which takes a
# moment, so wait for the applicant host to answer at all.
for _ in $(seq 1 30); do
  [ "$(fetch apply GET /form/apply-ose | cut -c1-3)" = 200 ] && break
  sleep 1
done

# expect <host> <method> <path> <status> [<body prefix>] [-- curl args...]
expect() {
  local host=$1 method=$2 path=$3 want=$4 body=${5:-}
  shift 4
  [ $# -gt 0 ] && shift
  [ "${1:-}" = -- ] && shift
  local got
  got=$(fetch "$host" "$method" "$path" "$@")
  if [ "${got%% *}" != "$want" ]; then
    fail "$host $method $path answered ${got%% *}, wanted $want"
  elif [ -n "$body" ] && [[ "${got#* }" != "$body"* ]]; then
    fail "$host $method $path reached upstream as '${got#* }', wanted '$body'"
  else
    echo "ok   $host $method $path -> $want"
  fi
}

AUTH=(-u "ose:$PASSWORD")

echo "== the applicant host serves the form and nothing else"
expect apply GET / 200 "upstream GET /form/apply-ose"
expect apply GET "/?Your%20collective=x" 200 "upstream GET /form/apply-ose?Your%20collective=x"
expect apply POST "/?a=1" 200 "upstream POST /form/apply-ose?a=1"
expect apply GET /form/apply-ose 200 "upstream GET /form/apply-ose"
expect apply GET "/form-waiting/42?signature=s" 200 "upstream GET /form-waiting/42?signature=s"
expect apply GET /form-waiting/42/n8n-execution-status 200 "upstream GET /form-waiting/42/n8n-execution-status"
expect apply GET /static/og_image.png 200
for p in /signin /setup /rest/settings /rest/login /api/v1/workflows /api/v1/docs/ \
  /webhook/x /webhook-test/x /healthz /assets/index.js /home/workflows /form-test/apply-ose; do
  expect apply GET "$p" 404
done

echo "== the admin host leaves machine endpoints open"
expect admin POST /webhook/oc-events 200 "upstream POST /webhook/oc-events"
expect admin GET /api/v1/workflows 200 "upstream GET /api/v1/workflows"
expect admin GET /healthz 200 "upstream GET /healthz"
expect admin GET "/form-waiting/42?signature=s" 200 "upstream GET /form-waiting/42"
expect admin GET /form/apply-ose 200 "upstream GET /form/apply-ose"

echo "== and asks for the editor password everywhere else"
for p in / /signin /setup /rest/settings /rest/login /rest/push /assets/index.js \
  /webhook-test/x /home/workflows /api/v1 /apiv1 /webhooks /healthz/x; do
  expect admin GET "$p" 401
done
expect admin GET / 401 "" -- -u "ose:wrong-password"
expect admin GET /signin 200 "upstream GET /signin" -- "${AUTH[@]}"
expect admin POST /rest/login 200 "upstream POST /rest/login" -- "${AUTH[@]}"
expect admin GET /rest/push 200 "upstream GET /rest/push" -- "${AUTH[@]}"

if [ "$failures" -gt 0 ]; then
  echo "caddy-routes: $failures failure(s)"
  docker logs "$RUN-proxy" 2>&1 | tail -n 20
  exit 1
fi
echo "caddy-routes: all checks passed"
