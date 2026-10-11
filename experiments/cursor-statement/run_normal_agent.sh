#!/bin/bash
# 30-minute Cursor agent. Banking phone-agent builder. The session log is the trace.
# Usage: run_normal_agent.sh <run-id>
set -u
if [ $# -lt 1 ]; then
  echo "usage: run_normal_agent.sh RUN_ID" >&2
  exit 2
fi
RUN_ID="$1"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CASE="${CASE_FOLDER:-/Users/toprakgundogdu/Downloads/test-environment}"
MOCK="${MOCK_URL:-https://statement-mock.guntop-mockbank.workers.dev}"
export PATH="${HOME}/.local/bin:${PATH}"
OUT_REL="experiments/cursor-statement/out/run-${RUN_ID}"
TRACE="${ROOT}/experiments/cursor-statement/trace/normal-${RUN_ID}.jsonl"
ERR="${ROOT}/experiments/cursor-statement/trace/normal-${RUN_ID}.err"
mkdir -p "${ROOT}/${OUT_REL}" "${ROOT}/experiments/cursor-statement/trace"
cd "${ROOT}"
printf '%s\n' "${TRACE}" > "${ROOT}/experiments/cursor-statement/trace/latest-normal-${RUN_ID}.txt"
PROMPT_FILE="${ROOT}/experiments/cursor-statement/trace/prompt-${RUN_ID}.txt"
cat > "${PROMPT_FILE}" << ENDPROMPT
You have 30 minutes. You are a banking phone-agent builder. Build the phone agent.

Follow the skills in experiments/cursor-statement/.cursor/skills, in order: pack-inventory, speech-library, workflow-graph, side-exits, adversarial-checklist. The skills apply to any banking phone agent. The case documents decide the product.

Case folder: ${CASE}
Mock that stays up: ${MOCK}
Tool URLs must use that host.

Write this run files under ${OUT_REL}. You may create personas and tests. Publish to Freya slot cursor-bank-${RUN_ID}. Source ${ROOT}/.env before any Freya call. Do not print secrets. If a skill names an mcp__fb__ tool that is not in this session, do that check in code.

Export with freya.export_workflow to ${OUT_REL}/final.workflow.json. Stop at 30 minutes with the best version published.
ENDPROMPT
perl -e 'alarm 1800; exec @ARGV or die $!' \
  agent -p --force --trust --sandbox disabled --approve-mcps \
  --workspace "${ROOT}" \
  --add-dir "${CASE}" \
  --output-format stream-json \
  "$(cat "${PROMPT_FILE}")" > "${TRACE}" 2>"${ERR}"
echo "trace ${TRACE}"
