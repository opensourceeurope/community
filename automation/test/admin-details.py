#!/usr/bin/env python3
"""Runs the nodes that carry a collective's admins through the pipeline, in a real n8n.

Intake keeps the public admin list on the row, apply 2 posts it in the Slack thread,
and the form turns the per-admin answers into one `admin_countries` value. All three
handle text that an applicant or Open Collective controls, and the form reads answers
back under labels it computed itself, which no static check can see. So each case runs
the committed nodes, read out of the exports rather than copied, inside the n8n image
production runs, and asserts on what they return.

    python3 automation/test/admin-details.py

Needs docker. Prints one line per case and exits non-zero if any case failed.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXPORTS = os.path.join(ROOT, "automation", "n8n")
COMPOSE = os.path.join(ROOT, "automation", "infra", "docker-compose.yml")
MARKER = "=== admin-details case ==="


def image():
    """The pinned n8n image production runs, unless the caller overrides it."""
    override = os.environ.get("N8N_TEST_IMAGE")
    if override:
        return override
    for line in open(COMPOSE):
        match = re.match(r"^\s*image:\s*(docker\.n8n\.io/n8nio/n8n:\S+)\s*$", line)
        if match:
            return match.group(1)
    raise SystemExit(f"{COMPOSE}: no n8n image line found")


def node(export, name):
    """One node of a committed export."""
    for candidate in json.load(open(os.path.join(EXPORTS, export)))["nodes"]:
        if candidate["name"] == name:
            return candidate
    raise SystemExit(f"{export}: no node named {name!r}")


def workflow(index, export, names, feeds):
    """Manual trigger, one Code node per entry of feeds, then the named nodes in order.

    feeds maps a node name to the items that node returns, so a node under test that
    reads $('Store verdict') finds a stand-in of that name upstream of it.
    """
    nodes = [{"id": "trigger", "name": "Manual", "type": "n8n-nodes-base.manualTrigger",
              "typeVersion": 1, "position": [0, 0], "parameters": {}}]
    chain = ["Manual"]
    for offset, (name, items) in enumerate(feeds.items(), start=1):
        nodes.append({"id": f"feed{offset}", "name": name, "type": "n8n-nodes-base.code",
                      "typeVersion": 2, "position": [offset * 200, 0],
                      "parameters": {"mode": "runOnceForAllItems",
                                     "jsCode": "const items = " + json.dumps(items)
                                               + ";\nreturn items.map((json) => ({ json }));"}})
        chain.append(name)
    for offset, name in enumerate(names, start=len(chain)):
        copy = json.loads(json.dumps(node(export, name)))
        copy["id"] = f"test{offset}"
        copy["position"] = [offset * 200, 0]
        nodes.append(copy)
        chain.append(name)
    return {
        "id": f"admindetails{index:04d}", "name": f"admin details case {index}",
        "settings": {"executionOrder": "v1"}, "nodes": nodes,
        "connections": {a: {"main": [[{"node": b, "type": "main", "index": 0}]]}
                        for a, b in zip(chain, chain[1:])},
    }


def run_cases(cases):
    """Import one workflow per case, execute each, and return each case's run data."""
    with tempfile.TemporaryDirectory(prefix="ose-admin-details-") as workdir:
        flows = os.path.join(workdir, "workflows")
        state = os.path.join(workdir, "n8n-home")
        os.makedirs(flows)
        os.makedirs(state)
        for path in (workdir, flows, state):
            os.chmod(path, 0o777)
        ids = []
        for index, case in enumerate(cases):
            flow = workflow(index, case["export"], case["nodes"], case["feeds"])
            ids.append(flow["id"])
            json.dump(flow, open(os.path.join(flows, flow["id"] + ".json"), "w"))
        runs = " ; ".join(f"echo '{MARKER}' ; n8n execute --id={i} 2>/dev/null" for i in ids)
        command = (f"n8n import:workflow --separate --input=/data/workflows >/dev/null 2>&1 "
                   f"&& {runs}")
        result = subprocess.run(
            ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}",
             "-v", f"{workdir}:/data", "-v", f"{state}:/home/node/.n8n",
             "-e", "HOME=/home/node", "-e", "N8N_DIAGNOSTICS_ENABLED=false",
             "-e", "N8N_RUNNERS_ENABLED=true",
             "--entrypoint", "sh", image(), "-c", command],
            capture_output=True, text=True, timeout=1800)
    chunks = result.stdout.split(MARKER)[1:]
    if len(chunks) != len(cases):
        raise SystemExit("n8n did not run every case:\n" + (result.stderr or result.stdout)[-2000:])
    executions = []
    for chunk in chunks:
        if "{" not in chunk:
            executions.append({"error": chunk.strip()[-500:] or "no output"})
            continue
        executions.append(json.loads(chunk[chunk.index("{"):]))
    return executions


def output(execution, name):
    """The items a node returned, or raise with the execution's error."""
    result = execution.get("data", {}).get("resultData", {})
    if execution.get("error") or result.get("error"):
        raise AssertionError("execution failed: "
                             + json.dumps(execution.get("error") or result.get("error"))[:600])
    runs = result.get("runData", {}).get(name)
    if not runs:
        return []
    return [item["json"] for item in (runs[0]["data"]["main"][0] or [])]


# --- intake ---------------------------------------------------------------------------

def application(slug, admins):
    """One pending application in the shape the intake GraphQL query returns."""
    return {"createdAt": "2026-11-03T10:00:00.000Z", "message": "", "customData": {},
            "account": {"slug": slug, "name": "Sample", "description": "", "longDescription": "",
                        "socialLinks": [], "members": {"nodes": [{"account": a} for a in admins]}}}


def intake_case(name, admins, check):
    response = {"data": {"host": {"slug": "europe", "hostApplications": {
        "totalCount": 1, "nodes": [application("sample-collective", admins)]}}}}
    return {"case": name, "export": "intake-sweep.json", "nodes": ["Extract new applications"],
            "feeds": {"Fetch pending applications": [response]}, "check": check}


ANNA = {"slug": "anna-example", "name": "Anna Example", "description": "Maintainer.",
        "company": "Example Labs", "email": "anna@example.org",
        "socialLinks": [{"type": "GITHUB", "url": "https://github.com/anna-example"},
                        {"type": "MASTODON", "url": "https://mastodon.example/@anna"}]}
ORG = {"slug": "example-org", "name": "Example Org", "description": None,
       "socialLinks": [{"type": "WEBSITE", "url": "https://example.org"}]}


def check_intake_admins(execution):
    items = output(execution, "Extract new applications")
    assert len(items) == 1, items
    stored = items[0]["oc_admins"]
    assert "anna@example.org" not in stored, "an admin email reached oc_admins"
    assert json.loads(stored) == [
        {"slug": "anna-example", "name": "Anna Example", "description": "Maintainer.",
         "company": "Example Labs",
         "socialLinks": [{"type": "GITHUB", "url": "https://github.com/anna-example"},
                         {"type": "MASTODON", "url": "https://mastodon.example/@anna"}]},
        {"slug": "example-org", "name": "Example Org", "description": None, "company": None,
         "socialLinks": [{"type": "WEBSITE", "url": "https://example.org"}]},
    ], stored
    assert items[0]["applicant_email"] == "anna@example.org", items[0]["applicant_email"]


def check_intake_no_admins(execution):
    items = output(execution, "Extract new applications")
    assert len(items) == 1 and items[0]["oc_admins"] == "", items


# --- apply 2 ----------------------------------------------------------------------------

def row(slug, oc_admins, anchored=True):
    """A row as Store verdict returns it, reduced to what the admin reply reads."""
    return {"slug": slug, "oc_admins": oc_admins,
            "slack_channel_id": "C0SAMPLE" if anchored else "",
            "slack_thread_ts": "1730000000.000100" if anchored else ""}


def slack_case(name, rows, check):
    return {"case": name, "export": "review.json",
            "nodes": ["Render admin details thread reply"],
            "feeds": {"Store verdict": rows}, "check": check}


SLACK_ADMINS = json.dumps([
    {"slug": "anna-example", "name": "Anna Example", "description": "Maintainer of a <b>parser</b>.",
     "company": "Example Labs",
     "socialLinks": [{"type": "GITHUB", "url": "https://github.com/example"},
                     {"type": "WEBSITE", "url": "javascript:alert(1)"}]},
    {"slug": "loud-admin", "name": "<!channel> Loud", "description": None, "company": None,
     "socialLinks": []},
])


def check_slack_admins(execution):
    items = output(execution, "Render admin details thread reply")
    assert len(items) == 1, items
    item = items[0]
    assert item["channel"] == "C0SAMPLE" and item["thread_ts"] == "1730000000.000100", item
    text = item["text"]
    assert "*Admins of the collective*" in text, text
    assert "<https://opencollective.com/anna-example|@anna-example>" in text, text
    assert "Example Labs" in text, text
    assert "Maintainer of a &lt;b&gt;parser&lt;/b&gt;." in text, text
    assert "<https://github.com/example|GITHUB>" in text, text
    assert "|WEBSITE>" not in text and "<javascript:" not in text, text
    assert "javascript:alert(1)" in text, text
    assert "&lt;!channel&gt; Loud" in text and "<!channel>" not in text, text


def check_slack_nothing(execution):
    items = output(execution, "Render admin details thread reply")
    assert items == [], items


CASES = [
    intake_case("intake-admins", [ANNA, ORG], check_intake_admins),
    intake_case("intake-no-admins", [], check_intake_no_admins),
    slack_case("slack-admins", [row("sample-collective", SLACK_ADMINS)], check_slack_admins),
    slack_case("slack-no-admins", [row("a", ""), row("b", "[]"), row("c", "not json"),
                                   row("d", None), row("e", "{}")], check_slack_nothing),
    slack_case("slack-no-anchor", [row("sample-collective", SLACK_ADMINS, anchored=False)],
               check_slack_nothing),
]


def check_intake_copies_match():
    """Both intake workflows must extract applications identically."""
    for name in ("Fetch pending applications", "Extract new applications",
                 "Create application row"):
        a = node("intake-sweep.json", name)["parameters"]
        b = node("oc-events-intake.json", name)["parameters"]
        assert a == b, f"{name} differs between the two intake workflows"


def main():
    check_intake_copies_match()
    selected = [c for c in CASES if len(sys.argv) < 2 or c["case"] in sys.argv[1:]]
    executions = run_cases(selected)
    failures = 0
    for case, execution in zip(selected, executions):
        try:
            case["check"](execution)
            print(f"pass  {case['case']}")
        except (AssertionError, KeyError, TypeError, ValueError) as error:
            failures += 1
            print(f"FAIL  {case['case']}: {str(error)[:600]}")
    print(f"{len(selected) - failures}/{len(selected)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
