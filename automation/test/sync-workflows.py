#!/usr/bin/env python3
"""Runs automation/infra/sync-workflows.py against a fake n8n workflow API on
localhost and checks what it sends: a new export is created inactive, nothing is
created while a live workflow has no export, since that is what a rename in git
leaves behind, an archived workflow does not count as one, a dry run sends
nothing, and a key without the create scope says so.

  python3 automation/test/sync-workflows.py
"""
import copy
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "infra", "sync-workflows.py")


def workflow(name, node="Start"):
    return {"name": name,
            "nodes": [{"id": "n-" + node, "name": node, "type": "n8n-nodes-base.noOp",
                       "typeVersion": 1, "position": [0, 0], "parameters": {}}],
            "connections": {}, "settings": {"timezone": "Europe/Berlin"}}


class Fake(http.server.BaseHTTPRequestHandler):
    live = []        # [{"id", "active", "isArchived", ...workflow}]
    writes = []      # (method, path, body)
    create_status = 200

    def log_message(self, *args):
        pass

    def reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        return json.loads(self.rfile.read(int(self.headers["Content-Length"])))

    def do_GET(self):
        if self.headers.get("X-N8N-API-KEY") != "test-key":
            return self.reply(401, {"message": "unauthorized"})
        if self.path.startswith("/api/v1/workflows?"):
            # Two pages whenever there is more than one workflow, so a sync that
            # stops at the first page misses the rest and the cases notice.
            if "cursor=page2" in self.path:
                return self.reply(200, {"data": Fake.live[1:], "nextCursor": None})
            more = "page2" if len(Fake.live) > 1 else None
            return self.reply(200, {"data": Fake.live[:1], "nextCursor": more})
        for item in Fake.live:
            if self.path == "/api/v1/workflows/%s" % item["id"]:
                return self.reply(200, item)
        return self.reply(404, {"message": "not found"})

    def do_POST(self):
        body = self.body()
        Fake.writes.append(("POST", self.path, body))
        if Fake.create_status != 200:
            return self.reply(Fake.create_status, {"message": "Forbidden"})
        self.reply(200, dict(body, id="new-id", active=False))

    def do_PUT(self):
        body = self.body()
        Fake.writes.append(("PUT", self.path, body))
        for item in Fake.live:
            if self.path == "/api/v1/workflows/%s" % item["id"]:
                item.update(body)
                return self.reply(200, item)
        return self.reply(404, {"message": "not found"})


def live(name, wid, archived=False, node="Start"):
    item = copy.deepcopy(workflow(name, node))
    item.update(id=wid, active=True, isArchived=archived)
    return item


def run(exports, *args):
    directory = tempfile.mkdtemp()
    for index, export in enumerate(exports):
        with open(os.path.join(directory, "w%d.json" % index), "w") as handle:
            json.dump(export, handle)
    env = dict(os.environ, N8N_API_URL="http://127.0.0.1:%d" % PORT, N8N_API_KEY="test-key")
    env.pop("N8N_API_KEY_FILE", None)
    try:
        return subprocess.run([sys.executable, "-B", SCRIPT, "--dir", directory] + list(args),
                              env=env, capture_output=True, text=True)
    finally:
        shutil.rmtree(directory)


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
PORT = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
failures = []


def check(case, condition, detail):
    if not condition:
        failures.append("FAIL [%s] %s" % (case, detail))


def reset(*items, create_status=200):
    Fake.live, Fake.writes, Fake.create_status = list(items), [], create_status


case = "a new export is created, inactive, and nothing else is written"
reset(live("apply 1", "id-1"))
result = run([workflow("apply 1"), workflow("ops new")])
posts = [w for w in Fake.writes if w[0] == "POST"]
check(case, result.returncode == 0, "exit %d: %s" % (result.returncode, result.stderr))
check(case, len(posts) == 1 and posts[0][2]["name"] == "ops new", "posts %s" % posts)
check(case, posts and "active" not in posts[0][2], "sent an active flag: %s" % posts)
check(case, [w for w in Fake.writes if w[0] == "PUT"] == [], "updated %s" % Fake.writes)
check(case, "created, inactive" in result.stdout, "stdout: %s" % result.stdout)

case = "a live workflow no export names blocks every creation and is named"
reset(live("apply 1", "id-1"), live("apply 2 old name", "id-2"))
result = run([workflow("apply 1"), workflow("apply 2 new name")])
check(case, result.returncode == 1, "exit %d" % result.returncode)
check(case, [w for w in Fake.writes if w[0] == "POST"] == [], "posted %s" % Fake.writes)
check(case, "'apply 2 old name'" in result.stderr, "stderr: %s" % result.stderr)

case = "the block still updates the exports that do match"
reset(live("apply 1", "id-1"), live("apply 2 old name", "id-2"))
result = run([workflow("apply 1", node="Changed"), workflow("apply 2 new name")])
check(case, result.returncode == 1, "exit %d" % result.returncode)
check(case, [w[1] for w in Fake.writes if w[0] == "PUT"] == ["/api/v1/workflows/id-1"],
      "writes %s" % Fake.writes)
check(case, [w for w in Fake.writes if w[0] == "POST"] == [], "posted %s" % Fake.writes)

case = "a workflow on the second page of the listing still blocks a creation"
reset(live("apply 1", "id-1"), live("apply 1", "id-1b", archived=True),
      live("only on the instance", "id-3"))
result = run([workflow("apply 1"), workflow("ops new")])
check(case, result.returncode == 1, "exit %d" % result.returncode)
check(case, [w for w in Fake.writes if w[0] == "POST"] == [], "posted %s" % Fake.writes)
check(case, "'only on the instance'" in result.stderr, "stderr: %s" % result.stderr)

case = "two live workflows with one name are refused, and neither is touched"
reset(live("apply 1", "id-1"), live("apply 1", "id-1c"))
result = run([workflow("apply 1", node="Changed")])
check(case, result.returncode == 1, "exit %d" % result.returncode)
check(case, Fake.writes == [], "wrote %s" % Fake.writes)

case = "an archived workflow with no export does not block a creation"
reset(live("apply 1", "id-1"), live("gone", "id-9", archived=True))
result = run([workflow("apply 1"), workflow("ops new")])
check(case, result.returncode == 0, "exit %d: %s" % (result.returncode, result.stderr))
check(case, len([w for w in Fake.writes if w[0] == "POST"]) == 1, "writes %s" % Fake.writes)

case = "an empty instance gets every export"
reset()
result = run([workflow("apply 1"), workflow("apply 2")])
check(case, result.returncode == 0, "exit %d: %s" % (result.returncode, result.stderr))
check(case, sorted(w[2]["name"] for w in Fake.writes if w[0] == "POST") == ["apply 1", "apply 2"],
      "writes %s" % Fake.writes)

case = "a dry run reports the creation and sends nothing"
reset(live("apply 1", "id-1"))
result = run([workflow("apply 1"), workflow("ops new")], "--dry-run")
check(case, result.returncode == 0, "exit %d: %s" % (result.returncode, result.stderr))
check(case, Fake.writes == [], "sent %s" % Fake.writes)
check(case, "would create, inactive" in result.stdout, "stdout: %s" % result.stdout)

case = "a key without the create scope fails and says which scope"
reset(live("apply 1", "id-1"), create_status=403)
result = run([workflow("apply 1"), workflow("ops new")])
check(case, result.returncode == 1, "exit %d" % result.returncode)
check(case, "workflow:create" in result.stderr, "stderr: %s" % result.stderr)

server.shutdown()
for failure in failures:
    print(failure)
if failures:
    sys.exit(1)
print("sync-workflows: all cases passed")
