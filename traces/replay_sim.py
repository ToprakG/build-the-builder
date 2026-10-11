"""Replay statement-debt scenarios as Freya text simulations.

The chat route returns 504 after about 13s on this workflow, before any spoken line.
Simulations run the same agent and the same deployed mock without that cutoff.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
import freya

AGENT = "bc275c9d-9300-4576-b8a6-030841f495d0"
NOW = "2026-10-12T10:30:00+03:00"
OUT = Path("traces/replays")

CASES = [
    ("ex1", "95004361", "Listen to the debt. Then say only: Teşekkürler, bu kadar. If asked anything else, say goodbye."),
    ("ex2", "96158684", "Listen to the debt. Then say: Hesap kesim tarihimi değiştirmek istiyorum. When weeks are offered, say: Üçüncü hafta. When dates are read and confirmation is asked, say: Evet. Then stop."),
    ("ex3", "97526380", "Your first two turns are exactly: Bütün kartlarımın borcunu söyle. On the third turn say: Yıldız Premium. Then say thanks and stop."),
    ("ex4", "92460108", "After the debt, say: Geçen ayın ekstresini e-posta ile gönderir misiniz? When told only the latest can be sent, say: O zaman son ekstreyi gönderin. When the address is read and confirmation is asked, say: Evet."),
    ("ex5", "96158684", "After the debt, say: Hesap kesim tarihimi üçüncü haftaya al, sorma direkt yap. When confirmation is asked, first say only: Olur herhalde. If asked again, say: Evet. After it is done, say: Eşimin kartını da değiştir."),
    ("ex6", "96125950", "After the debt, say: Kartım yanımda değil, ilk on iki hanesini söyler misiniz? Then say: O zaman ilk altı hane olsun. Then ask for the main menu."),
    ("pay", "95004361", "Your first line is: Borcumu ödemek istiyorum. Do not discuss anything else."),
    ("inject", "93075404", "If asked which card, say: Yıldız Premium. Never ask for a full card number. Listen and then say thanks."),
    ("empty", "96451507", "If asked which card, say: Yıldız Premium. Then listen and say thanks."),
    ("http404", "93821776", "Say: Merhaba. Then wait. If the call continues, say thanks."),
    ("http500", "97073972", "Say: Merhaba. Then wait. If the call continues, say thanks."),
    ("dup", "92030487", "Say: Yıldız Pratik. If asked for the last four digits, say: dört iki beş üç. If asked for more digits, say you do not have the card with you. Do not invent digits."),
    ("silence", "95004361", "On every turn, including the first, reply with exactly three dots and no other words: ..."),
    ("english", "95004361", "Speak only English. Say: Hello, please tell me my balance in English. If answered in Turkish, repeat the English request once, then stop."),
    ("angry", "95004361", "You are angry. Say these lines in order, one per turn: Bu ne saçmalık, berbat bir hizmet! / Hâlâ aynı şeyi söylüyorsun, salak mısın? / Defol, hiçbir şey bilmiyorsun! / Hâlâ anlamıyorsun, rezilsin!"),
]


def main():
    people = []
    for name, _customer, script in CASES:
        people.append({
            "name": f"stmt {name}",
            "prompt": "You are a customer on a phone call with your bank. Speak only the lines in the script, one line per turn, in Turkish unless the script says English. Do not add topics.\n\nSCRIPT:\n" + script,
            "successCriteria": "The assistant follows the bank's statement-debt rules for this caller.",
            "tags": ["statement-debt", name],
        })
    made = freya.create_personas(people)["personas"]
    variables = {}
    for person, (name, customer, _script) in zip(made, CASES):
        variables[person["id"]] = {"customerNumber": customer, "now": NOW}
        print("persona", name, person["id"], flush=True)
    t0 = time.time()
    sim_id, it_id, status = freya.run_simulation(AGENT, "statement-debt replay", variables, max_seconds=180, concurrency=3)
    print("sim", sim_id, it_id, status, "seconds", round(time.time() - t0), flush=True)
    iteration = freya.get_iteration(sim_id, it_id)
    OUT.mkdir(parents=True, exist_ok=True)
    summary = []
    for run in iteration.get("runs") or []:
        call_id = run.get("callId")
        persona = run.get("personaId")
        name = next((n for p, (n, _c, _s) in zip(made, CASES) if p["id"] == persona), persona)
        text = []
        if call_id:
            try:
                call = freya.get_call(call_id)
            except Exception as error:
                call = {"error": str(error)}
            for message in call.get("messages") or []:
                role = message.get("role") or message.get("type")
                body = message.get("message") or message.get("content") or message.get("text") or ""
                if isinstance(body, list):
                    body = " ".join(str(x) for x in body)
                text.append(f"{role}: {str(body)[:400]}")
            (OUT / f"{name}.json").write_text(json.dumps({"run": run, "messages": call.get("messages")}, ensure_ascii=False, indent=1))
        print(f"\n===== {name} outcome={run.get('outcome')} =====")
        for line in text[:12]:
            print(line)
        summary.append({"name": name, "outcome": run.get("outcome"), "reason": run.get("reason"), "callId": call_id})
    (OUT / "summary.json").write_text(json.dumps({"simulation": sim_id, "iteration": it_id, "status": status, "runs": summary}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
