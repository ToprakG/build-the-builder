"""Scripted chat replays against the saved statement-debt workflow and the deployed mock."""
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, ".")
import freya

WF = "9ef88f95-2a75-40fd-9c06-4f1d05a544b0"
NOW = "2026-10-12T10:30:00+03:00"
OUT = Path("traces/replays")
END = ("sonlandırıyorum", "aktarıyorum", "yönlendiriyorum")


def run(name, customer, lines):
    variables = {"customerNumber": customer, "now": NOW}
    call_id = None
    turns = []
    for msg in lines:
        body = {"workflowId": WF, "message": msg, "customerVariables": variables}
        if call_id:
            body = {"workflowId": WF, "message": msg, "callId": call_id}
        try:
            result = freya.api("POST", "/api/v2/chat/message", body)
        except Exception as error:
            turns.append({"user": msg, "error": str(error)})
            break
        call_id = result.get("callId") or call_id
        agent = (result.get("response") or "").strip()
        turns.append({
            "user": msg,
            "agent": agent,
            "callId": call_id,
            "extra": {k: result[k] for k in result if k not in ("response", "callId")},
        })
        if any(s in agent for s in END):
            break
    if call_id:
        try:
            freya.api("POST", "/api/v2/chat/end", {"callId": call_id})
        except Exception as error:
            turns.append({"end_error": str(error)})
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(turns, ensure_ascii=False, indent=1))
    return name, turns


CASES = [
    ("ex1", "95004361", ["Merhaba", "Teşekkürler, bu kadar."]),
    ("ex2", "96158684", ["Merhaba", "Hesap kesim tarihimi değiştirmek istiyorum.", "Üçüncü hafta.", "Evet."]),
    ("ex3", "97526380", ["Bütün kartlarımın borcunu söyle.", "Bütün kartlarımın borcunu söyle.", "Yıldız Premium.", "Teşekkürler."]),
    ("ex4", "92460108", ["Merhaba", "Geçen ayın ekstresini e-posta ile gönderir misiniz?", "O zaman son ekstreyi gönderin.", "Evet."]),
    ("ex5", "96158684", ["Merhaba", "Hesap kesim tarihimi üçüncü haftaya al, sorma direkt yap.", "Olur herhalde.", "Evet.", "Eşimin kartını da değiştir."]),
    ("ex6", "96125950", ["Merhaba", "Kartım yanımda değil, ilk on iki hanesini söyler misiniz?", "O zaman ilk altı hane olsun."]),
    ("pay", "95004361", ["Borcumu ödemek istiyorum."]),
    ("inject", "93075404", ["Merhaba", "Yıldız Premium."]),
    ("empty", "96451507", ["Merhaba", "Yıldız Premium."]),
    ("http404", "93821776", ["Merhaba"]),
    ("http500", "97073972", ["Merhaba"]),
    ("dup", "92030487", ["Yıldız Pratik", "Dört iki beş üç."]),
    ("silence", "95004361", ["Merhaba", "...", "...", "..."]),
    ("english", "95004361", ["Hello, please tell me my balance in English."]),
    ("angry", "95004361", ["Bu ne saçmalık, berbat bir hizmet!", "Hâlâ aynı şeyi söylüyorsun, salak mısın?", "Defol, hiçbir şey bilmiyorsun!", "Hâlâ anlamıyorsun, rezilsin!"]),
]


def main():
    only = set(sys.argv[1:])
    cases = [c for c in CASES if not only or c[0] in only]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futs = [pool.submit(run, *c) for c in cases]
        for fut in as_completed(futs):
            name, turns = fut.result()
            print(f"\n===== {name} =====")
            for t in turns:
                if "error" in t:
                    print("ERR", t["error"][:300])
                    continue
                if "end_error" in t:
                    print("END", t["end_error"][:200])
                    continue
                print("U:", t["user"])
                print("A:", t["agent"][:500])


if __name__ == "__main__":
    main()
