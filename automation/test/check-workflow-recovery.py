#!/usr/bin/env python3
"""Runs automation/infra/check-workflow-recovery.py against a fake n8n API and
a fake Slack on localhost: the first look posts nothing, a success after a
failure posts one escaped recovery and never again, failures are counted
across runs, runs started by hand are skipped, a run that ends late is read by
when it stopped, a failure that comes back in the same interval posts nothing,
a refused Slack post is retried, and only workflows wired to the failure alert
are read. An n8n that does not answer moves nothing on, while a key without
the execution scope, or a token others can read, is a check that could not
run.

    python3 automation/test/check-workflow-recovery.py
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
SCRIPT = os.path.join(os.path.dirname(HERE), "infra", "check-workflow-recovery.py")
PYCACHE = os.path.join(os.path.dirname(HERE), "infra", "__pycache__")

SWEEP = "apply 1b — catch-up <!channel> & co"
REVIEW = "apply 2 — AI review"
HANDLER = "ops — alert Slack when any apply workflow fails"


def export(name, error_workflow):
    settings = {"errorWorkflow": "h1"} if error_workflow else {}
    return {"name": name, "connections": {}, "settings": settings,
            "nodes": [{"id": "t", "name": "Trigger", "type": "n8n-nodes-base.scheduleTrigger",
                       "typeVersion": 1, "position": [0, 0], "parameters": {}}]}


def run_(rid, status, stopped, mode="trigger"):
    return {"id": str(rid), "status": status, "mode": mode, "finished": status == "success",
            "startedAt": stopped, "stoppedAt": stopped}


class Fake(http.server.BaseHTTPRequestHandler):
    workflows = []
    runs = {}
    down = False
    drop_n8n = False
    drop_slack_for = None
    no_status = False
    executions_forbidden = False
    slack_ok = True
    posted = []
    read = []

    def log_message(self, *args):
        pass

    def reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def drop(self):
        # A reply that breaks off: urllib raises RemoteDisconnected, not URLError.
        self.close_connection = True
        self.connection.close()

    def do_GET(self):
        if Fake.drop_n8n and self.path.startswith("/api/v1/executions?"):
            return self.drop()
        if Fake.down:
            return self.reply(503, {"message": "Service unavailable"})
        if self.headers.get("X-N8N-API-KEY") != "test-key":
            return self.reply(401, {"message": "unauthorized"})
        if self.path.startswith("/api/v1/workflows?"):
            return self.reply(200, {"data": Fake.workflows, "nextCursor": None})
        if self.path.startswith("/api/v1/executions?"):
            if Fake.executions_forbidden:
                return self.reply(403, {"message": "Forbidden"})
            wid = self.path.split("workflowId=")[1].split("&")[0]
            Fake.read.append(wid)
            newest_first = sorted(Fake.runs.get(wid, []), key=lambda r: -int(r["id"]))
            if Fake.no_status:
                newest_first = [dict((k, v) for k, v in r.items() if k != "status") for r in newest_first]
            return self.reply(200, {"data": newest_first, "nextCursor": None})
        return self.reply(404, {"message": "not found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.headers.get("Authorization") != "Bearer xoxb-test":
            return self.reply(200, {"ok": False, "error": "invalid_auth"})
        if Fake.drop_slack_for and Fake.drop_slack_for in body["text"]:
            return self.drop()
        if not Fake.slack_ok:
            return self.reply(200, {"ok": False, "error": "not_in_channel"})
        Fake.posted.append(body)
        return self.reply(200, {"ok": True})


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
        for label, name, wired in (("intake-sweep.json", SWEEP, True),
                                   ("review.json", REVIEW, True),
                                   ("ops-alert-on-failure.json", HANDLER, False)):
            with open(os.path.join(root, "automation", "n8n", label), "w") as handle:
                json.dump(export(name, wired), handle)
        with open(os.path.join(root, "automation", "infra", ".env"), "w") as handle:
            handle.write("SLACK_CHANNEL=ose-alerts\n")
        token = os.path.join(root, "token")
        with open(token, "w") as handle:
            handle.write("xoxb-test\n")
        os.chmod(token, 0o600)
        state = os.path.join(root, "state", "state.json")

        Fake.workflows = [
            {"id": "s1", "name": SWEEP, "active": True},
            {"id": "r1", "name": REVIEW, "active": True},
            {"id": "h1", "name": HANDLER, "active": True},
            {"id": "old", "name": REVIEW, "active": False, "isArchived": True},
        ]

        def run(env=None):
            Fake.posted = []
            Fake.read = []
            full = dict(os.environ, N8N_API_URL=url, N8N_API_KEY="test-key",
                        SLACK_TOKEN_FILE=token, SLACK_API=url + "/slack",
                        OSE_RECOVERY_STATE=state)
            full.update(env or {})
            result = subprocess.run([sys.executable, "-B", SCRIPT, root],
                                    capture_output=True, text=True, env=full)
            return result.returncode, result.stdout + result.stderr

        def saved():
            with open(state) as handle:
                return json.load(handle)

        # The sweep's latest run failed and the review's succeeded, long ago.
        Fake.runs = {
            "s1": [run_(10, "success", "2026-11-12T05:00:02.000Z"),
                   run_(11, "error", "2026-11-12T06:01:26.000Z")],
            "r1": [run_(20, "error", "2026-11-01T05:00:00.000Z"),
                   run_(21, "success", "2026-11-01T06:00:00.000Z")],
        }
        code, out = run()
        check("the first look exits 0", code == 0, out)
        check("the first look posts nothing, however the history ends", Fake.posted == [], out)
        check("only workflows wired to the failure alert are read, archived ones left out",
              sorted(Fake.read) == ["r1", "s1"], out)
        check("the first look opens a failure for a workflow whose latest run failed",
              saved()["s1"]["failure"]["first_id"] == "11" and saved()["r1"]["failure"] is None, out)
        check("it says which workflow is still failing", "still failing: %s" % SWEEP in out, out)

        # Another failure, a run started by hand that succeeded, then a real success.
        Fake.runs["s1"] += [run_(12, "crashed", "2026-11-12T07:00:03.000Z"),
                            run_(13, "success", "2026-11-12T07:30:00.000Z", mode="manual"),
                            run_(14, "success", "2026-11-12T08:00:02.000Z")]
        code, out = run()
        check("a recovery exits 0", code == 0, out)
        check("a success after a failure posts once", len(Fake.posted) == 1, out)
        text = Fake.posted[0]["text"] if Fake.posted else ""
        check("the post goes to SLACK_CHANNEL without a preview",
              Fake.posted and Fake.posted[0]["channel"] == "ose-alerts"
              and Fake.posted[0]["unfurl_links"] is False, out)
        check("the workflow name is escaped", "&lt;!channel&gt; &amp; co" in text and "<!channel>" not in text, text)
        check("it counts both failed runs, the crash included, and not the run by hand",
              "after 2 failed runs since <!date^%d^" % 1794463286 in text, text)
        check("the time falls back to UTC for a client that cannot localise it",
              "|2026-11-12 08:00 UTC>" in text, text)
        check("it links the run that succeeded", "%s/workflow/s1/executions/14" % url in text, text)
        check("the failure is closed", saved()["s1"]["failure"] is None, out)

        code, out = run()
        check("the same history posts nothing again", code == 0 and Fake.posted == [], out)

        # A form run that started before the failure and ended after it.
        Fake.runs["r1"] += [run_(22, "error", "2026-11-12T09:00:00.000Z"),
                            run_(18, "success", "2026-11-12T09:05:00.000Z")]
        code, out = run()
        check("a run is read by when it stopped, not by its id",
              len(Fake.posted) == 1 and "/workflow/r1/executions/18" in Fake.posted[0]["text"], out)
        check("one failed run is singular",
              Fake.posted and "after 1 failed run since" in Fake.posted[0]["text"], out)

        # Fails, works, fails again within one interval.
        Fake.runs["s1"] += [run_(15, "error", "2026-11-12T09:00:00.000Z"),
                            run_(16, "success", "2026-11-12T09:01:00.000Z"),
                            run_(17, "error", "2026-11-12T09:02:00.000Z")]
        code, out = run()
        check("a failure that came back in the same interval posts no recovery",
              code == 0 and Fake.posted == [], out)
        check("and stays open from the newest failure", saved()["s1"]["failure"]["first_id"] == "17", out)

        Fake.runs["s1"].append(run_(19, "success", "2026-11-12T10:00:00.000Z"))
        Fake.slack_ok = False
        code, out = run()
        check("a refused post exits 1 and says why", code == 1 and "not_in_channel" in out, out)
        check("a refused post keeps the failure open", saved()["s1"]["failure"]["first_id"] == "17", out)
        Fake.slack_ok = True
        code, out = run()
        check("the next run posts the recovery Slack refused",
              code == 0 and len(Fake.posted) == 1 and "executions/19" in Fake.posted[0]["text"], out)

        Fake.runs["s1"].append(run_(23, "error", "2026-11-12T11:00:00.000Z"))
        before = saved()
        Fake.down = True
        code, out = run()
        Fake.down = False
        check("an n8n that answers 503 exits 0", code == 0 and "did not answer" in out, out)
        check("and moves nothing on", saved() == before, out)

        code, out = run({"N8N_API_URL": "http://127.0.0.1:1"})
        check("an unreachable n8n exits 0 too", code == 0 and saved() == before, out)

        Fake.executions_forbidden = True
        code, out = run()
        Fake.executions_forbidden = False
        check("a key without the execution scope exits 2 and names the scope",
              code == 2 and "could not run" in out and "execution:list" in out, out)

        code, out = run({"N8N_API_KEY": "wrong-key"})
        check("a refused key exits 2", code == 2 and "HTTP 401" in out, out)

        Fake.runs["s1"].append(run_(24, "success", "2026-11-12T12:00:00.000Z"))
        os.chmod(token, 0o644)
        code, out = run()
        os.chmod(token, 0o600)
        check("a token others can read is refused, and the token never printed",
              code == 2 and "chmod 600" in out and "xoxb-test" not in out, out)
        check("and nothing it read is forgotten", saved() == before, out)
        code, out = run()
        check("the next run posts the recovery from what the aborted runs left unread",
              code == 0 and len(Fake.posted) == 1 and "executions/24" in Fake.posted[0]["text"], out)

        # Both workflows recover in one run, and Slack drops the second post.
        Fake.runs["s1"] += [run_(25, "error", "2026-11-12T13:00:00.000Z"),
                            run_(26, "success", "2026-11-12T13:10:00.000Z")]
        Fake.runs["r1"] += [run_(27, "error", "2026-11-12T13:00:00.000Z"),
                            run_(28, "success", "2026-11-12T13:10:00.000Z")]
        Fake.drop_slack_for = "/workflow/r1/"
        code, out = run()
        Fake.drop_slack_for = None
        check("a post that breaks off exits 1 rather than a traceback",
              code == 1 and "Traceback" not in out and "could not reach Slack" in out, out)
        check("the recovery posted before it was kept", len(Fake.posted) == 1
              and "executions/26" in Fake.posted[0]["text"] and saved()["s1"]["failure"] is None, out)
        code, out = run()
        check("the next run posts only the one that broke off",
              code == 0 and len(Fake.posted) == 1 and "executions/28" in Fake.posted[0]["text"], out)

        Fake.runs["s1"].append(run_(29, "error", "2026-11-12T14:00:00.000Z"))
        before = saved()
        Fake.drop_n8n = True
        code, out = run()
        Fake.drop_n8n = False
        check("an n8n reply that breaks off while reading runs exits 0 and moves nothing on",
              code == 0 and "did not answer" in out and saved() == before, out)

        Fake.no_status = True
        code, out = run()
        Fake.no_status = False
        check("runs without a status are a check that could not run, not silence",
              code == 2 and "no status" in out, out)

        # A workflow whose only run on the page is still going.
        Fake.runs["r1"] = [{"id": "30", "status": "running", "mode": "trigger",
                            "startedAt": "2026-11-12T15:00:00.000Z", "stoppedAt": None}]
        code, out = run()
        check("a page holding only a run still going is read, not refused",
              code == 0 and "no status" not in out, out)

        # A recovery is due, and the state cannot be written. The run still
        # going ends, so this case does not depend on the one above.
        Fake.runs["r1"] = [run_(30, "success", "2026-11-12T15:01:00.000Z")]
        run()
        Fake.runs["s1"].append(run_(31, "success", "2026-11-12T15:00:00.000Z"))
        before = saved()
        os.chmod(os.path.dirname(state), 0o500)
        try:
            code, out = run()
        finally:
            os.chmod(os.path.dirname(state), 0o700)
        check("a state that cannot be saved exits 2 and posts nothing, so nothing repeats",
              code == 2 and Fake.posted == [] and "Traceback" not in out, out)
        check("and the state is unchanged", saved() == before, out)
        code, out = run()
        check("once it can be saved the recovery posts once",
              code == 0 and len(Fake.posted) == 1 and "executions/31" in Fake.posted[0]["text"], out)
        code, out = run()
        check("and never again", code == 0 and Fake.posted == [], out)

        for label, content in (("not an object", "[]"),
                               ("not valid JSON", '{"s1": {"seen_until": "2026'),
                               ("an entry this check never writes", '{"s1": {"failure": {"count": "x"}}}')):
            with open(state, "w") as handle:
                handle.write(content)
            code, out = run()
            check("a state file that is %s exits 2 and says to delete it" % label,
                  code == 2 and "Delete it" in out and "Traceback" not in out, out)

        if os.path.isdir(PYCACHE):
            shutil.rmtree(PYCACHE)
        subprocess.run([sys.executable, SCRIPT, root], capture_output=True,
                       env=dict(os.environ, N8N_API_URL=url, N8N_API_KEY="test-key",
                                SLACK_TOKEN_FILE=token, SLACK_API=url + "/slack",
                                OSE_RECOVERY_STATE=state))
        check("a run without -B leaves no __pycache__ for the deploy to refuse",
              not os.path.isdir(PYCACHE), "found %s" % PYCACHE)
    finally:
        shutil.rmtree(root)
        server.shutdown()

    for failure in failures:
        print("FAIL: %s\n" % failure)
    if failures:
        return 1
    print("check-workflow-recovery.py: every case passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
