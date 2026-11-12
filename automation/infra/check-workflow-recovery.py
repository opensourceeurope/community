#!/usr/bin/env python3
"""Post to Slack when a workflow that failed runs successfully again.

`ops — alert Slack when any apply workflow fails` posts the moment a run
fails, and nothing ever said that the next run worked. A sweep that failed
once on an Open Collective outage and succeeded an hour later looked, from the
channel, exactly like one that was still broken. This script closes that loop.

It reads the n8n executions of every exported workflow that names an error
workflow, the same set the failure alert covers, and walks the runs that ended
since its last look, oldest first:

  a failed run      opens a failure, or adds to the one already open
  a successful run  closes an open failure and posts that the workflow
                    recovered, with how many runs failed and since when

Runs started by hand are skipped both ways, because the failure alert never
fires for one. A run still going, or one waiting on a form page, is read once
it ends: the walk orders runs by when they stopped, not by when they started.

The first look at a workflow posts nothing. It records where the history ends
and opens a failure only if the latest run failed, so installing this never
replays old alerts.

What it remembers is a JSON file, by default
~/.local/state/ose-workflow-recovery/state.json. Deleting it costs at most the
recovery message of a failure open at that moment.

It exits:

  0  it looked, and posted every recovery it found
  1  Slack refused a recovery message, which is retried on the next run
  2  the check itself could not run, such as a refused API key

An n8n that does not answer, or answers 5xx, exits 0 and moves nothing on, so
the next run reads the same history. The deploy restarts n8n now and then, and
a check that ran into a restart would otherwise alert about nothing. A refused
key exits 2, which fails the unit and ose-alert@ posts it.

The key needs `execution:list` on top of the deploy's scopes, and the Slack bot
token is the one alert-slack.sh reads, from ~/.slack-bot-token (mode 600). The
channel is SLACK_CHANNEL from the compose .env.

Reads the instance URL from N8N_API_URL, or builds it from N8N_HOST in the
box's .env, and the API key from N8N_API_KEY, or from the file named by
N8N_API_KEY_FILE, by default ~/.n8n-api-key. The same key the deploy uses.

    check-workflow-recovery.py [repo root]
"""
import importlib.util
import json
import os
import re
import stat
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

EXPORTS = "automation/n8n"
BOX_ENV = "automation/infra/.env"
PAGE = 100

FAILED = ("error", "crashed")
SUCCEEDED = ("success",)
BY_HAND = ("manual",)

HERE = os.path.dirname(os.path.abspath(__file__))
# Loading the sibling modules would otherwise write a __pycache__ into the
# box's checkout, and deploy.sh refuses to deploy over an untracked file.
sys.dont_write_bytecode = True


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sync = _load("sync_workflows", "sync-workflows.py")
inactive = _load("check_inactive_workflows", "check-inactive-workflows.py")


def unreachable(error):
    """An n8n that is down or restarting, rather than one refusing the key."""
    text = str(error)
    return text.startswith("cannot reach") or re.match(r"HTTP 5\d\d ", text) is not None


def executions(base, key, workflow_id):
    """The latest page of a workflow's runs, without their data."""
    path = "/api/v1/executions?" + urllib.parse.urlencode(
        {"workflowId": workflow_id, "limit": PAGE, "includeData": "false"})
    try:
        return sync.call(base, key, "GET", path).get("data", [])
    except sync.ApiError as error:
        if str(error).startswith("HTTP 403 "):
            raise sync.ApiError("%s. The key needs the execution:list scope" % error)
        raise


def ended(run):
    return run.get("stoppedAt") or ""


def watched(exports):
    """Exported workflow names whose failures reach the failure alert."""
    return set(name for name, (_, export) in exports.items()
               if (export.get("settings") or {}).get("errorWorkflow"))


def walk(previous, runs):
    """New state for one workflow, and the recovery it found, if any.

    `previous` is None on the first look. A recovery is the closed failure plus
    the run that closed it. Two recoveries in one interval post the last one,
    and none when the workflow failed again after it, since the alert for that
    failure is the newer news.
    """
    done = sorted((r for r in runs
                   if r.get("status") in FAILED + SUCCEEDED
                   and r.get("mode") not in BY_HAND and ended(r)),
                  key=lambda r: (ended(r), int(r["id"])))
    if previous is None:
        latest = done[-1] if done else None
        failure = None
        if latest and latest["status"] in FAILED:
            failure = {"first_id": latest["id"], "first_at": ended(latest), "count": 1}
        return {"seen_until": ended(latest) if latest else "", "failure": failure}, None

    seen_until = previous.get("seen_until") or ""
    failure = previous.get("failure")
    recovery = None
    for run in done:
        if ended(run) <= seen_until:
            continue
        if run["status"] in FAILED:
            if failure:
                failure = dict(failure, count=failure["count"] + 1)
            else:
                failure = {"first_id": run["id"], "first_at": ended(run), "count": 1}
        elif failure:
            recovery = (failure, run)
            failure = None
        seen_until = ended(run)
    if failure:
        recovery = None
    return {"seen_until": seen_until, "failure": failure}, recovery


def esc(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def slack_time(stamp):
    """A Slack date token, shown in each reader's own timezone."""
    moment = datetime.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    fallback = moment.strftime("%Y-%m-%d %H:%M UTC")
    return "<!date^%d^{date_short_pretty} {time}|%s>" % (moment.timestamp(), fallback)


def message(base, name, workflow_id, failure, run):
    count = failure["count"]
    runs = "1 failed run" if count == 1 else "%d failed runs" % count
    return "\n".join([
        ":white_check_mark: *%s* recovered" % esc(name),
        "Succeeded %s after %s since %s" % (slack_time(ended(run)), runs, slack_time(failure["first_at"])),
        "Execution: %s/workflow/%s/executions/%s" % (base, workflow_id, run["id"]),
    ])


def slack_settings(root):
    path = os.path.expanduser(os.environ.get("SLACK_TOKEN_FILE", "~/.slack-bot-token"))
    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode not in (0o600, 0o400):
        raise ValueError("%s is mode %o, so others can read it: chmod 600 %s" % (path, mode, path))
    with open(path) as handle:
        token = "".join(handle.read().split())
    if not token:
        raise ValueError("%s is empty" % path)
    channel = ""
    with open(os.path.join(root, BOX_ENV)) as handle:
        for line in handle:
            m = re.match(r"^\s*SLACK_CHANNEL\s*=\s*(.*)$", line)
            if m:
                channel = m.group(1).strip().strip("\"'")
    if not channel:
        raise ValueError("no SLACK_CHANNEL in %s" % BOX_ENV)
    return token, channel


def post(token, channel, text):
    url = os.environ.get("SLACK_API", "https://slack.com/api/chat.postMessage")
    body = json.dumps({"channel": channel, "text": text, "unfurl_links": False}).encode("utf-8")
    request = urllib.request.Request(url, body, method="POST")
    request.add_header("Authorization", "Bearer " + token)
    request.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError) as error:
        return "could not reach Slack: %s" % getattr(error, "reason", error)
    return None if data.get("ok") else "Slack refused the message: %s" % data.get("error", "unknown error")


def state_path():
    return os.path.expanduser(os.environ.get(
        "OSE_RECOVERY_STATE", "~/.local/state/ose-workflow-recovery/state.json"))


def load_state(path):
    try:
        with open(path) as handle:
            return json.load(handle)
    except FileNotFoundError:
        return {}


def save_state(path, state):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    with os.fdopen(fd, "w") as handle:
        json.dump(state, handle, indent=1, sort_keys=True)
    os.replace(tmp, path)


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "..")
    path = state_path()
    try:
        base = inactive.base_url(root)
        key = inactive.read_key()
        names = watched(sync.load_exports(os.path.join(root, EXPORTS)))
        state = load_state(path)
        live = [w for w in sync.list_workflows(base, key)
                if w["name"] in names and not w.get("isArchived")]
        found = [(w, walk(state.get(w["id"]), executions(base, key, w["id"]))) for w in live]
    except sync.ApiError as exc:
        if unreachable(exc):
            print("n8n did not answer, so nothing was read. The next run reads the same history: %s" % exc)
            return 0
        print("the workflow recovery check could not run: %s" % exc)
        return 2
    except (OSError, ValueError) as exc:
        print("the workflow recovery check could not run: %s" % exc)
        return 2

    refused = []
    slack = None
    for workflow, (after, recovery) in found:
        if recovery:
            try:
                slack = slack or slack_settings(root)
            except (OSError, ValueError) as exc:
                print("the workflow recovery check could not post: %s" % exc)
                return 2
            problem = post(slack[0], slack[1], message(base, workflow["name"], workflow["id"], *recovery))
            if problem:
                # Keep the old state, so the next run finds this recovery again.
                refused.append("%s: %s" % (workflow["name"], problem))
                continue
            print("posted: %s recovered with execution %s" % (workflow["name"], recovery[1]["id"]))
        state[workflow["id"]] = after

    open_now = sorted(w["name"] for w, (after, _) in found if after["failure"])
    save_state(path, state)
    for name in open_now:
        print("still failing: %s" % name)
    for line in refused:
        print(line)
    if refused:
        return 1
    print("Read %d workflows, %d failing." % (len(found), len(open_now)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
