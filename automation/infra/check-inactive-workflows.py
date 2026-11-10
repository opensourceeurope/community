#!/usr/bin/env python3
"""Say whether every workflow git ships is switched on in n8n.

The deploy creates a workflow a merge brings, inactive, and never activates
one, because whether a workflow runs is a person's call. Nothing used to say
that a call was waiting, so a new scheduled workflow could sit switched off
for days while everyone assumed it ran. This script lists every workflow that
has an export in automation/n8n and is inactive on the instance. It exits:

  0  every exported workflow is active
  1  at least one is inactive, so activate it or archive it
  2  the check itself could not run

ose-inactive-workflows.service runs it hourly. A non-zero exit fails the unit,
and ose-alert@ posts the report to the alerts channel, once, then posts
recovered when every workflow is active again. So the report is written to be
read in Slack, and it holds names and ids, never a credential.

The error handler counts like any other workflow: n8n runs one only once it
has been activated and so published. An export with no workflow on the
instance is left out: creating it is the deploy's job, and the deploy reports
when it cannot.

ose-alert@ shows the last 15 journal lines and systemd adds a few of its own,
so each workflow takes one line and the instruction comes last, where a long
list cuts the oldest lines first.

Reads the instance URL from N8N_API_URL, or builds it from N8N_HOST in the
box's .env, and the API key from N8N_API_KEY, or from the file named by
N8N_API_KEY_FILE, by default ~/.n8n-api-key. The same key the deploy uses.

    check-inactive-workflows.py [repo root]
"""
import importlib.util
import os
import re
import sys

EXPORTS = "automation/n8n"
BOX_ENV = "automation/infra/.env"

HERE = os.path.dirname(os.path.abspath(__file__))
# Loading the sibling module would otherwise write a __pycache__ into the box's
# checkout, and deploy.sh refuses to deploy over an untracked file.
sys.dont_write_bytecode = True
_spec = importlib.util.spec_from_file_location("sync_workflows", os.path.join(HERE, "sync-workflows.py"))
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)


def base_url(root):
    url = os.environ.get("N8N_API_URL", "")
    if not url:
        with open(os.path.join(root, BOX_ENV)) as handle:
            for line in handle:
                m = re.match(r"^\s*N8N_HOST\s*=\s*(.*)$", line)
                if m:
                    url = "https://" + m.group(1).strip().strip("\"'")
    if not url or url == "https://":
        raise ValueError("no N8N_API_URL, and no N8N_HOST in %s" % BOX_ENV)
    return url.rstrip("/")


def read_key():
    key = os.environ.get("N8N_API_KEY", "")
    if key:
        return key
    path = os.path.expanduser(os.environ.get("N8N_API_KEY_FILE", "~/.n8n-api-key"))
    with open(path) as handle:
        key = handle.read().strip()
    if not key:
        raise ValueError("the API key file %s is empty" % path)
    return key


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "..")
    try:
        base = base_url(root)
        key = read_key()
        exports = sync.load_exports(os.path.join(root, EXPORTS))
        live = sync.list_workflows(base, key)
    except (OSError, ValueError, sync.ApiError) as exc:
        print("the inactive workflow check could not run: %s" % exc)
        return 2

    off = []
    for workflow in live:
        if workflow.get("isArchived") or workflow.get("active"):
            continue
        if workflow["name"] not in exports:
            continue
        off.append((workflow, os.path.basename(exports[workflow["name"]][0])))

    if not off:
        print("Every workflow in %s is active on %s." % (EXPORTS, base))
        return 0

    print("In git but switched off in n8n, so never run:")
    for workflow, label in sorted(off, key=lambda item: item[0]["name"]):
        since = (workflow.get("createdAt") or "")[:10] or "unknown"
        print("  %s (%s, created %s) %s/workflow/%s"
              % (workflow["name"], label, since, base, workflow["id"]))
    print("The deploy creates a new workflow inactive and leaves turning it on to a person. "
          "Activate each one in the editor, or archive it and delete its export if it should not run.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
