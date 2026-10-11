"""Generated Python for code nodes and hooks.

Every snippet = imports + the runtime helpers it needs (with their dependencies) + an optional literal MSG map between
`# MSG-BEGIN` / `# MSG-END` (the patchable what_to_do_next texts) + a try/except body that reads CFG from a JSON string.
No double braces anywhere (docs/tools-and-code.md "Rules for snippets"); the compiler asserts it.
"""

from __future__ import annotations

import ast
import json
import re
import textwrap
from functools import lru_cache
from pathlib import Path

from .i18n import MSAY_SRC, inject_parsers, localize

RUNTIME = Path(__file__).resolve().parent / "runtime.py"
MSG_BEGIN, MSG_END = "# MSG-BEGIN", "# MSG-END"


class SnippetError(ValueError):
    pass


@lru_cache(maxsize=1)
def _runtime_parts():
    src = RUNTIME.read_text(encoding="utf-8")
    tree = ast.parse(src)
    funcs, consts = {}, {}
    for node in tree.body:
        seg = ast.get_source_segment(src, node)
        if isinstance(node, ast.FunctionDef):
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant) and isinstance(first.value.value, str):
                lines = seg.splitlines()
                a, b = first.lineno - node.lineno, first.end_lineno - node.lineno
                seg = "\n".join(lines[:a] + lines[b + 1:])
            funcs[node.name] = (seg, {n.id for n in ast.walk(node) if isinstance(n, ast.Name)})
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            consts[node.targets[0].id] = (seg, {n.id for n in ast.walk(node.value) if isinstance(n, ast.Name)})
    return funcs, consts


def helpers(names) -> str:
    """Source of the named helpers plus everything they reference, in runtime.py order."""
    funcs, consts = _runtime_parts()
    need, todo = set(), list(names)
    while todo:
        n = todo.pop()
        if n in need or (n not in funcs and n not in consts):
            continue
        need.add(n)
        todo += list((funcs.get(n) or consts.get(n))[1])
    order = [k for k in list(consts) + list(funcs) if k in need]
    consts_first = [consts[k][0] for k in order if k in consts]
    funcs_after = [funcs[k][0] for k in order if k in funcs]
    return "\n".join(consts_first) + ("\n\n" if consts_first else "") + "\n\n\n".join(funcs_after)


def cfg_literal(cfg: dict) -> str:
    """A Python string literal holding the JSON of cfg, with no double braces."""
    text = json.dumps(cfg, ensure_ascii=True, sort_keys=True)
    while "{{" in text or "}}" in text:
        text = text.replace("{{", "{ {").replace("}}", "} }")
    lit = json.dumps(text)
    assert "{{" not in lit and "}}" not in lit
    return lit


def msg_block(msgs: dict) -> str:
    for k, v in msgs.items():
        if "{" in v or "}" in v:
            raise SnippetError(f"message {k!r} contains a brace")
    return f"{MSG_BEGIN}\nMSG = {json.dumps(msgs, ensure_ascii=True, sort_keys=True)}\n{MSG_END}\n"


def replace_msgs(code: str, msgs: dict) -> str:
    a, b = code.index(MSG_BEGIN), code.index(MSG_END) + len(MSG_END) + 1
    return code[:a] + msg_block(msgs) + code[b:]


def read_msgs(code: str) -> dict:
    a, b = code.index(MSG_BEGIN) + len(MSG_BEGIN), code.index(MSG_END)
    line = code[a:b].strip()
    return json.loads(line[len("MSG = "):])


_CFG_KEY = re.compile(r"""(?:CFG|cfg)(?:\[|\.get\()\s*['"](\w+)['"]""")


def cfg_subset(cfg: dict, *sources: str) -> dict:
    """Only the CFG keys the snippet's code (body + helpers) reads."""
    keys = set()
    for src in sources:
        keys |= set(_CFG_KEY.findall(src))
    return {k: v for k, v in cfg.items() if k in keys}


def build(body: str, cfg: dict, uses=(), fallback: str = "pass", msgs: dict | None = None) -> str:
    body = textwrap.dedent(body).strip("\n")
    fallback = textwrap.dedent(fallback).strip("\n")
    parts = ["import datetime", "import json", "import re", ""]
    h = helpers(set(uses) | {"jget"})
    lang = str(cfg.get("lang") or "")
    body, h = localize(body, lang), localize(h, lang)
    if lang == "tr":
        h = inject_parsers(h)
    if "msay(" in body and lang == "tr":
        h = (h + "\n\n\n" if h else "") + MSAY_SRC
    cfg = cfg_subset(cfg, body, h)
    if h:
        parts += [h, ""]
    if msgs is not None:
        parts.append(msg_block(msgs))
    parts.append("try:")
    parts.append("    CFG = json.loads(" + cfg_literal(cfg) + ")")
    parts.append(textwrap.indent(body, "    "))
    parts.append("except Exception as _err:")
    parts.append(textwrap.indent(fallback + "\n" + "vars['tcc_error'] = str(_err)[:300]", "    "))
    code = localize("\n".join(parts) + "\n", lang)  # also the fallback block
    if "{{" in code or "}}" in code:
        raise SnippetError("generated code contains a double brace")
    compile(code, "<snippet>", "exec")
    return code
