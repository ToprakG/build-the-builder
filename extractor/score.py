"""Field-by-field diff of an extracted config against a hand-written config.json."""

SKIP = {"_note", "name"}


def _walk(value, prefix=""):
    if isinstance(value, dict):
        out = {}
        for key in value:
            path = f"{prefix}.{key}" if prefix else key
            out.update(_walk(value[key], path))
        return out
    if isinstance(value, list) and value and isinstance(value[0], dict):
        out = {}
        for i, item in enumerate(value):
            out.update(_walk(item, f"{prefix}[{i}]"))
        return out
    return {prefix: value}


def diff(got, gold):
    """Return (matches, mismatches). mismatches are {path, got, expected}."""
    left, right = _walk(got), _walk(gold)
    paths = [p for p in dict.fromkeys(list(right) + list(left)) if p.split(".", 1)[0].split("[", 1)[0] not in SKIP]
    bad = []
    ok = 0
    for path in paths:
        if path not in right:
            bad.append({"path": path, "got": left.get(path), "expected": "<absent>"})
        elif path not in left:
            bad.append({"path": path, "got": "<absent>", "expected": right[path]})
        elif left[path] != right[path]:
            bad.append({"path": path, "got": left[path], "expected": right[path]})
        else:
            ok += 1
    return ok, bad


def report(name, got, gold):
    ok, bad = diff(got, gold)
    total = ok + len(bad)
    lines = [f"{name}: {ok}/{total} fields"]
    for item in bad:
        lines.append(f"  {item['path']}: got {item['got']!r} expected {item['expected']!r}")
    return ok, total, "\n".join(lines)
