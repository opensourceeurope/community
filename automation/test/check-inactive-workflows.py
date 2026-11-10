#!/usr/bin/env python3
"""Runs automation/infra/check-inactive-workflows.py against a fake n8n
workflow API on localhost: an exported workflow that is inactive fails the
check and is named with its link, the error handler included, an active one,
an archived one and a workflow with no export do not, a long list keeps its
instruction in the lines the alert shows, the check leaves no __pycache__
behind, and an unreachable API or a
missing key is reported as a check that could not run. The report is what
ose-alert@ posts, so it has to stay within the 15 journal lines it shows.

    python3 automation/test/check-inactive-workflows.py
"""
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "infra", "check-inactive-workflows.py")
PYCACHE = os.path.join(os.path.dirname(HERE), "infra", "__pycache__")

DIGEST = "ops — weekly digest of open applications in Slack"
REVIEW = "apply 2 — AI review"
HANDLER = "ops — alert Slack when any apply workflow fails"


def export(name, trigger):
    return {"name": name, "connections": {}, "settings": {},
            "nodes": [{"id": "t", "name": "Trigger", "type": trigger,
                       "typeVersion": 1, "position": [0, 0], "parameters": {}}]}


def live(wid, name, active, archived=False):
    return {"id": wid, "name": name, "active": active, "isArchived": archived,
            "createdAt": "2026-11-03T17:10:50.115Z"}


class Fake(http.server.BaseHTTPRequestHandler):
    workflows = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.headers.get("X-N8N-API-KEY") != "test-key":
            code, body = 401, {"message": "unauthorized"}
        elif self.path.startswith("/api/v1/workflows?"):
            # Two pages, so a check that stops at the first misses the rest.
            if "cursor=page2" in self.path:
                code, body = 200, {"data": Fake.workflows[1:], "nextCursor": None}
            else:
                code, body = 200, {"data": Fake.workflows[:1],
                                   "nextCursor": "page2" if len(Fake.workflows) > 1 else None}
        else:
            code, body = 404, {"message": "not found"}
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)


failures = []


def check(label, condition, output):
    if not condition:
        failures.append("%s\n--- output ---\n%s" % (label, output))


def main():
    server = http.server.HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d" % server.server_port

    root = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(root, "automation", "n8n"))
        os.makedirs(os.path.join(root, "automation", "infra"))
        for label, name, trigger in (("ops-weekly-digest.json", DIGEST, "n8n-nodes-base.scheduleTrigger"),
                                     ("review.json", REVIEW, "n8n-nodes-base.scheduleTrigger"),
                                     ("ops-alert-on-failure.json", HANDLER, "n8n-nodes-base.errorTrigger")):
            with open(os.path.join(root, "automation", "n8n", label), "w") as handle:
                json.dump(export(name, trigger), handle)

        def run(workflows, env=None):
            Fake.workflows = workflows
            full = dict(os.environ, N8N_API_URL=url, N8N_API_KEY="test-key")
            full.update(env or {})
            result = subprocess.run([sys.executable, "-B", SCRIPT, root],
                                    capture_output=True, text=True, env=full)
            return result.returncode, result.stdout + result.stderr

        code, out = run([live("r1", REVIEW, True), live("d1", DIGEST, True), live("h1", HANDLER, True)])
        check("every workflow active exits 0", code == 0 and "Every workflow" in out, out)

        code, out = run([live("r1", REVIEW, True), live("d1", DIGEST, False), live("h1", HANDLER, False),
                         live("x1", "a workflow nobody exported", False),
                         live("old", REVIEW, False, archived=True)])
        check("an inactive exported workflow exits 1", code == 1, out)
        check("the report names it, its export, its creation date and its link",
              "%s (ops-weekly-digest.json, created 2026-11-03) %s/workflow/d1" % (DIGEST, url) in out, out)
        check("an inactive error handler is reported, since n8n only runs a published one",
              "%s/workflow/h1" % url in out, out)
        check("a workflow with no export is not reported", "nobody exported" not in out, out)
        check("an archived copy is not reported", "/workflow/old" not in out, out)
        check("the report fits the 15 lines the alert shows", len(out.strip().splitlines()) <= 11, out)

        # A first install creates every workflow inactive. The alert keeps the
        # last lines, so the instruction must be the last thing printed.
        many = [live("m%d" % i, DIGEST if i == 0 else "x%d" % i, False) for i in range(12)]
        code, out = run(many)
        check("a long list ends with what to do", code == 1 and out.strip().splitlines()[-1].startswith("The deploy creates"), out)

        if os.path.isdir(PYCACHE):
            shutil.rmtree(PYCACHE)
        subprocess.run([sys.executable, SCRIPT, root], capture_output=True,
                       env=dict(os.environ, N8N_API_URL=url, N8N_API_KEY="test-key"))
        check("a run without -B leaves no __pycache__ for the deploy to refuse",
              not os.path.isdir(PYCACHE), "found %s" % PYCACHE)

        code, out = run([live("d1", DIGEST, False)], {"N8N_API_KEY": "wrong-key"})
        check("a refused key exits 2", code == 2 and "could not run" in out and "HTTP 401" in out, out)

        code, out = run([], {"N8N_API_URL": "http://127.0.0.1:1"})
        check("an unreachable API exits 2", code == 2 and "could not run" in out, out)

        missing = os.path.join(root, "no-such-key")
        code, out = run([], {"N8N_API_KEY": "", "N8N_API_KEY_FILE": missing})
        check("a missing key file exits 2", code == 2 and "could not run" in out, out)

        with open(os.path.join(root, "automation", "infra", ".env"), "w") as handle:
            handle.write("N8N_HOST=127.0.0.1:%d\n" % server.server_port)
        code, out = run([], {"N8N_API_URL": ""})
        check("without N8N_API_URL it builds an https URL from N8N_HOST",
              code == 2 and "https://127.0.0.1:%d" % server.server_port in out, out)
    finally:
        shutil.rmtree(root)
        server.shutdown()

    for failure in failures:
        print("FAIL: %s\n" % failure)
    if failures:
        return 1
    print("check-inactive-workflows.py: every case passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
