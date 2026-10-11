"""Serve build-the-builder's mock bank with T-CC's OWN cases and policy (run as a script, in its own process).

    python vendor_bankd.py --vendor <repo> --cases cases.json --config policy.json --port 8787

The mock bank (vendor tools/driver/mockbank.py) reads every field name, enum and rule from the config, which is the
same canonical schema as our policy.json. It needs FastAPI and uvicorn; the caller picks a Python that has them.
"""
import argparse
import json
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vendor", required=True)
    ap.add_argument("--cases")
    ap.add_argument("--config")
    ap.add_argument("--multi", help="JSON file {name: {cases, config}}: one bank per name under /<name>")
    ap.add_argument("--port", type=int, default=8787)
    a = ap.parse_args()
    root = Path(a.vendor).resolve()
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "tools" / "driver"))
    import uvicorn
    from mockbank import create_app

    if a.multi:  # several briefs behind ONE port (and so one tunnel): /<name>/v1/<tool>, /<name>/v1/practice/...
        from fastapi import FastAPI
        app = FastAPI()
        for name, spec in json.loads(Path(a.multi).read_text()).items():
            app.mount("/" + name, create_app(cases=json.loads(Path(spec["cases"]).read_text()),
                                             config=json.loads(Path(spec["config"]).read_text())))
    else:
        app = create_app(cases=json.loads(Path(a.cases).read_text()), config=json.loads(Path(a.config).read_text()))
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
