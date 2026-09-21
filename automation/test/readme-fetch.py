#!/usr/bin/env python3
"""Runs apply 5's README nodes in a real n8n Code node sandbox and checks what they fetch.

The sandbox is the point. The Code node has no `URL` global, so `new URL()` throws a
ReferenceError there while it parses perfectly under plain `node`. The parser caught that
alongside a genuinely malformed URL and returned null either way, so every repository was
refused and no README was ever fetched, with nothing failing anywhere to say so. A test
that runs the parser under plain node cannot see that. This one runs it where it runs in
production: an n8n container, started from the committed export.

Both node bodies are read out of automation/n8n/summary.json rather than copied, so the
test exercises what is committed and cannot drift from it.

The fetch cases reach real repositories over the network, which is deliberate: a candidate
URL that no longer matches a repository host's API is the other half of what this catches.

    python3 automation/test/readme-fetch.py

Needs docker and a network. Prints one line per case and exits non-zero if any case failed.
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXPORT = os.path.join(ROOT, "automation", "n8n", "summary.json")
FIXTURES = os.path.join(ROOT, "automation", "test", "fixtures", "readme-fetch.json")
IMAGE = os.environ.get("N8N_TEST_IMAGE", "docker.n8n.io/n8nio/n8n:latest")
WORKFLOW_ID = "readmefetchtest"
NODES = ("Choose the repository URL", "Fetch the README")


def node_code(name):
    """The jsCode of one node of apply 5, as committed."""
    export = json.load(open(EXPORT))
    for node in export["nodes"]:
        if node["name"] == name:
            return node["parameters"]["jsCode"]
    raise SystemExit(f"{EXPORT}: no node named {name!r}")


def build(workdir):
    """A workflow that feeds the fixtures through the two real nodes."""
    fixtures = json.load(open(FIXTURES))
    feed = "return " + json.dumps([{"json": f} for f in fixtures]) + ";"

    def code(node_id, name, js, x):
        return {"id": node_id, "name": name, "type": "n8n-nodes-base.code",
                "typeVersion": 2, "position": [x, 0],
                "parameters": {"mode": "runOnceForAllItems", "jsCode": js}}

    workflow = {
        "id": WORKFLOW_ID,
        "name": "readme fetch test",
        "settings": {"executionOrder": "v1"},
        "nodes": [
            {"id": "trigger", "name": "Manual", "type": "n8n-nodes-base.manualTrigger",
             "typeVersion": 1, "position": [0, 0], "parameters": {}},
            code("rows", "Rows", feed, 200),
            code("choose", NODES[0], node_code(NODES[0]), 400),
            code("fetch", NODES[1], node_code(NODES[1]), 600),
        ],
        "connections": {
            "Manual": {"main": [[{"node": "Rows", "type": "main", "index": 0}]]},
            "Rows": {"main": [[{"node": NODES[0], "type": "main", "index": 0}]]},
            NODES[0]: {"main": [[{"node": NODES[1], "type": "main", "index": 0}]]},
        },
    }
    path = os.path.join(workdir, "workflow.json")
    with open(path, "w") as handle:
        json.dump(workflow, handle)
    return fixtures


def run(workdir):
    """Import and execute the workflow in a throwaway n8n container."""
    state = os.path.join(workdir, "n8n-home")
    os.makedirs(state, exist_ok=True)
    os.chmod(workdir, 0o777)
    os.chmod(state, 0o777)
    command = (f"n8n import:workflow --input=/data/workflow.json >/dev/null 2>&1 && "
               f"n8n execute --id={WORKFLOW_ID} 2>/dev/null")
    result = subprocess.run(
        ["docker", "run", "--rm",
         "-v", f"{workdir}:/data", "-v", f"{state}:/home/node/.n8n",
         "-e", "N8N_DIAGNOSTICS_ENABLED=false", "-e", "N8N_RUNNERS_ENABLED=true",
         "--entrypoint", "sh", IMAGE, "-c", command],
        capture_output=True, text=True, timeout=900)
    if "{" not in result.stdout:
        raise SystemExit("n8n produced no execution result:\n"
                         + (result.stderr or result.stdout)[-2000:])
    return json.loads(result.stdout[result.stdout.index("{"):])


def report(execution, fixtures):
    result = execution["data"]["resultData"]
    if result.get("error"):
        raise SystemExit("execution failed: " + json.dumps(result["error"])[:1000])
    items = [i["json"] for i in result["runData"][NODES[1]][0]["data"]["main"][0]]
    got = {item["slug"]: item for item in items}

    failures = 0
    print(f"{'case':<36} {'host_kind':<10} {'readme':<8} result")
    print("-" * 72)
    for fixture in fixtures:
        item = got.get(fixture["slug"], {})
        host_kind = item.get("host_kind", "MISSING")
        fetched = bool((item.get("readme") or "").strip())
        ok = host_kind == fixture["expect_host_kind"] and fetched == fixture["expect_readme"]
        failures += 0 if ok else 1
        note = "" if ok else (f"   expected host_kind={fixture['expect_host_kind']} "
                              f"readme={fixture['expect_readme']}")
        print(f"{fixture['case']:<36} {host_kind:<10} {str(fetched):<8} "
              f"{'pass' if ok else 'FAIL'}{note}")
    print("-" * 72)
    print(f"{len(fixtures) - failures}/{len(fixtures)} passed")
    return failures


def main():
    import tempfile
    with tempfile.TemporaryDirectory(prefix="ose-readme-fetch-") as workdir:
        fixtures = build(workdir)
        failures = report(run(workdir), fixtures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
