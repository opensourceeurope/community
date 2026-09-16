#!/usr/bin/env python3
"""Refresh automation/n8n/*.json from the live instance.

Preserves each file's existing node order and per-node key order, so the diff shows what
changed rather than how the API happened to order its response. Reads N8N_API_URL and
N8N_API_KEY from the environment.
"""
import collections
import json
import os
import subprocess

MAP = {
    "riA64OvtXf8DuNcN": "oc-events-intake.json",
    "YO86SOJa1iSXNi4D": "intake-sweep.json",
    "yuVFI3fv0U0yxF0k": "review.json",
    "4HTkfX1viOXGmObT": "followup.json",
    "liyDDeSeZGnngb6Z": "form-ose.json",
}
OUTDIR = "automation/n8n"


def ordered_like(new, old):
    """new, with old's key order first and any new keys appended."""
    out = collections.OrderedDict()
    for key in old:
        if key in new:
            out[key] = new[key]
    for key in new:
        if key not in out:
            out[key] = new[key]
    return out


def main():
    base = os.environ["N8N_API_URL"].rstrip("/")
    key = os.environ["N8N_API_KEY"]
    for workflow_id, filename in MAP.items():
        path = os.path.join(OUTDIR, filename)
        old = json.load(open(path))
        old_nodes = {n["id"]: n for n in old["nodes"]}
        old_order = [n["id"] for n in old["nodes"]]

        raw = subprocess.run(
            ["curl", "-sS", "-H", f"X-N8N-API-KEY: {key}",
             f"{base}/api/v1/workflows/{workflow_id}"],
            capture_output=True, text=True, check=True).stdout
        live = json.loads(raw)
        if "nodes" not in live:
            raise SystemExit(f"{workflow_id}: unexpected response")

        by_id = {n["id"]: n for n in live["nodes"]}
        order = ([i for i in old_order if i in by_id]
                 + [n["id"] for n in live["nodes"] if n["id"] not in old_nodes])
        nodes = [ordered_like(by_id[i], old_nodes[i]) if i in old_nodes else by_id[i]
                 for i in order]

        out = collections.OrderedDict()
        out["name"] = live["name"]
        out["nodes"] = nodes
        out["connections"] = ordered_like(live["connections"], old.get("connections", {}))
        out["settings"] = ordered_like(live.get("settings", {}), old.get("settings", {}))
        out["pinData"] = live.get("pinData") or {}
        with open(path, "w") as handle:
            json.dump(out, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        print(f"{filename}: {len(nodes)} nodes")


if __name__ == "__main__":
    main()
