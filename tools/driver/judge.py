"""Per-check yes or no for one call.

Coded checks read the tool events. The other checks use the Decisions API when OPENAI_API_KEY is set.
A check with no probability stays unanswered. The call passes when every answer is yes.
"""
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from judges import judge_c

def judge_transcript(messages, case, thresholds=None):
    verdict = judge_c(messages, case, thresholds)
    checks = []
    for row in verdict["checks"]:
        if row["pass"] is None:
            answer = None
        else:
            answer = "yes" if row["pass"] else "no"
        checks.append({"check": row["text"], "answer": answer, "family": row["family"], "source": row["source"], "p": row["p"]})
    return {"pass": verdict["pass"], "min_p": verdict["min_p"], "status": verdict["status"], "checks": checks}
