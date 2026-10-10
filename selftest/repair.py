"""Replace one text slot. Keep the edit only when its tests pass and no other test regresses."""
import json

from selftest.slots import accept_edit, get_text, set_text


def decide_keep(previous, current, slot_names):
    """previous and current map a test name to pass, fail, or error."""
    regressed = [
        name for name, outcome in previous.items()
        if outcome == "pass" and name in current and current.get(name) != "pass"
    ]
    still_failing = [name for name in slot_names if current.get(name) != "pass"]
    return (not regressed and not still_failing), regressed, still_failing


def classify(first, second_by_name):
    """A test that fails once and then passes is flaky. The others are real failures."""
    real, flaky = [], []
    for row in first:
        if row.get("outcome") == "pass":
            continue
        again = second_by_name.get(row["name"])
        if again and again.get("outcome") == "pass":
            flaky.append(row)
        else:
            real.append(row)
    return real, flaky


def group_failures(failures, tests_by_name, workflow):
    """Real failures grouped by their first editable slot. The rest are reported, not repaired."""
    groups, skipped = {}, []
    for row in failures:
        test = tests_by_name.get(row["name"]) or {}
        slot = None
        for candidate in test.get("targets") or []:
            if get_text(workflow, candidate):
                slot = candidate
                break
        if slot is None:
            skipped.append(row)
            continue
        bucket = groups.setdefault(slot["id"], {"slot": slot, "names": []})
        bucket["names"].append(row["name"])
    return groups, skipped


def propose(slot_text, failures, config, trace):
    """One model call. Returns replacement text, or None when the edit is rejected."""
    from extractor.llm import complete, model_name
    failing = []
    for row in failures:
        failing.append(
            f"- {row['name']}: {row.get('expectation') or ''}\n"
            f"  result: {row.get('reason') or row.get('outcome')}\n"
            f"  agent: {(row.get('agent') or '')[:300]}"
        )
    excerpt = json.dumps({
        key: config.get(key) for key in (
            "identity", "callback", "bands", "holidays", "queue_map", "note_map", "sentences", "outcome_map",
        )
    }, default=str)[:4000]
    message = (
        "Rewrite the slot text so the failing tests pass. Return {\"text\": \"...\"} and nothing else.\n"
        "Replace the whole slot. Do not add a new section. Keep every {{token}} exactly.\n"
        "Keep the same tool names, dates, amounts, and queue names. Do not invent a rule.\n"
        "The replacement may be at most 400 characters longer than the original.\n\n"
        "SLOT:\n" + slot_text + "\n\nFAILURES:\n" + "\n".join(failing) + "\n\nCONFIG:\n" + excerpt
    )
    payload, row = complete(
        [{"role": "system", "content": "You edit one workflow instruction. Return JSON only."},
         {"role": "user", "content": message}],
        purpose="repair slot",
    )
    if trace:
        trace.model(row.get("model") or model_name(), f"repair slot ({row.get('seconds')}s)")
    text = payload.get("text") if isinstance(payload, dict) else None
    if not accept_edit(slot_text, text):
        return None
    return text


def apply_edit(workflow, slot, text):
    previous = get_text(workflow, slot)
    if not set_text(workflow, slot, text):
        return None
    return previous


def _self_check():
    from selftest.slots import accept_edit
    kept, regressed, still = decide_keep({"a": "pass", "b": "fail"}, {"a": "pass", "b": "pass"}, ["b"])
    assert kept and not regressed and not still
    kept, regressed, _still = decide_keep({"a": "pass"}, {"a": "fail"}, ["b"])
    assert not kept and regressed == ["a"]
    kept, regressed, _still = decide_keep({"a": "pass", "c": "pass"}, {"a": "pass"}, ["a"])
    assert kept and not regressed
    real, flaky = classify(
        [{"name": "a", "outcome": "fail"}, {"name": "b", "outcome": "fail"}, {"name": "c", "outcome": "pass"}],
        {"a": {"outcome": "pass"}},
    )
    assert [row["name"] for row in flaky] == ["a"]
    assert [row["name"] for row in real] == ["b"]
    assert not accept_edit("Say {{current_floor}}.", "Say the floor.")
    assert accept_edit("Say {{current_floor}}.", "Say {{current_floor}}. Do not record a lower amount.")
    print("repair checks passed")


if __name__ == "__main__":
    _self_check()
