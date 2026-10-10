"""Write the testbed: briefs, tool API files, correct configs and cases for Brief A, Brief B and the seven fake briefs."""
import copy
import json
import os
import pathlib
import sys

import assemble
import briefs
import configs
import toolapi

OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "testbed")


def export_cfg(cfg):
    c = copy.deepcopy(cfg)
    c.pop("now_dates", None)
    for t in c["product_types"].values():
        t.pop("floor_pct", None)
    c["_note"] = "Hand-written correct config: what a perfect extractor reads from this brief. Canonical keys on the left, the brief's own values on the right."
    return c


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n")


def main():
    stats = []
    for cfg in configs.ALL:
        cases = assemble.build(cfg)
        if cfg["id"] in ("brief-a", "brief-b"):
            dump(OUT / "personas" / f"{cfg['id']}.cases.json", cases)
            dump(OUT / "configs" / f"{cfg['id']}.config.json", export_cfg(cfg))
        else:
            d = OUT / "fake_briefs" / cfg["id"]
            text = {"f5-halden-email": briefs.render_f5, "f6-corvid-memo": briefs.render_f6, "f7-lumen-codes": briefs.render_f7}.get(cfg["id"], briefs.render_clean)(cfg)
            (d).mkdir(parents=True, exist_ok=True)
            (d / "brief.md").write_text(text)
            dump(d / "tools.openapi.json", toolapi.build(cfg))
            dump(d / "config.json", export_cfg(cfg))
            dump(d / "cases.json", cases)
        stats.append((cfg["id"], len(cases)))
    print(stats, sum(n for _, n in stats))


if __name__ == "__main__":
    main()
