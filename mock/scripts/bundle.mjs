// specs/*.json -> src/bundled_specs.json (keyed by tenant). Run before dev/deploy.
import fs from "node:fs";
const out = {};
for (const f of fs.readdirSync("specs").filter((f) => f.endsWith(".json"))) {
  const s = JSON.parse(fs.readFileSync("specs/" + f, "utf8"));
  const t = s.tenant || s.id || f.replace(/\.json$/, "").replace(/^spec\./, "");
  out[t] = s;
}
fs.writeFileSync("src/bundled_specs.json", JSON.stringify(out));
console.log("bundled", Object.keys(out).join(", ") || "(none)");
