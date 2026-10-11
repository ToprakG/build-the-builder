#!/bin/bash
# 15-minute Cursor agent run. The session log is the trace.
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export PATH="${HOME}/.local/bin:${PATH}"
STAMP="$(date +%Y%m%d-%H%M%S)"
TRACE="${ROOT}/experiments/cursor-statement/trace/session-${STAMP}.jsonl"
ERR="${ROOT}/experiments/cursor-statement/trace/session-${STAMP}.err"
mkdir -p "${ROOT}/experiments/cursor-statement/trace" "${ROOT}/experiments/cursor-statement/out"
cd "${ROOT}"
echo "${TRACE}" > "${ROOT}/experiments/cursor-statement/trace/latest.txt"
PROMPT=$(cat <<'EOF'
You have 15 minutes. Follow the skills in experiments/cursor-statement/.cursor/skills, in order: pack-inventory, speech-library, workflow-graph, side-exits, adversarial-checklist.

The case is /Users/toprakgundogdu/Downloads/test-environment. The mock that stays up is https://statement-mock.guntop-mockbank.workers.dev. Tool URLs must use that host.

Iterate on experiments/cursor-statement/latest/workflow.json. Personas are in out/diag/personas20.json and out/statement-debt/personas.json, personas-more.json, and adversarial-personas.json. Run unit tests and persona calls, patch what fails, and publish to the existing Freya workflow b223742a-f636-442d-bd61-3f4098f0c10c. Source /Users/toprakgundogdu/Downloads/build-the-builder/.env before any Freya call. Do not print secrets. If a skill names an mcp__fb__ tool that is not in this session, do that check in code.

Export the published workflow with freya.export_workflow to experiments/cursor-statement/out/final.workflow.json. Stop at 15 minutes with the best version published.
EOF
)
perl -e 'alarm 900; exec @ARGV or die $!' \
  agent -p --force --trust --sandbox disabled --approve-mcps \
  --workspace "${ROOT}" \
  --add-dir /Users/toprakgundogdu/Downloads/test-environment \
  --output-format stream-json \
  "${PROMPT}" > "${TRACE}" 2>"${ERR}"
echo "trace ${TRACE}"
