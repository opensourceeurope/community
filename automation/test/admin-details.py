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
             # Render summary request reads the model name, as it does in production.
             "-e", "N8N_BLOCK_ENV_ACCESS_IN_NODE=false", "-e", "AI_MODEL=test-model",
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
        executions.append(json.JSONDecoder().raw_decode(chunk[chunk.index("{"):])[0])
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
        "createdAt": "2019-03-01T09:00:00.000Z",
        "memberOf": {"totalCount": 3, "nodes": [{"account": {"slug": "sample-collective"}},
                                                {"account": {"slug": "other-a"}},
                                                {"account": {"slug": "other-b"}}]},
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
                         {"type": "MASTODON", "url": "https://mastodon.example/@anna"}],
         "createdAt": "2019-03-01T09:00:00.000Z", "adminOfOthers": 2},
        {"slug": "example-org", "name": "Example Org", "description": None, "company": None,
         "socialLinks": [{"type": "WEBSITE", "url": "https://example.org"}],
         "createdAt": None, "adminOfOthers": None},
    ], stored
    assert items[0]["applicant_email"] == "anna@example.org", items[0]["applicant_email"]


def check_intake_no_admins(execution):
    items = output(execution, "Extract new applications")
    assert len(items) == 1 and items[0]["oc_admins"] == "", items


# --- apply 2 ----------------------------------------------------------------------------

def row(slug, oc_admins, anchored=True):
    """A row as Store verdict returns it, reduced to what the admin reply reads."""
    return {"slug": slug, "oc_admins": oc_admins, "applied_at": "2026-11-03T10:00:00.000Z",
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
                     {"type": "WEBSITE", "url": "javascript:alert(1)"}],
     "createdAt": "2019-03-01T09:00:00.000Z", "adminOfOthers": 2},
    {"slug": "loud-admin", "name": "<!channel> Loud", "description": None, "company": None,
     "socialLinks": [], "createdAt": "2026-11-03T09:57:30.000Z", "adminOfOthers": 0},
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
    assert "_On Open Collective since March 2019 · admin of 2 other collectives_" in text, text
    assert ("_Profile created 2 minutes before the application · no description or links on"
            " the profile · admin of no other collective_") in text, text


def check_slack_nothing(execution):
    items = output(execution, "Render admin details thread reply")
    assert items == [], items


# --- the form ---------------------------------------------------------------------------

ADULT = "Are all admins of the collective at least 18 years old?"
OTHER = "Any other admins we did not list, and where do they live?"
FALLBACK = "In which country does each admin live?"
# The one spelling of a country question's label. Form page admins builds it, Form page 6
# builds it again to prefill, and Collect admin countries looks the answer up by it, so all
# three must carry this exact line or an answer is silently lost.
ADMIN_LABEL_JS = ("const adminName = (a) => String(a.name || '').replace(/[<>]/g, '').trim() "
                  "|| String(a.slug); const adminLabel = (a) => 'Where does ' + adminName(a) "
                  "+ ' (@' + String(a.slug) + ') live?';")


def label(name, slug):
    shown = name.replace("<", "").replace(">", "").strip() or slug
    return f"Where does {shown} (@{slug}) live?"


def check_label_spelling_shared():
    """Every node that builds or reads a country label spells it the same way."""
    for name, field in (("Form page admins", "jsonOutput"), ("Form page 6", "jsonOutput"),
                        ("Collect admin countries", "jsCode")):
        source = node("form-ose.json", name)["parameters"][field]
        assert ADMIN_LABEL_JS in source, f"{name} does not build the admin label the shared way"


def form_case(name, oc_admins, page_admins, page_six, check):
    return {"case": name, "export": "form-ose.json", "nodes": ["Collect admin countries"],
            "feeds": {"Read the stored answers again": [{"slug": "sample", "oc_admins": oc_admins}],
                      "Form page admins": [page_admins], "Form page 6": [page_six]},
            "check": check}


FORM_ADMINS = json.dumps([
    {"slug": "ann-example", "name": "Ann \"Q\" O'Neil", "socialLinks": []},
    {"slug": "bo-example", "name": "<b>Bo</b>", "socialLinks": []},
])
ANN = label("Ann \"Q\" O'Neil", "ann-example")
BO = label("<b>Bo</b>", "bo-example")


def collected(execution):
    items = output(execution, "Collect admin countries")
    assert len(items) == 1, items
    return items[0]


def check_countries_listed(execution):
    item = collected(execution)
    assert item["admin_countries"] == (
        "Ann \"Q\" O'Neil (@ann-example): Poland\n"
        "bBo/b (@bo-example): Germany\n"
        "Other admins: Cy, Spain"), item["admin_countries"]
    assert item["admins_adult"] == "Yes", item
    assert item["Contact email"] == "applicant@example.org", "page 6 answers were not passed on"


def check_countries_page_six_wins(execution):
    item = collected(execution)
    assert item["admin_countries"] == ("Ann \"Q\" O'Neil (@ann-example): Czechia\n"
                                       "bBo/b (@bo-example):"), item["admin_countries"]
    assert item["admins_adult"] == "No", item


def check_countries_fallback(execution):
    item = collected(execution)
    assert item["admin_countries"] == "Dee, Italy\nEd, France", item["admin_countries"]


def check_countries_duplicates(execution):
    item = collected(execution)
    assert item["admin_countries"] == ("Sam (@sam-one): Austria\n"
                                       "Sam (@sam-two): Belgium"), item["admin_countries"]


def check_submission_reply(execution):
    items = output(execution, "Render submission thread reply")
    assert len(items) == 1, items
    text = items[0]["text"]
    assert "• *All admins 18 or older:* Yes" in text, text
    assert "• *Where the admins live:*" in text, text
    assert "◦ &lt;!channel&gt; Ann (@ann-example): Poland" in text and "<!channel>" not in text, text
    assert "◦ Other admins: Cy, Spain" in text, text


SUBMITTED_ROW = {
    "slug": "sample", "org": "OSE", "collective_name": "Sample",
    "collective_url": "https://opencollective.com/sample",
    "slack_channel_id": "C0SAMPLE", "slack_thread_ts": "1730000000.000100",
    "answers": json.dumps({"page": 6, "responses": {"activities": "Parses files.",
                                                     "admins_adult": "Yes"}}),
    "admin_countries": "<!channel> Ann (@ann-example): Poland\nOther admins: Cy, Spain",
}


def check_summary_request(execution):
    items = output(execution, "Render summary request")
    assert len(items) == 1, items
    # The row rides along beside the request, and only request is sent to the model.
    request = json.dumps(items[0]["request"])
    assert "All admins said to be 18 or older" in request, request[:800]
    for leaked in ("Anna Example", "anna-example", "Poland", "Germany"):
        assert leaked not in request, f"{leaked} reached the model request"


SUMMARY_ROW = {
    "slug": "sample", "collective_name": "Sample", "description": "A sample library.",
    "applicant_kind": "project", "repo_url": "", "readme": "",
    "answers": json.dumps({"page": 6, "responses": {"activities": "Parses files.",
                                                     "admins_adult": "Yes"}}),
    "oc_admins": json.dumps([{"slug": "anna-example", "name": "Anna Example"}]),
    "admin_countries": "Anna Example (@anna-example): Poland\nOther admins: Bo, Germany",
}


CASES = [
    {"case": "summary-request", "export": "summary.json", "nodes": ["Render summary request"],
     "feeds": {"Feed": [SUMMARY_ROW]}, "check": check_summary_request},
    {"case": "submission-reply", "export": "form-ose.json",
     "nodes": ["Render submission thread reply"],
     "feeds": {"Record submission": [SUBMITTED_ROW]}, "check": check_submission_reply},
    form_case("countries-listed", FORM_ADMINS,
              {ADULT: "Yes", ANN: "Poland", BO: "Germany", OTHER: "Cy, Spain"},
              {"Contact email": "applicant@example.org", ADULT: "Yes", ANN: " Poland ",
               BO: "Germany", OTHER: "Cy, Spain"},
              check_countries_listed),
    form_case("countries-page6-wins", FORM_ADMINS,
              {ADULT: "Yes", ANN: "Poland", BO: "Germany"},
              {ADULT: "No", ANN: "Czechia", BO: "", OTHER: ""},
              check_countries_page_six_wins),
    form_case("countries-fallback", "",
              {ADULT: "Yes", FALLBACK: "Dee, Italy\nEd, France"},
              {ADULT: "Yes"}, check_countries_fallback),
    form_case("countries-duplicate-names",
              json.dumps([{"slug": "sam-one", "name": "Sam"}, {"slug": "sam-two", "name": "Sam"}]),
              {ADULT: "Yes", label("Sam", "sam-one"): "Austria", label("Sam", "sam-two"): "Belgium"},
              {ADULT: "Yes", label("Sam", "sam-one"): "Austria", label("Sam", "sam-two"): "Belgium"},
              check_countries_duplicates),
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
    check_label_spelling_shared()
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
