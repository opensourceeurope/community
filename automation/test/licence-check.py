#!/usr/bin/env python3
"""Runs apply 5's licence check inside a real n8n Code node sandbox, against the live OSI list.

`Check the licence against the OSI list` fetches https://opensource.org/api/licenses and
matches the licence answer to it, and `Render summary request` turns the result into the line
the model reads. Both node bodies are read out of automation/n8n/summary.json, so this tests
what is committed. The container runs them the way production does, which is the only place
a missing sandbox global shows up (see readme-fetch.py for the case that taught us that).

The OSI list is fetched for real on purpose: a changed API, a list that comes back short or
without MIT, turns every answer into "unavailable", and that is what this catches.

    python3 automation/test/licence-check.py

Needs docker and a network. Prints one line per case and exits non-zero if any case failed.
"""
import importlib.util
import json
import os
import sys
import tempfile

sys.dont_write_bytecode = True  # importing readme-fetch.py must not leave a __pycache__ behind
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("readme_fetch", os.path.join(HERE, "readme-fetch.py"))
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)

CHECK = "Check the licence against the OSI list"
RENDER = "Render summary request"
LINE = "Licence check against the Open Source Initiative list:"

# (case, applicant kind, licence answer, expected licence_osi, text the model must be shown)
CASES = [
    ("spdx id", "project", "MIT", "approved", "mentions The MIT License (MIT)"),
    ("version written loosely", "project", "Apache 2.0", "approved", "(Apache-2.0)"),
    ("gpl shorthand", "project", "GPLv3", "approved", "(GPL-3.0)"),
    ("or-later suffix", "project", "AGPL-3.0-or-later", "approved", "(AGPL-3.0)"),
    ("european licence", "project", "EUPL 1.2", "approved", "(EUPL-1.2)"),
    ("non-commercial", "project", "CC BY-NC 4.0", "unmatched", "matched no licence on it"),
    ("another version", "project", "BSL 1.1", "unmatched", "matched no licence on it"),
    ("an ordinary word", "project", "Fair use permitted", "unmatched", "matched no licence on it"),
    ("a negated mention", "project", "Not MIT, all rights reserved", "approved", "This records a mention only"),
    ("proprietary", "project", "All rights reserved", "unmatched", "MIT: The MIT License"),
    ("no answer", "project", "", "not_checked", None),
    ("community is not asked", "community", "MIT", "not_checked", None),
]

# What the repository host reported, as Fetch the README leaves it. These rows give no licence
# answer, so only the repository line is under test.
# (case, repo_licence_status, repo_licence, host_kind, expected repo_licence_osi, text shown)
REPO_CASES = [
    ("repo spdx id", "found", "MIT", "github", "approved", "GitHub reports it: MIT, which is on the OSI list"),
    ("repo -only suffix", "found", "GPL-3.0-only", "generic", "approved", "which is on the OSI list"),
    ("repo gitlab lowercase", "found", "gpl-3.0", "gitlab", "approved", "GitLab reports it: gpl-3.0, which is on the OSI list"),
    ("repo plus suffix", "found", "GPL-2.0+", "generic", "approved", "which is on the OSI list"),
    ("repo near miss", "found", "BSD-3-Clause-Clear", "github", "unmatched", "which is not on the OSI list"),
    ("repo dual licence", "found", "CC-BY-4.0, MIT", "generic", "approved", "which is on the OSI list"),
    ("repo has no licence", "none", "", "github", "not_checked", "GitHub found no licence file"),
    ("repo licence unknown", "unidentified", "", "gitlab", "not_checked", "could not identify"),
    ("repo not read", "not_read", "", "generic", "not_checked", None),
]
REPO_LINE = "Licence file in the repository"


def build(workdir):
    rows = [{"json": {
        "slug": f"case-{i}",
        "collective_name": case,
        "description": "",
        "applicant_kind": kind,
        "answers": json.dumps({"responses": {"licence": licence}}),
        "repo_url": "",
        "readme": "",
    }} for i, (case, kind, licence, _, _) in enumerate(CASES)]
    rows += [{"json": {
        "slug": f"repo-{i}",
        "collective_name": case,
        "description": "",
        "applicant_kind": "project",
        "answers": json.dumps({"responses": {}}),
        "repo_url": "https://example.org/owner/name",
        "host_kind": host_kind,
        "repo_licence_status": status,
        "repo_licence": licence,
        "readme": "",
    }} for i, (case, status, licence, host_kind, _, _) in enumerate(REPO_CASES)]

    def code(node_id, name, js, x):
        return {"id": node_id, "name": name, "type": "n8n-nodes-base.code",
                "typeVersion": 2, "position": [x, 0],
                "parameters": {"mode": "runOnceForAllItems", "jsCode": js}}

    workflow = {
        "id": harness.WORKFLOW_ID,
        "name": "licence check test",
        "settings": {"executionOrder": "v1"},
        "nodes": [
            {"id": "trigger", "name": "Manual", "type": "n8n-nodes-base.manualTrigger",
             "typeVersion": 1, "position": [0, 0], "parameters": {}},
            code("rows", "Rows", "return " + json.dumps(rows) + ";", 200),
            code("check", CHECK, harness.node_code(CHECK), 400),
            code("render", RENDER, harness.node_code(RENDER), 600),
        ],
        "connections": {
            "Manual": {"main": [[{"node": "Rows", "type": "main", "index": 0}]]},
            "Rows": {"main": [[{"node": CHECK, "type": "main", "index": 0}]]},
            CHECK: {"main": [[{"node": RENDER, "type": "main", "index": 0}]]},
        },
    }
    with open(os.path.join(workdir, "workflow.json"), "w") as handle:
        json.dump(workflow, handle)


def report(execution):
    result = execution["data"]["resultData"]
    if result.get("error"):
        raise SystemExit("execution failed: " + json.dumps(result["error"])[:1000])
    items = [i["json"] for i in result["runData"][RENDER][0]["data"]["main"][0]]
    got = {item["slug"]: item for item in items}

    failures = 0
    print(f"{'case':<26} {'licence_osi':<12} result")
    print("-" * 52)
    for i, (case, _, _, expect, shown) in enumerate(CASES):
        item = got.get(f"case-{i}", {})
        status = item.get("licence_osi", "MISSING")
        message = (item.get("request") or {}).get("messages", [{}, {}])[1].get("content", "")
        ok = status == expect and (LINE in message if shown else LINE not in message)
        if shown:
            ok = ok and shown in message
        failures += 0 if ok else 1
        print(f"{case:<26} {status:<12} {'pass' if ok else 'FAIL   expected ' + expect}")
    for i, (case, _, _, _, expect, shown) in enumerate(REPO_CASES):
        item = got.get(f"repo-{i}", {})
        status = item.get("repo_licence_osi", "MISSING")
        message = (item.get("request") or {}).get("messages", [{}, {}])[1].get("content", "")
        ok = status == expect and (shown in message if shown else REPO_LINE not in message)
        failures += 0 if ok else 1
        print(f"{case:<26} {status:<12} {'pass' if ok else 'FAIL   expected ' + expect}")
    total = len(CASES) + len(REPO_CASES)
    print("-" * 52)
    print(f"{total - failures}/{total} passed")
    return failures


def main():
    with tempfile.TemporaryDirectory(prefix="ose-licence-check-") as workdir:
        build(workdir)
        # Render summary request reads $env.AI_MODEL, which a fresh container refuses.
        failures = report(harness.run(workdir, env=("N8N_BLOCK_ENV_ACCESS_IN_NODE=false",
                                                     "AI_MODEL=test-model")))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
