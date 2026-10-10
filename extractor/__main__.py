"""Read briefs into a config and score it.

    python3 -m extractor --score-all
    python3 -m extractor --score-all --static
    python3 -m extractor briefs/brief-a.md --tools tools/openapi.json --gold testbed/configs/brief-a.config.json
"""
import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from extractor.extract import extract, targets
from extractor.llm import LLMError
from extractor.propose import propose
from extractor.score import report
from extractor.tools_api import load, parse


def _raw(paths):
    return "\n\n".join(path.read_text(encoding="utf-8") for path in paths)


def static_config(briefs, tools):
    facts = parse(load(tools))
    config, conflicts = propose(_raw(briefs), facts, briefs)
    return config, conflicts


def main():
    parser = argparse.ArgumentParser(description="Extract a collections brief into a config")
    parser.add_argument("briefs", nargs="*")
    parser.add_argument("--tools")
    parser.add_argument("--gold")
    parser.add_argument("--score-all", action="store_true")
    parser.add_argument("--static", action="store_true", help="code reading only, no model")
    parser.add_argument("--out")
    args = parser.parse_args()
    if args.score_all:
        return score_all(static=args.static)
    if not args.briefs or not args.tools:
        parser.error("give brief files and --tools, or --score-all")
    briefs = [pathlib.Path(p) for p in args.briefs]
    tools = pathlib.Path(args.tools)
    if args.static:
        config, conflicts = static_config(briefs, tools)
        result = {"config": config, "conflicts": [{k: v for k, v in c.items() if k != "at"} for c in conflicts]}
    else:
        result = extract(briefs, tools)
        config = result["config"]
    if args.gold:
        gold = json.loads(pathlib.Path(args.gold).read_text())
        _ok, _total, line = report(briefs[-1].stem, config, gold)
        print(line)
        if not args.static and result.get("model_config"):
            _ok, _total, model_line = report("model", result["model_config"], gold)
            print(model_line)
    else:
        print(json.dumps({k: result[k] for k in result if k != "rules"}, indent=1)[:4000])
    if args.out:
        path = pathlib.Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=1, default=str))
        print(path)
    return 0


def score_all(static=False):
    failed = 0
    out_dir = ROOT / "results" / "extract"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, briefs, tools, gold_path in targets():
        gold = json.loads(gold_path.read_text())
        if static:
            config, conflicts = static_config(briefs, tools)
            result = {"config": config, "conflicts": conflicts}
        else:
            print(f"-- {name}", flush=True)
            try:
                result = extract(briefs, tools)
            except LLMError as error:
                print(f"  model call failed: {error}")
                config, conflicts = static_config(briefs, tools)
                ok, total, line = report(name, config, gold)
                print(line)
                print("  used the code reading; the model call did not finish")
                if ok != total:
                    failed += 1
                continue
            config = result["config"]
            (out_dir / f"{name}.json").write_text(json.dumps(result, indent=1, default=str))
        ok, total, line = report(name, config, gold)
        print(line, flush=True)
        if not static and result.get("checks"):
            print("  checks:", "; ".join(result["checks"][:6]))
        if ok != total:
            failed += 1
    return failed


if __name__ == "__main__":
    sys.exit(main())
