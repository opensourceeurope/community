#!/usr/bin/env python3
"""Checks, inside a real n8n, that the form link apply 3 emails is the only one apply 4 accepts.

`Render reminder` in apply 3 signs the link with an HMAC of the slug, and `Derive slug`
in apply 4 refuses a submission whose token does not match the collective it names. The
two are written separately in two workflows, so this runs both, as committed, in the
pinned n8n image with the same environment the compose file gives production. Each
case takes the link out of the rendered email, changes it the way an attacker or a
careless applicant would, and submits it to its own copy of `Derive slug`.

A third case runs `Derive slug` with no secret at all and expects it to throw rather
than accept anything.

    python3 automation/test/form-link-token.py

Needs docker. Prints one line per case and exits non-zero if any case failed.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
N8N = os.path.join(ROOT, "automation", "n8n")
COMPOSE = os.path.join(ROOT, "automation", "infra", "docker-compose.yml")
SECRET = "test-secret-0123456789abcdef-0123456789"
FORM_BASE = "https://apply.example.org"
URL_LABEL = "Your collective's Open Collective URL"

ROWS = [
    {"slug": "alpha-project", "collective_url": "https://opencollective.com/alpha-project",
     "collective_name": "Alpha", "org": "OSE", "applicant_email": "alpha@example.org"},
    {"slug": "beta", "collective_url": "", "collective_name": "Beta", "org": "OSE",
     "applicant_email": "beta@example.org"},
]

# name, which row's email, what the visitor submits (JS, given `url` and `t` parsed
# from that email's link), expected url_ok, expected slug
CASES = [
    ("the emailed link is accepted", 0, "({url, t})", True, "alpha-project"),
    ("another collective's URL with this token", 0,
     "({url: 'https://opencollective.com/victim', t})", False, ""),
    ("the URL alone, with no token", 0, "({url, t: ''})", False, ""),
    ("a token with one character changed", 0,
     "({url, t: (t[0] === 'A' ? 'B' : 'A') + t.slice(1)})", False, ""),
    ("the slug in capitals keeps its token", 0,
     "({url: url.replace('alpha-project', 'ALPHA-PROJECT'), t})", True, "alpha-project"),
    ("a link with no URL, typed in by hand", 1,
     "({url: 'https://opencollective.com/beta', t})", True, "beta"),
    ("beta's token on alpha's URL", 1,
     "({url: 'https://opencollective.com/alpha-project', t})", False, ""),
]


def image():
    override = os.environ.get("N8N_TEST_IMAGE")
    if override:
        return override
    for line in open(COMPOSE):
        match = re.match(r"^\s*image:\s*(docker\.n8n\.io/n8nio/n8n:\S+)\s*$", line)
        if match:
            return match.group(1)
    raise SystemExit(f"{COMPOSE}: no n8n image line found")


def compose_value(name):
    """A literal value the compose file gives the n8n container."""
    for line in open(COMPOSE):
        match = re.match(rf"^\s*{name}:\s*\"?([^\"\s]+)\"?\s*$", line)
        if match:
            return match.group(1)
    raise SystemExit(f"{COMPOSE}: {name} is not set for n8n")


def node(workflow_file, name):
    for n in json.load(open(os.path.join(N8N, workflow_file)))["nodes"]:
        if n["name"] == name:
            return n
    raise SystemExit(f"{workflow_file}: no node named {name!r}")


def code(node_id, name, js, x, y=0, mode="runOnceForAllItems"):
    return {"id": node_id, "name": name, "type": "n8n-nodes-base.code", "typeVersion": 2,
            "position": [x, y], "parameters": {"mode": mode, "jsCode": js}}


def build(workdir):
    reminder = node("followup.json", "Render reminder")
    derive_js = node("form-ose.json", "Derive slug")["parameters"]["jsCode"]
    rows_js = "return " + json.dumps([{"json": r} for r in ROWS]) + ";"

    nodes = [
        {"id": "trigger", "name": "Manual", "type": "n8n-nodes-base.manualTrigger",
         "typeVersion": 1, "position": [0, 0], "parameters": {}},
        code("rows", "Rows", rows_js, 200),
        dict(reminder, id="reminder", position=[400, 0]),
    ]
    connections = {
        "Manual": {"main": [[{"node": "Rows", "type": "main", "index": 0}]]},
        "Rows": {"main": [[{"node": "Render reminder", "type": "main", "index": 0}]]},
        "Render reminder": {"main": [[]]},
    }
    for i, (_, row, submit, _, _) in enumerate(CASES):
        # Pull this row's link out of the email body and parse its query by hand: the
        # sandbox has no URL or URLSearchParams.
        case_js = f"""
const body = $('Render reminder').all()[{row}].json.body;
const link = body.split('\\n').find(l => l.startsWith({json.dumps(FORM_BASE)}));
const query = {{}};
for (const pair of (link.split('?')[1] || '').split('&')) {{
  const [k, v = ''] = pair.split('=');
  query[decodeURIComponent(k)] = decodeURIComponent(v);
}}
const url = query[{json.dumps(URL_LABEL)}] || '';
const t = query.t || '';
const s = {submit};
return [{{ json: {{ 'Contact email': 'x@example.org', {json.dumps(URL_LABEL)}: s.url, t: s.t }} }}];
"""
        nodes.append(code(f"case{i}", f"Case {i}", case_js, 600, i * 160))
        nodes.append(code(f"derive{i}", f"Derive slug {i}", derive_js, 800, i * 160))
        connections["Render reminder"]["main"][0].append(
            {"node": f"Case {i}", "type": "main", "index": 0})
        connections[f"Case {i}"] = {"main": [[{"node": f"Derive slug {i}", "type": "main", "index": 0}]]}

    workflow = {"id": "formlinktokentest", "name": "form link token test",
                "settings": {"executionOrder": "v1"}, "nodes": nodes, "connections": connections}
    with open(os.path.join(workdir, "workflow.json"), "w") as handle:
        json.dump(workflow, handle)

    lone = {"id": "formlinknosecret", "name": "form link no secret",
            "settings": {"executionOrder": "v1"},
            "nodes": [
                {"id": "t", "name": "Manual", "type": "n8n-nodes-base.manualTrigger",
                 "typeVersion": 1, "position": [0, 0], "parameters": {}},
                code("in", "Submission", "return [{ json: { t: 'x', "
                     f"{json.dumps(URL_LABEL)}: 'https://opencollective.com/beta' }} }}];", 200),
                code("d", "Derive slug", derive_js, 400),
            ],
            "connections": {
                "Manual": {"main": [[{"node": "Submission", "type": "main", "index": 0}]]},
                "Submission": {"main": [[{"node": "Derive slug", "type": "main", "index": 0}]]},
            }}
    with open(os.path.join(workdir, "nosecret.json"), "w") as handle:
        json.dump(lone, handle)


def execute(workdir, workflow_file, workflow_id, secret):
    state = os.path.join(workdir, f"home-{workflow_id}")
    os.makedirs(state, exist_ok=True)
    os.chmod(workdir, 0o777)
    os.chmod(state, 0o777)
    command = (f"n8n import:workflow --input=/data/{workflow_file} >/dev/null 2>&1 && "
               f"n8n execute --id={workflow_id} 2>/dev/null")
    env = {
        "HOME": "/home/node", "N8N_DIAGNOSTICS_ENABLED": "false", "N8N_RUNNERS_ENABLED": "true",
        "N8N_BLOCK_ENV_ACCESS_IN_NODE": "false",
        "NODE_FUNCTION_ALLOW_BUILTIN": compose_value("NODE_FUNCTION_ALLOW_BUILTIN"),
        "FORM_URL_OSE": FORM_BASE, "FORM_LINK_SECRET": secret,
        "DRY_RUN": "true", "DRY_RUN_RECIPIENT": "dry@example.org",
    }
    args = ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}",
            "-v", f"{workdir}:/data", "-v", f"{state}:/home/node/.n8n"]
    for key, value in env.items():
        args += ["-e", f"{key}={value}"]
    args += ["--entrypoint", "sh", image(), "-c", command]
    result = subprocess.run(args, capture_output=True, text=True, timeout=900)
    if "{" not in result.stdout:
        raise SystemExit("n8n produced no execution result:\n" + (result.stderr or result.stdout)[-2000:])
    # A failed execution prints more after its JSON, so read the first object only.
    parsed, _ = json.JSONDecoder().raw_decode(result.stdout[result.stdout.index("{"):])
    return parsed["data"]["resultData"]


def main():
    failures = 0
    with tempfile.TemporaryDirectory(prefix="ose-form-link-") as workdir:
        build(workdir)
        result = execute(workdir, "workflow.json", "formlinktokentest", SECRET)
        if result.get("error"):
            raise SystemExit("execution failed: " + json.dumps(result["error"])[:1500])
        runs = result["runData"]
        bodies = [i["json"]["body"] for i in runs["Render reminder"][0]["data"]["main"][0]]
        if any("t=" not in b for b in bodies):
            print("FAIL the reminder link carries no token")
            failures += 1

        print(f"{'case':<46} {'url_ok':<7} {'slug':<15} result")
        print("-" * 76)
        for i, (name, _, _, want_ok, want_slug) in enumerate(CASES):
            out = runs[f"Derive slug {i}"][0]["data"]["main"][0][0]["json"]
            ok = out["url_ok"] is want_ok and out["slug"] == want_slug
            failures += 0 if ok else 1
            print(f"{name:<46} {str(out['url_ok']):<7} {out['slug'] or '-':<15} "
                  f"{'pass' if ok else f'FAIL, wanted {want_ok} {want_slug or chr(45)}'}")

        lone = execute(workdir, "nosecret.json", "formlinknosecret", "")
        threw = bool(lone.get("error")) and "FORM_LINK_SECRET" in json.dumps(lone["error"])
        failures += 0 if threw else 1
        print(f"{'no secret: Derive slug throws':<46} {'-':<7} {'-':<15} {'pass' if threw else 'FAIL'}")
        print("-" * 76)
        total = len(CASES) + 1
        print(f"{total - failures}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
