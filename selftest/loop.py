"""Self-test after publish: generate tests, run them, repair text slots, run them again.

Code is never edited. A slot edit is kept only when its tests pass and no passing test regresses.
The loop stops when the time left is shorter than one run.
"""
import json
import pathlib
import time

from selftest.brief_tests import disagreements, expectations, reextract
from selftest.gen_tests import generate
from selftest.repair import apply_edit, classify, decide_keep, get_text, group_failures, propose
from selftest.slots import clone
from tools.unit_tests import execute, index_results, plan_from_timing, rerun, write_timing

ROOT = pathlib.Path(__file__).resolve().parent.parent
WINDOW_SECONDS = 24 * 60
CRITICAL = ("identity-limit", "deadline", "holiday", "transfer", "promise-fail")


def _read_briefs(paths):
    parts = []
    for path in paths:
        file = pathlib.Path(path)
        if file.exists() and file.suffix.lower() in {".md", ".txt"}:
            parts.append(file.read_text())
    return "\n\n".join(parts)


def _outcomes(results):
    return {row["name"]: row.get("outcome") for row in results}


def _log_results(trace, results):
    passed = sum(row.get("outcome") == "pass" for row in results)
    trace.line(f"- {passed}/{len(results)} passed")
    for row in results:
        if row.get("outcome") != "pass":
            trace.line(f"- fail {row['name']}: {(row.get('reason') or row.get('outcome') or '')[:200]}")
    return passed


def _publish(workflow_id, workflow, trace, label):
    import freya
    version = freya.update_workflow(workflow_id, workflow)
    trace.line(f"- published {label}: version {version}")
    return version


def _apply_extraction(config, plain, tool_api, trace):
    """Brief expectations that disagree with the config. One question per field. Keep a value only when checks do not get worse."""
    from extractor.checks import check
    from extractor.tools_api import load, parse
    try:
        scenarios, found = expectations(plain, trace)
    except Exception as error:
        trace.line(f"- brief scenarios failed: {error}")
        return config, False
    trace.line(f"- brief scenarios: {len(scenarios)}")
    rows = disagreements(plain, config, found)
    if not rows:
        trace.line("- brief expectations agree with the config")
        return config, False
    facts = parse(load(tool_api))
    try:
        before = check(config, facts, plain)
    except Exception as error:
        trace.line(f"- config checks could not run: {error}")
        return config, False
    changed = False
    for item in rows:
        trace.line(f"- disagree {item['path']}: config {item['config']!r}, brief {item['brief']!r}")
        try:
            value = reextract(plain, item, trace)
        except Exception as error:
            trace.line(f"- re-extract {item['path']} failed: {error}")
            continue
        if value is None:
            trace.line(f"- re-extract {item['path']}: kept the config value")
            continue
        trial = json.loads(json.dumps(config))
        from selftest.brief_tests import _set
        _set(trial, item["path"], value)
        after = check(trial, facts, plain)
        if len(after) > len(before):
            trace.line(f"- re-extract {item['path']}: dropped, checks went from {len(before)} to {len(after)}")
            continue
        _set(config, item["path"], value)
        before = after
        changed = True
        trace.line(f"- re-extract {item['path']}: now {value!r}")
    return config, changed


def _repair_round(workflow, tests, real, results, workflow_id, config, trace, deadline, last_run):
    """Edit one slot at a time. Revert that slot when the suite gets worse."""
    by_name = {test["name"]: test for test in tests}
    by_result = index_results(results)
    good_results = results
    groups, skipped = group_failures(real, by_name, workflow)
    for row in skipped:
        trace.line(f"- not repaired {row['name']}: no conversation slot (code and wiring stay as they are)")
    previous = _outcomes(results)
    kept_workflow = clone(workflow)
    edited = 0
    for bucket in list(groups.values())[:3]:
        if time.time() + last_run + 30 > deadline:
            trace.line("- repair stopped: not enough time for another run")
            break
        slot = bucket["slot"]
        old = get_text(workflow, slot)
        failing = [by_result[name] for name in bucket["names"] if name in by_result]
        for row in failing:
            test = by_name.get(row["name"]) or {}
            row["expectation"] = test.get("expectation") or ""
        trace.line(f"- repair {slot['id']} for {', '.join(bucket['names'])}")
        trace.line(f"- before: {old[:300].replace(chr(10), ' ')}")
        try:
            text = propose(old, failing, config, trace)
        except Exception as error:
            trace.line(f"- repair {slot['id']} failed: {error}")
            continue
        if not text:
            trace.line(f"- repair {slot['id']}: edit rejected")
            continue
        if not apply_edit(workflow, slot, text):
            trace.line(f"- repair {slot['id']}: slot was not found")
            continue
        trace.line(f"- after: {text[:300].replace(chr(10), ' ')}")
        try:
            _publish(workflow_id, workflow, trace, slot["id"])
            results, seconds = execute(workflow_id, tests)
        except Exception as error:
            trace.line(f"- rerun after {slot['id']} failed: {error}. Reverting.")
            apply_edit(workflow, slot, old)
            try:
                _publish(workflow_id, workflow, trace, f"revert {slot['id']}")
            except Exception as publish_error:
                trace.line(f"- revert publish failed: {publish_error}")
            continue
        last_run = max(last_run, seconds)
        current = _outcomes(results)
        keep, regressed, still = decide_keep(previous, current, bucket["names"])
        _log_results(trace, results)
        if keep:
            trace.line(f"- kept {slot['id']}")
            previous = current
            good_results = results
            kept_workflow = clone(workflow)
            edited += 1
            real = [row for row in real if row["name"] not in bucket["names"] or current.get(row["name"]) != "pass"]
        else:
            trace.line(f"- reverted {slot['id']}: regressed {regressed or 'none'}; still failing {still or 'none'}")
            apply_edit(workflow, slot, old)
            workflow = clone(kept_workflow)
            results = good_results
            try:
                _publish(workflow_id, workflow, trace, f"revert {slot['id']}")
            except Exception as error:
                trace.line(f"- revert publish failed: {error}")
    return workflow, good_results, real, last_run, edited


def run_loop(workflow, config, workflow_id, briefs, tool_api, trace):
    """Returns the workflow to keep. Publishes each candidate it keeps."""
    from tools.envfile import load_env
    load_env()
    started = time.time()
    deadline = started + WINDOW_SECONDS
    limit, rounds, reason = plan_from_timing()
    trace.section("Unit self-test")
    trace.line(f"- {reason}")

    plain = _read_briefs(briefs)
    rebuilt = False
    if plain:
        config, rebuilt = _apply_extraction(config, plain, tool_api, trace)
        if rebuilt:
            from extractor.tools_api import load
            from seed.graph import build_workflow
            workflow = build_workflow(config, load(tool_api))
            _publish(workflow_id, workflow, trace, "after extraction repair")

    tests = generate(config, limit=limit)
    trace.line(f"- generated {len(tests)} config tests")
    try:
        results, seconds = execute(workflow_id, tests)
    except Exception as error:
        trace.line(f"- unit run failed: {error}")
        return workflow
    write_timing(len(results), seconds, sum(row.get("outcome") == "pass" for row in results))
    _next_limit, _next_rounds, next_reason = plan_from_timing()
    trace.line(f"- first run {seconds:.0f}s. Next build: {next_reason}")
    _log_results(trace, results)

    failed_names = [row["name"] for row in results if row.get("outcome") != "pass"]
    second = {}
    if failed_names and time.time() + 30 < deadline:
        trace.line(f"- re-run {len(failed_names)} failure(s) once")
        try:
            again, _seconds = rerun(workflow_id, tests, failed_names)
            second = index_results(again)
            for row in again:
                trace.line(f"- rerun {row['name']}: {row.get('outcome')}")
        except Exception as error:
            trace.line(f"- re-run failed: {error}")
    real, flaky = classify(results, second)
    for row in flaky:
        trace.line(f"- flaky {row['name']}: failed once, then passed. Not repaired in round 1.")

    last_run = seconds
    best = clone(workflow)
    best_passed = sum(row.get("outcome") == "pass" for row in results)
    for round_number in range(1, rounds + 1):
        if not real:
            trace.line(f"- round {round_number}: no real failures")
            break
        if time.time() + last_run + 30 > deadline:
            trace.line(f"- round {round_number} skipped: not enough time")
            break
        trace.line(f"- repair round {round_number}: {len(real)} real failure(s)")
        workflow, results, real, last_run, edited = _repair_round(
            workflow, tests, real, results, workflow_id, config, trace, deadline, last_run,
        )
        passed = sum(row.get("outcome") == "pass" for row in results)
        if passed >= best_passed:
            best = clone(workflow)
            best_passed = passed
        if edited == 0:
            trace.line(f"- round {round_number}: no edit kept")
            break

    critical = [test["name"] for test in tests if any(tag in CRITICAL for tag in test.get("tags") or [])]
    _publish(workflow_id, best, trace, "best")
    if critical and time.time() + (2 * last_run) < deadline:
        trace.line("- final round: critical tests run twice")
        try:
            first, _ = rerun(workflow_id, tests, critical)
            second_rows, _ = rerun(workflow_id, tests, critical)
            b = index_results(second_rows)
            for row in first:
                other = b.get(row["name"], {})
                if row.get("outcome") != other.get("outcome"):
                    trace.line(f"- flaky at the end {row['name']}: {row.get('outcome')} then {other.get('outcome')}")
                elif row.get("outcome") != "pass":
                    trace.line(f"- still failing {row['name']}")
        except Exception as error:
            trace.line(f"- final critical run failed: {error}")

    trace.line(f"- best version kept {best_passed} passing test(s). Elapsed {time.time() - started:.0f}s.")
    return best
