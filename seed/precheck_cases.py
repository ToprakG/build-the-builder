"""Fast pre-check for every testbed case. No Freya call and no bank.

For each brief the workflow is built locally. Each case's products are handed to
the compute_products code node, the same snippet Freya runs, and the result is
compared with that case's oracle. This does not play the phone agent. Run it
before tools/run_fake.py.

    python3 seed/precheck_cases.py
    python3 seed/precheck_cases.py --brief f1-nordhavn
"""
import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TESTBED = ROOT / "testbed"


def load_testbed(brief_id):
    if brief_id in ("brief-a", "brief-b"):
        cases = json.loads((TESTBED / "personas" / f"{brief_id}.cases.json").read_text())
        config = json.loads((TESTBED / "configs" / f"{brief_id}.config.json").read_text())
    else:
        folder = TESTBED / "fake_briefs" / brief_id
        cases = json.loads((folder / "cases.json").read_text())
        config = json.loads((folder / "config.json").read_text())
    return cases, config


def briefs(only=None):
    names = ["brief-a", "brief-b"] + [path.name for path in sorted((TESTBED / "fake_briefs").iterdir()) if path.is_dir()]
    if only:
        names = [name for name in names if name == only]
    return names


def tools_path(brief_id):
    if brief_id in ("brief-a", "brief-b"):
        return ROOT / "tools" / "openapi.json"
    return TESTBED / "fake_briefs" / brief_id / "tools.openapi.json"


def products_for(case, config):
    fields = config["account_fields"]
    fmt = config.get("product_id_format", "p{n}")
    rows = []
    for index, product in enumerate(case.get("products") or [], 1):
        row = {fields["product_id"]: fmt.format(n=index)}
        row.update(product)
        rows.append(row)
    return rows


def compute_snippet(brief_id, config):
    from seed.graph import build_workflow
    from seed.workflow_check import iter_snippets
    tools_api = json.loads(tools_path(brief_id).read_text())
    workflow = build_workflow(config, tools_api)
    for kind, where, code in iter_snippets(workflow):
        if kind == "code" and where == "node:compute_products":
            return code
    raise RuntimeError(f"{brief_id}: compute_products snippet missing")


def check_case(case, config, code):
    from seed.workflow_check import _run
    problems = []
    oracle = case.get("oracle") or {}
    expected = case["expected"]
    mapped = (config.get("outcome_map") or {}).get(oracle.get("outcome_key"))
    if mapped is None:
        problems.append(f"unknown outcome key {oracle.get('outcome_key')}")
    elif (expected.get("outcome"), expected.get("reason")) != (mapped[0], mapped[1]):
        problems.append(f"expected {(expected.get('outcome'), expected.get('reason'))} != map {tuple(mapped)}")
    variables = {"now": case["variables"]["now"], "products": products_for(case, config)}
    error = _run(code, variables)
    if error:
        problems.append(f"compute_products {error}")
        return problems
    facts = json.loads(variables.get("products_json") or "[]")
    rows = oracle.get("products") or []
    if len(facts) != len(rows):
        problems.append(f"product count {len(facts)} != {len(rows)}")
    for got, row in zip(facts, rows):
        pairs = (
            ("band", got.get("band"), row["band"]),
            ("deadline", got.get("deadline"), row["deadline"]),
            ("attempts", got.get("attempts_allowed"), row["attempts"]),
            ("attempt1_date", got.get("attempt1_date"), row["attempt1_date"]),
            ("floor", round(float(got.get("floor") or 0), 2), round(float(row["floor"]), 2)),
            ("past_due", round(float(got.get("full") or 0), 2), round(float(row["past_due"]), 2)),
            ("product_id", got.get("product_id"), row["product_id"]),
        )
        for name, left, right in pairs:
            if left != right:
                problems.append(f"{row.get('product_id')} {name} {left} != {right}")
    return problems


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--brief")
    args = parser.parse_args()
    total = failed = 0
    for name in briefs(args.brief):
        cases, config = load_testbed(name)
        code = compute_snippet(name, config)
        bad = []
        for case in cases:
            problems = check_case(case, config, code)
            total += 1
            if problems:
                failed += 1
                bad.append((case["id"], problems))
        print(f"{name:16} {len(cases) - len(bad):3}/{len(cases):<3}")
        for case_id, problems in bad[:8]:
            print("   -", case_id, "; ".join(problems[:3]))
    print(f"\n{total - failed} / {total} cases match the oracle")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
