#!/usr/bin/env python3
"""Runs automation/scripts/create-data-table.py against a fake n8n data table
API on localhost and checks what it sends: the whole table when none exists,
only the missing columns when one does, nothing on a dry run, and nothing
removed or changed.

  python3 automation/test/create-data-table.py
"""
import http.server
import importlib.util
import json
import os
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "scripts", "create-data-table.py")

spec = importlib.util.spec_from_file_location("create_data_table", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
DOCUMENTED = module.documented_columns(module.DOC)


class Fake(http.server.BaseHTTPRequestHandler):
    tables = []      # [{"id", "name", "columns": [...]}]
    writes = []      # (method, path, body)

    def log_message(self, *args):
        pass

    def reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.headers.get("X-N8N-API-KEY") != "test-key":
            return self.reply(401, {"message": "unauthorized"})
        if self.path.startswith("/api/v1/data-tables?"):
            return self.reply(200, {"data": [{"id": t["id"], "name": t["name"]} for t in Fake.tables]})
        for table in Fake.tables:
            if self.path == "/api/v1/data-tables/%s/columns" % table["id"]:
                return self.reply(200, table["columns"])
        return self.reply(404, {"message": "not found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Fake.writes.append(("POST", self.path, body))
        self.reply(201, {"id": "new-id"})


def run(*args):
    env = dict(os.environ, N8N_API_URL="http://127.0.0.1:%d" % PORT, N8N_API_KEY="test-key")
    env.pop("N8N_API_KEY_FILE", None)
    return subprocess.run([sys.executable, "-B", SCRIPT] + list(args),
                          env=env, capture_output=True, text=True)


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
PORT = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
failures = []


def check(case, condition, detail):
    if not condition:
        failures.append("FAIL [%s] %s" % (case, detail))


case = "no table: the dry run sends nothing"
Fake.tables, Fake.writes = [], []
result = run("--dry-run")
check(case, result.returncode == 0, "exit %d: %s" % (result.returncode, result.stderr))
check(case, Fake.writes == [], "sent %s" % Fake.writes)

case = "no table: the whole table is created from the document"
Fake.writes = []
result = run()
check(case, result.returncode == 0, "exit %d: %s" % (result.returncode, result.stderr))
check(case, len(Fake.writes) == 1 and Fake.writes[0][1] == "/api/v1/data-tables",
      "expected one create, sent %s" % [w[1] for w in Fake.writes])
if Fake.writes:
    body = Fake.writes[0][2]
    check(case, body.get("name") == "ose_applications", "wrong name %s" % body.get("name"))
    check(case, body.get("columns") == DOCUMENTED, "columns differ from the document")
    check(case, {"name": "answers", "type": "string"} in body.get("columns", []),
          "the String (JSON) column answers is missing")

case = "a table missing two columns: only those two are added"
present = [dict(c, id="c%d" % i) for i, c in enumerate(DOCUMENTED[:-2])]
present.append({"id": "x", "name": "kept_by_hand", "type": "string"})
Fake.tables = [{"id": "t1", "name": "ose_applications", "columns": present}]
Fake.writes = []
result = run()
check(case, result.returncode == 0, "exit %d: %s" % (result.returncode, result.stderr))
check(case, [w[2] for w in Fake.writes] == DOCUMENTED[-2:],
      "expected the last two documented columns, sent %s" % [w[2] for w in Fake.writes])
check(case, all(w[1] == "/api/v1/data-tables/t1/columns" for w in Fake.writes),
      "a write went somewhere else: %s" % [w[1] for w in Fake.writes])
check(case, "left alone: kept_by_hand" in result.stdout, "the extra column was not reported")

case = "a complete table: nothing is sent"
Fake.tables = [{"id": "t1", "name": "ose_applications",
                "columns": [dict(c, id="c%d" % i) for i, c in enumerate(DOCUMENTED)]}]
Fake.writes = []
result = run()
check(case, result.returncode == 0 and Fake.writes == [], "sent %s" % Fake.writes)
check(case, "every documented column is present" in result.stdout, "no all-present line")

server.shutdown()
for failure in failures:
    print(failure)
if failures:
    print("%d check(s) failed" % len(failures))
    sys.exit(1)
print("create-data-table: all cases passed")
