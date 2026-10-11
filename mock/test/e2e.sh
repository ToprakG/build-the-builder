#!/bin/sh
# usage: test/e2e.sh <base-url> [admin-token]   — hits every endpoint, then checks the ledger
SPEC=${SPEC:-$(cd $(dirname $0)/.. && pwd)/specs/spec.brief-a.json}; B=${1:-http://localhost:8799}/${TENANT:-$(node -p "require('$SPEC').id")}; T=$2; fail=0
C=$(node -p "require('$SPEC').customers[0].customer_id"); DOB=$(node -p "require('$SPEC').customers[0].identity.date_of_birth"); PH=$(node -p "require('$SPEC').customers[0].identity.phone_last4")
C2=$(node -p "const c=require('$SPEC').customers;(c[1]||c[0]).customer_id")
post(){ body=$(printf '%s' "$2" | sed "s/CID/$C/; s/\$DOB/$DOB/; s/\$PH/$PH/"); curl -s -X POST "$B/v1/$1" -H 'content-type: application/json' -d "$body"; echo; }
chk(){ echo "$2" | grep -q "$3" && echo "ok   $1" || { echo "FAIL $1: $2"; fail=1; }; }
[ -n "$T" ] && chk put-spec "$(curl -s -X PUT -H "Authorization: Bearer $T" --data-binary @$SPEC $B/admin/spec)" '"ok":true'
[ -n "$T" ] && curl -s -X POST -H "Authorization: Bearer $T" "$B/admin/reset" >/dev/null
chk openapi-server "$(curl -s $B/openapi.json)" "\"url\":\"$B/v1\""
chk openapi "$(curl -s $B/openapi.json)" '"/verify_identity"'
chk pre-verify "$(post get_account '{"customer_id":"CID"}')" 'identity not verified'
chk dob "$(post verify_identity '{"customer_id":"CID","field":"date_of_birth","answer":"$DOB"}')" '"verified":true'
chk phone "$(post verify_identity '{"customer_id":"CID","field":"phone_last4","answer":"$PH"}')" '"all_verified":true'
chk account "$(post get_account '{"customer_id":"CID"}')" '"product_id":"p1"'
chk promise "$(post create_promise '{"customer_id":"CID","product_id":"p1","amount":126,"date":"2026-10-20"}')" 'promise_id'
chk dup-promise "$(post create_promise '{"customer_id":"CID","product_id":"p1","amount":126,"date":"2026-10-20"}')" '"ok":false'
chk note "$(post add_note '{"customer_id":"CID","product_id":"p1","reason":"hardship"}')" '"ok":true'
chk callback "$(post schedule_callback '{"customer_id":"CID","date":"2026-10-21","time":"10:00"}')" '"ok":true'
chk transfer "$(post transfer_to_agent '{"customer_id":"CID","queue":"general"}')" '"ok":true'
chk close "$(post close_call '{"customer_id":"CID","outcome":"promise"}')" '"ok":true'
chk after-close "$(post get_account '{"customer_id":"CID"}')" 'already closed'
chk ledger "$(curl -s $B/v1/practice/ledger/$C)" '"outcome":"promise"'
chk records "$(curl -s $B/records?customer_id=$C)" '"tool":"close_call"'
chk state "$(curl -s $B/state)" '"closed":true'
chk 500 "$(curl -s -o /dev/null -w '%{http_code}' -X POST $B/v1/get_account -H 'x-mock-fail: 1' -d "{\"customer_id\":\"$C2\"}")" '500'
[ -n "$T" ] && chk reset "$(curl -s -X POST -H "Authorization: Bearer $T" "$B/admin/reset?customer_id=$C")" '"ok":true' && chk after-reset "$(curl -s $B/v1/practice/ledger/$C)" '"outcome":null'
chk admin-forbidden "$(curl -s -X POST "$B/admin/reset")" 'forbidden'
exit $fail
