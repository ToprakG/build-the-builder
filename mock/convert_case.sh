#!/bin/bash
# The 20:00 command: case folder -> mock/spec.<id>.json -> --check.   usage: mock/convert_case.sh <case_folder> [id=freyabank]
#   contract files = BRIEF.md, README.md first, then every other .md/.docx/.pdf in the folder (service contract, announcements, Q&A; example conversations are skipped)
#   customers      = CUSTOMERS=<file> if set, else the only .xlsx/.csv in the folder (several -> it stops and asks for CUSTOMERS=)
#   openapi        = OPENAPI=<file> if set, else the first *openapi*.json|yaml in the folder, else derived from the contract by the LLM
cd "$(dirname "$0")/.." || exit 1
F=${1:?case folder}; ID=${2:-freyabank}; PY=${PY:-.venv310/bin/python}
C=(); for n in BRIEF.md README.md; do [ -f "$F/$n" ] && C+=("$F/$n"); done
while IFS= read -r f; do case "$(basename "$f" | tr '[:upper:]' '[:lower:]')" in brief.md|readme.md|*rnek*|*example*|*conversation*|*gorusme*|*görüşme*|*dialog*) ;; *) C+=("$f");; esac
done < <(find "$F" -maxdepth 2 -type f \( -name '*.md' -o -name '*.docx' -o -name '*.pdf' \) ! -name '~*' | sort)
if [ -z "$CUSTOMERS" ]; then K=$(find "$F" -maxdepth 2 -type f \( -name '*.xlsx' -o -name '*.csv' \) ! -name '~*' | sort); [ "$(echo "$K" | grep -c .)" = 1 ] && CUSTOMERS=$K || { echo "customers file: found '$K' - set CUSTOMERS=<file>"; exit 2; }; fi
O=${OPENAPI:-$(find "$F" -maxdepth 2 -type f \( -iname '*openapi*.json' -o -iname '*openapi*.yaml' \) | sort | head -1)}
echo "contract: ${C[*]}"; echo "customers: $CUSTOMERS"; echo "openapi: ${O:-<derived by the LLM>}"
$PY mock/convert.py --id "$ID" --name "Freya Bank" --contract "${C[@]}" ${O:+--openapi "$O"} --customers "$CUSTOMERS" --out mock/spec.$ID.json || exit 1
$PY mock/convert.py --check mock/spec.$ID.json
