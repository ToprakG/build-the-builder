"""Run one conversation on Vapi with a trivial one-tool agent and print the transcript.

Needs VAPI_API_KEY, an LLM key for SIMULATOR_MODEL, and TOOLS_BASE_URL pointing at the kasko tool server.
"""

from __future__ import annotations

import json

from agentforge.config import settings
from agentforge.platform.vapi import VapiPlatform
from agentforge.schemas import AgentConfig, Persona, ToolDef


def main() -> None:
    cfg = AgentConfig(
        version_label="smoke",
        model=settings().agent_model,
        first_message="Merhaba, ben yapay zeka asistanıyım. Kimliğinizi doğrulamak için TC kimlik numaranızın son dört hanesini söyler misiniz?",
        system_prompt=(
            "Sen bir sigorta şirketinin Türkçe sesli asistanısın. Kısa cümlelerle konuş. "
            "Arayan TC son dört hanesini ve doğum tarihini verince verify_identity aracını çağır, "
            "sonucu sesli olarak söyle ve görüşmeyi kibarca bitir."
        ),
        tools=[
            ToolDef(
                name="verify_identity",
                description="Arayanın kimliğini doğrular.",
                parameters={
                    "type": "object",
                    "properties": {
                        "tckn_last4": {"type": "string", "description": "TCKN son 4 hane"},
                        "birth_date": {"type": "string", "description": "Doğum tarihi, YYYY-AA-GG"},
                    },
                    "required": ["tckn_last4", "birth_date"],
                },
            )
        ],
    )
    persona = Persona(
        name="smoke-caller",
        prompt="Adın Ayşe Yılmaz. TC son dört hanen 4821, doğum tarihin 12 Mart 1985. Sadece kimliğini doğrulatmak istiyorsun.",
        successCriteria="Agent calls verify_identity with tckn_last4=4821 and birth_date=1985-03-12.",
        tags=["train"],
    )
    run = VapiPlatform().run_conversation(cfg, persona, run_idx=0)
    for turn in run.transcript:
        print(f"[{turn.turn_idx:02d}] {turn.role:9s} {turn.text}")
        for call in turn.tool_calls or []:
            print(f"           -> {call['name']}({json.dumps(call['args'], ensure_ascii=False)})")
    print(f"\nended={run.ended_reason} duration={run.duration_s}s cost={run.cost_usd}")
    print("tool calls:", json.dumps([c.model_dump() for c in run.tool_calls], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
