#!/bin/sh
# Load a converter spec into the deployed bank mock as <tenant>, check it end to end, leave the ledger clean, print the mock_url.
#   scripts/mock_load_case.sh <spec.json> <tenant>          (needs MOCK_ADMIN_TOKEN in .env; MOCK_HOST overrides the Worker host)
set -e
[ $# -eq 2 ] || { echo "usage: $0 <spec.json> <tenant>" >&2; exit 2; }
SPEC=$(cd "$(dirname "$1")" && pwd)/$(basename "$1"); TENANT=$2
ROOT=$(cd "$(dirname "$0")/.." && pwd)
[ -f "$ROOT/.env" ] && { set -a; . "$ROOT/.env"; set +a; }
[ -n "$MOCK_ADMIN_TOKEN" ] || { echo "MOCK_ADMIN_TOKEN missing (.env)" >&2; exit 2; }
HOST=${MOCK_HOST:-https://bank-mock.tccbank2026-yc.workers.dev}; URL=$HOST/$TENANT
case "$TENANT" in *[!a-z0-9-]*|"") echo "tenant must be [a-z0-9-]" >&2; exit 2;; esac
A="Authorization: Bearer $MOCK_ADMIN_TOKEN"
r=$(curl -s -X PUT -H "$A" --data-binary @"$SPEC" "$URL/admin/spec"); echo "put spec: $r"; echo "$r" | grep -q '"ok":true' || exit 1
export SPEC TENANT
"$ROOT/mock/test/e2e.sh" "$HOST" "$MOCK_ADMIN_TOKEN"        # also resets the tenant at its end
r=$(curl -s -X POST -H "$A" "$URL/admin/reset"); echo "final reset: $r"
echo "openapi servers: $(curl -s "$URL/openapi.json" | python3 -c "import json,sys;print(json.load(sys.stdin)['servers'])")"
echo "mock_url=$URL"
