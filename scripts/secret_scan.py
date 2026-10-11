"""Scan the commits that would be pushed for secrets; prints COUNTS only, never the matched text.   python3 scripts/secret_scan.py <git range, e.g. origin/main..main>
Looks for key-like patterns and for every value of .env / KEY|TOKEN|SECRET|PASSWORD environment variables (model names such as JUDGE_MODEL are ignored).
Exit 1 if anything is found."""
import os
import re
import subprocess
import sys

rng = sys.argv[1]
text = subprocess.run(["git", "log", "-p", "--no-color", rng], capture_output=True, text=True, errors="ignore").stdout
pats = {"FREYA_API_KEY value": r"FREYA_API_KEY\w*\s*=\s*[A-Za-z0-9_\-]{10,}", "sk- key": r"\bsk-[A-Za-z0-9_\-]{16,}", "Bearer token": r"Bearer\s+[A-Za-z0-9._\-]{20,}",
        "x-api-key value": r"x-api-key[\"']?\s*[:=]\s*[\"'][A-Za-z0-9_\-]{16,}", "private key": r"BEGIN [A-Z ]*PRIVATE KEY"}
vals = set()
if os.path.exists(".env"):
    for line in open(".env", errors="ignore"):
        line = line.strip().removeprefix("export ")
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            v = v.strip().strip("\"'")
            if len(v) >= 8 and not re.search(r"MODEL|URL|HOST|PROVIDER|VOICE|ID$", k):
                vals.add(v)
for k, v in os.environ.items():
    if re.search(r"KEY|TOKEN|SECRET|PASSWORD", k) and len(v) >= 8:
        vals.add(v)
hits = {k: len(re.findall(p, text)) for k, p in pats.items()}
real = sum(1 for v in vals if v in text)
print(f"secret scan of {rng}: {len(text) // 1000} KB; pattern hits {sum(hits.values())} {hits if any(hits.values()) else ''}; secret VALUES found: {real}")
sys.exit(1 if sum(hits.values()) + real else 0)
