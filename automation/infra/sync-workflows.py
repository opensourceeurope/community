#!/usr/bin/env python3
"""Push the workflow exports in automation/n8n into a running n8n instance.

Each export goes to the workflow of the same name, updated in place. That
workflow keeps its id, so every Execute Workflow node that calls it keeps
working. Running the sync twice changes nothing the second time.

It never activates or deactivates a workflow. It never adds a second workflow
under a name the instance already has, and it never reads or writes a
credential. The invariants of the exports themselves belong to
automation/scripts/check-workflows.py, which deploy-workflows.sh runs first.

Reads the instance URL from N8N_API_URL and the API key from N8N_API_KEY, or
from the file named by N8N_API_KEY_FILE. deploy-workflows.sh is how this runs
on the VPS. Call it directly to sync a checkout somewhere else.
"""
import argparse
import collections
import glob
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

# The public API rejects every other top-level key as read-only, `id` and
# `active` among them. Sending only these four is therefore also the reason an
# update cannot change whether a workflow runs.
SENT_KEYS = ("name", "nodes", "connections", "settings")

EXECUTE_WORKFLOW_TYPE = "n8n-nodes-base.executeWorkflow"

# n8n derives these two from the workflow itself and, in the words of its own
# OpenAPI document at /api/v1/openapi.yml, "any value sent when creating or
# updating a workflow is ignored". A GET returns them all the same. Comparing
# them would report a difference no write can settle, and four of the exports
# carry binaryMode, so every run would push them again.
DERIVED_SETTINGS = ("binaryMode", "credentialResolverId")


class ApiError(Exception):
    pass


def call(base, key, method, path, body=None):
    request = urllib.request.Request(base + path, method=method)
    request.add_header("X-N8N-API-KEY", key)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, data, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        raise ApiError("HTTP %d on %s %s: %s" % (error.code, method, path, detail))
    except urllib.error.URLError as error:
        raise ApiError("cannot reach %s: %s" % (base, error.reason))


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def writable_settings(settings):
    return dict((k, v) for k, v in (settings or {}).items() if k not in DERIVED_SETTINGS)


def load_exports(directory):
    """Every export in the directory, keyed by workflow name."""
    exports = collections.OrderedDict()
    paths = sorted(glob.glob(os.path.join(directory, "*.json")))
    if not paths:
        raise ApiError("no workflow exports in %s" % directory)
    for path in paths:
        with open(path) as handle:
            try:
                workflow = json.load(handle)
            except ValueError as error:
                raise ApiError("%s: not valid JSON: %s" % (path, error))
        check_export(path, workflow)
        name = workflow["name"]
        if name in exports:
            raise ApiError("%s: another export already uses the name %r" % (path, name))
        exports[name] = (path, workflow)
    return exports


def check_export(path, workflow):
    for key in ("name", "nodes", "connections"):
        if key not in workflow:
            raise ApiError("%s: no %r key, so this is not a workflow export" % (path, key))
    if "credentials" in workflow:
        raise ApiError("%s: carries a top-level 'credentials' key. Credentials live in "
                       "n8n's own store and are never deployed from git" % path)
    for node in workflow["nodes"]:
        for kind, reference in (node.get("credentials") or {}).items():
            extra = sorted(set(reference) - {"id", "name"})
            if extra:
                raise ApiError("%s: node %r carries %s in its %s credential. An export "
                               "names a credential by id and nothing else"
                               % (path, node["name"], extra, kind))


def list_workflows(base, key):
    workflows, cursor = [], None
    while True:
        path = "/api/v1/workflows?limit=100"
        if cursor:
            path += "&cursor=" + urllib.parse.quote(cursor)
        page = call(base, key, "GET", path)
        workflows.extend(page.get("data", []))
        cursor = page.get("nextCursor")
        if not cursor:
            return workflows


def differences(live, wanted):
    """The keys that differ, and the node names behind a difference in nodes."""
    changed = [key for key in SENT_KEYS
               if canonical(live.get(key)) != canonical(wanted[key])]
    if "settings" in changed and (writable_settings(live.get("settings"))
                                  == wanted["settings"]):
        changed.remove("settings")
    if "nodes" not in changed:
        return changed, []
    before = dict((node["id"], node) for node in live.get("nodes", []))
    after = dict((node["id"], node) for node in wanted["nodes"])
    names = ["+" + after[i]["name"] for i in after if i not in before]
    names += ["-" + before[i]["name"] for i in before if i not in after]
    names += [after[i]["name"] for i in after
              if i in before and canonical(after[i]) != canonical(before[i])]
    return changed, sorted(names)


def hand_off_warnings(exports, live_ids):
    """Execute Workflow nodes whose target id is not on this instance.

    A hand-off points at a workflow id, not a name, so an export written against
    another instance calls a workflow that is not there. n8n reports nothing:
    the caller succeeds and the stage it handed off never runs.
    """
    warnings = []
    for name, (path, export) in exports.items():
        for node in export["nodes"]:
            if node.get("type") != EXECUTE_WORKFLOW_TYPE:
                continue
            target = node.get("parameters", {}).get("workflowId", {})
            wanted_id = target.get("value") if isinstance(target, dict) else target
            if wanted_id and wanted_id not in live_ids:
                warnings.append("%s: node %r calls workflow id %s, which is not on this "
                                "instance" % (os.path.basename(path), node["name"], wanted_id))
    return warnings


def read_key():
    key = os.environ.get("N8N_API_KEY", "")
    if key:
        return key
    key_file = os.environ.get("N8N_API_KEY_FILE", "")
    if not key_file:
        sys.exit("error: no API key. Set N8N_API_KEY or N8N_API_KEY_FILE")
    try:
        with open(os.path.expanduser(key_file)) as handle:
            key = handle.read().strip()
    except OSError as error:
        sys.exit("error: cannot read the API key file: %s" % error)
    if not key:
        sys.exit("error: the API key file %s is empty" % key_file)
    return key


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", default="automation/n8n",
                        help="directory holding the workflow exports (default: %(default)s)")
    parser.add_argument("--base-url", default=os.environ.get("N8N_API_URL", ""),
                        help="instance base URL, without /api/v1 (default: $N8N_API_URL)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change, and change nothing")
    parser.add_argument("--allow-create", action="store_true",
                        help="add an export the instance does not have yet, inactive")
    arguments = parser.parse_args()

    base = arguments.base_url.rstrip("/")
    if not base:
        sys.exit("error: no instance URL. Set N8N_API_URL or pass --base-url")
    key = read_key()

    try:
        exports = load_exports(arguments.dir)
        live_workflows = list_workflows(base, key)
    except ApiError as error:
        sys.exit("error: %s" % error)

    by_name = collections.defaultdict(list)
    for workflow in live_workflows:
        if not workflow.get("isArchived"):
            by_name[workflow["name"]].append(workflow)

    verb = "would update" if arguments.dry_run else "updated"
    counts = collections.Counter()
    problems = []
    print("%d exports from %s against %s%s"
          % (len(exports), arguments.dir, base, " (dry run)" if arguments.dry_run else ""))

    for name, (path, export) in exports.items():
        label = os.path.basename(path)
        matches = by_name.get(name, [])
        wanted = {"name": name, "nodes": export["nodes"],
                  "connections": export["connections"],
                  "settings": writable_settings(export.get("settings"))}

        if len(matches) > 1:
            problems.append("%s: %d workflows on the instance are named %r, so there is "
                            "no single one to update" % (label, len(matches), name))
            counts["failed"] += 1
            continue

        if not matches:
            if not arguments.allow_create:
                problems.append("%s: no workflow named %r on the instance. Re-run with "
                                "--allow-create to add it, inactive" % (label, name))
                counts["failed"] += 1
                continue
            if arguments.dry_run:
                print("  would create, inactive  %s  %s" % (label, name))
                counts["created"] += 1
                continue
            try:
                created = call(base, key, "POST", "/api/v1/workflows", wanted)
            except ApiError as error:
                problems.append("%s: %s" % (label, error))
                counts["failed"] += 1
                continue
            print("  created, inactive  %s  id %s" % (label, created["id"]))
            counts["created"] += 1
            continue

        live = matches[0]
        state = "active" if live.get("active") else "inactive"
        changed, nodes = differences(live, wanted)
        if not changed:
            print("  unchanged  %s  [%s]" % (label, state))
            counts["unchanged"] += 1
            continue

        detail = ", ".join(changed)
        if nodes:
            detail += " (%d: %s)" % (len(nodes), ", ".join(nodes))
        print("  %s  %s  [%s]  %s" % (verb, label, state, detail))
        if arguments.dry_run:
            counts["changed"] += 1
            continue
        try:
            call(base, key, "PUT", "/api/v1/workflows/%s" % live["id"], wanted)
            after = call(base, key, "GET", "/api/v1/workflows/%s" % live["id"])
        except ApiError as error:
            problems.append("%s: %s" % (label, error))
            counts["failed"] += 1
            continue
        # Report a state change rather than correct it. Whether a workflow runs
        # is the operator's decision and this script does not get to make it.
        if bool(after.get("active")) != bool(live.get("active")):
            problems.append("%s: the update turned this workflow from %s to %s. Set it "
                            "back in the n8n UI"
                            % (label, state, "active" if after.get("active") else "inactive"))
            counts["failed"] += 1
            continue
        counts["changed"] += 1

    summary = ", ".join("%d %s" % (counts[k], k) for k in
                        ("changed", "created", "unchanged", "failed") if counts[k])
    others = len([w for w in live_workflows if w["name"] not in exports])
    print("%s. %d other workflows on the instance left alone"
          % (summary or "nothing to do", others))

    for warning in hand_off_warnings(exports, set(w["id"] for w in live_workflows)):
        print("warning: %s" % warning, file=sys.stderr)
    for problem in problems:
        print("error: %s" % problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
