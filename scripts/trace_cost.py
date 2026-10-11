"""Sum the extract model-call tokens of a trace dir and price them (default: Opus 5.5 on OpenRouter, $4/$20 per M).

    python3 scripts/trace_cost.py <trace dir> [<trace dir> ...]
"""
import json
import sys
from pathlib import Path

PRICE_IN, PRICE_OUT = 2e-6, 10e-6

total = 0.0
for d in sys.argv[1:]:
    i = o = n = 0
    for line in (Path(d) / "events.jsonl").read_text().splitlines():
        e = json.loads(line)
        if "extract" in str(e.get("purpose", "")) and e.get("tokens_in", e.get("in")) is not None:
            n += 1
            i += e.get("tokens_in", e.get("in")) or 0
            o += e.get("tokens_out", e.get("out")) or 0
    cost = i * PRICE_IN + o * PRICE_OUT
    total += cost
    print(f"{Path(d).name}: {n} extract call(s), {i} in, {o} out, ${cost:.3f}")
print(f"total ${total:.3f}")
