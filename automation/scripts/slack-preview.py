#!/usr/bin/env python3
"""Renders the Slack messages the workflows post, and links each one to Block Kit Builder.

Every Slack post in the pipeline is fed by a Code node that builds its text, so reading
the export shows the template and never the message a reviewer gets: the escaping, the
emoji, the bold, the line breaks and the continuation of a long reply all happen when the
node runs. This runs those nodes, as committed, in a throwaway container on the image
production runs, with stand-in upstream nodes that return the sample rows in the fixture.
Nothing is posted anywhere.

By default it renders only the nodes whose code differs from origin/main, once as they are
there and once as they are here, so a change can be read before and after.

    python3 automation/scripts/slack-preview.py
    python3 automation/scripts/slack-preview.py --node "Render AI review thread reply"
    python3 automation/scripts/slack-preview.py --all --out /path/to/out

Needs docker and git. Writes an index.html into --out with every message, its raw text,
and a link that opens it in Slack's Block Kit Builder, then prints that path.
"""
import argparse
import glob
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXPORTS = os.path.join(ROOT, "automation", "n8n")
FIXTURE = os.path.join(ROOT, "automation", "test", "fixtures", "slack-preview.json")
COMPOSE = os.path.join(ROOT, "automation", "infra", "docker-compose.yml")
ENV_EXAMPLE = os.path.join(ROOT, "automation", ".env.example")
BUILDER = "https://app.slack.com/block-kit-builder/#"
# A section block holds at most 3000 characters of text. The workflows post plain text,
# which may run longer, so a message past this is shown as several sections in a row.
SECTION_LIMIT = 3000
MARKER = "=== slack-preview run ==="


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


def exports_at(ref):
    """{file name: export} for every workflow export, here or at a git ref."""
    if ref is None:
        return {os.path.basename(path): json.load(open(path))
                for path in sorted(glob.glob(os.path.join(EXPORTS, "*.json")))}
    listing = subprocess.run(["git", "-C", ROOT, "ls-tree", "--name-only", ref, "automation/n8n/"],
                             capture_output=True, text=True, check=True).stdout.split()
    return {os.path.basename(path): json.loads(subprocess.run(
                ["git", "-C", ROOT, "show", f"{ref}:{path}"],
                capture_output=True, text=True, check=True).stdout)
            for path in listing if path.endswith(".json")}


def render_nodes(export):
    """The Code nodes whose output a Slack node posts, by name. Reactions post no text."""
    by_name = {node["name"]: node for node in export["nodes"]}
    found = {}
    for source, outputs in export.get("connections", {}).items():
        for branch in outputs.get("main", []):
            for edge in branch or []:
                target = by_name.get(edge["node"])
                if not (target and "slack" in target["type"].lower()
                        and target["parameters"].get("operation", "post") == "post"):
                    continue
                # A post fed by anything else would drop out of the preview and out of
                # CI without a word, so it stops the run instead.
                if by_name.get(source, {}).get("type") != "n8n-nodes-base.code":
                    raise SystemExit(f"{export.get('name')}: {target['name']!r} is fed by "
                                     f"{source!r}, which is not a Code node, so it cannot "
                                     "be previewed")
                found[source] = by_name[source]
    return found


def referenced_nodes(code):
    """Every node name the code reads with $('...')."""
    return set(re.findall(r"\$\(['\"]([^'\"]+)['\"]\)", code))


def env_flags():
    """The documented non-secret defaults, so a node reading $env sees what production does."""
    flags = ["-e", "SLACK_CHANNEL=C0SAMPLE"]
    for line in open(ENV_EXAMPLE):
        match = re.match(r"^([A-Z_]+)=(.*\S)", line)
        if match and match.group(1) not in ("SLACK_CHANNEL", "AI_API_KEY"):
            flags += ["-e", f"{match.group(1)}={match.group(2)}"]
    return flags


def workflow(index, node, feeds):
    """Manual trigger, one stand-in per feed in order, then the render node.

    The last feed is the render node's input. The others are there for $('...') to find.
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
    copy = json.loads(json.dumps(node))
    copy["id"] = "target"
    copy["position"] = [len(chain) * 200, 0]
    nodes.append(copy)
    chain.append(node["name"])
    return {
        "id": f"slackpreview{index:04d}", "name": f"slack preview {index}",
        "settings": {"executionOrder": "v1"}, "nodes": nodes,
        "connections": {a: {"main": [[{"node": b, "type": "main", "index": 0}]]}
                        for a, b in zip(chain, chain[1:])},
    }


def run_all(jobs):
    """Import every job's workflow, execute each in one container, return each one's texts."""
    with tempfile.TemporaryDirectory(prefix="ose-slack-preview-") as workdir:
        flows = os.path.join(workdir, "workflows")
        state = os.path.join(workdir, "n8n-home")
        os.makedirs(flows)
        os.makedirs(state)
        for path in (workdir, flows, state):
            os.chmod(path, 0o777)
        for index, job in enumerate(jobs):
            flow = workflow(index, job["node"], job["feeds"])
            job["id"] = flow["id"]
            json.dump(flow, open(os.path.join(flows, flow["id"] + ".json"), "w"))
        runs = " ; ".join(f"echo '{MARKER}' ; n8n execute --id={job['id']} 2>/dev/null"
                          for job in jobs)
        command = (f"n8n import:workflow --separate --input=/data/workflows >/dev/null 2>&1 "
                   f"&& {runs}")
        result = subprocess.run(
            ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}",
             "-v", f"{workdir}:/data", "-v", f"{state}:/home/node/.n8n",
             "-e", "HOME=/home/node", "-e", "N8N_DIAGNOSTICS_ENABLED=false",
             "-e", "N8N_RUNNERS_ENABLED=true", "-e", "N8N_BLOCK_ENV_ACCESS_IN_NODE=false",
             "-e", "GENERIC_TIMEZONE=Europe/Berlin", *env_flags(),
             "--entrypoint", "sh", image(), "-c", command],
            capture_output=True, text=True, timeout=1800)
    chunks = result.stdout.split(MARKER)[1:]
    if len(chunks) != len(jobs):
        raise SystemExit("n8n did not run every node:\n" + (result.stderr or result.stdout)[-2000:])
    for job, chunk in zip(jobs, chunks):
        job["texts"], job["error"] = [], None
        if "{" not in chunk:
            job["error"] = chunk.strip()[-500:] or "no output"
            continue
        execution = json.JSONDecoder().raw_decode(chunk[chunk.index("{"):])[0]
        data = execution.get("data", {}).get("resultData", {})
        if data.get("error"):
            job["error"] = data["error"].get("message") or json.dumps(data["error"])[:500]
            continue
        runs = data.get("runData", {}).get(job["node"]["name"]) or []
        items = (runs[0]["data"]["main"][0] or []) if runs else []
        job["texts"] = [item["json"].get("text", "") for item in items]


def sections(text):
    """The text cut into section-sized pieces on line breaks, so none exceeds the limit."""
    pieces, current = [], ""
    for line in text.split("\n"):
        while len(line) > SECTION_LIMIT:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(line[:SECTION_LIMIT])
            line = line[SECTION_LIMIT:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > SECTION_LIMIT:
            pieces.append(current)
            current = line
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def builder_url(text):
    """A Block Kit Builder link that shows the text as Slack renders mrkdwn."""
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": piece}}
              for piece in sections(text)]
    return BUILDER + urllib.parse.quote(json.dumps({"blocks": blocks}, ensure_ascii=False), safe="")


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Slack preview</title>
<style>
:root {{ --bg: #fff; --fg: #1d1c1d; --muted: #616061; --line: #ddd; --card: #f8f8f8; --link: #1264a3; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg: #1a1d21; --fg: #d1d2d3; --muted: #9a9b9d; --line: #35373b; --card: #222529; --link: #4fa8e8; }}
}}
body {{ margin: 0; background: var(--bg); color: var(--fg); font: 15px/1.5 system-ui, sans-serif; }}
main {{ max-width: 1200px; margin: 0 auto; padding: 24px 16px; }}
h1 {{ font-size: 20px; margin: 0 0 4px; }}
p.note {{ color: var(--muted); margin: 0 0 24px; }}
section {{ border-top: 1px solid var(--line); padding: 16px 0; }}
h2 {{ font-size: 16px; margin: 0 0 8px; }}
h2 small {{ color: var(--muted); font-weight: normal; }}
.cols {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 420px), 1fr)); gap: 16px; }}
h3 {{ font-size: 13px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); margin: 0 0 8px; }}
.msg {{ background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 12px; margin-bottom: 12px; }}
pre {{ white-space: pre-wrap; word-break: break-word; margin: 0 0 8px; font: 13px/1.45 ui-monospace, monospace; }}
a {{ color: var(--link); }}
.error {{ color: #e01e5a; }}
</style></head><body><main>
<h1>Slack preview</h1>
<p class="note">Raw text as the node posts it. Open a message in Block Kit Builder to see it as Slack renders it.
A message over {limit} characters is split into several sections there, and posts as one message in Slack.</p>
{body}
</main></body></html>
"""


def column(title, job):
    if job is None:
        return f'<div><h3>{title}</h3><p class="note">Not on this side.</p></div>'
    if job["error"]:
        return f'<div><h3>{title}</h3><p class="error">{html.escape(job["error"])}</p></div>'
    if not job["texts"]:
        return f'<div><h3>{title}</h3><p class="note">The node posted nothing for this sample.</p></div>'
    # Builder refuses a payload with no blocks, so an empty message gets no link.
    messages = "".join(
        f'<div class="msg"><pre>{html.escape(text)}</pre>'
        + (f'<a href="{html.escape(builder_url(text))}" target="_blank" rel="noopener">'
           f'Open in Block Kit Builder</a> · {len(text)} characters</div>'
           if text.strip() else '<span class="error">Empty message</span></div>')
        for text in job["texts"])
    return f"<div><h3>{title}</h3>{messages}</div>"


def page(previews, out):
    body = "".join(
        f'<section><h2>{html.escape(name)} <small>{html.escape(", ".join(files))}</small></h2>'
        f'<div class="cols">{"".join(column(title, job) for title, job in sides)}</div></section>'
        for name, files, sides in previews)
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, "index.html")
    open(path, "w").write(PAGE.format(limit=SECTION_LIMIT, body=body))
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--node", action="append", default=[],
                        help="render node, or the Slack node it feeds, repeatable")
    parser.add_argument("--all", action="store_true", help="every node that feeds a Slack post")
    parser.add_argument("--base", default="origin/main",
                        help="git ref to compare against and to render as 'before', "
                             "or '' to compare nothing")
    parser.add_argument("--fixture", default=FIXTURE, help="sample input for each render node")
    parser.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "slack-preview"))
    args = parser.parse_args()

    fixture = json.load(open(args.fixture))
    # Before is the commit this branch grew from, not the tip of --base, so a change that
    # landed on main since does not show up here as if this branch had made it. An empty
    # --base compares the checkout with itself, for CI, where origin/main may not have been
    # fetched and every node is rendered once with --all.
    here = exports_at(None)
    if args.base:
        fork = subprocess.run(["git", "-C", ROOT, "merge-base", "HEAD", args.base],
                              capture_output=True, text=True, check=True).stdout.strip()
        base, base_title = exports_at(fork), f"before ({args.base} at {fork[:7]})"
    else:
        base, base_title = here, "before"

    # Keyed by export and node, because the two intake workflows each carry a node of the
    # same name, and a change to one copy alone must still be previewed.
    def collect(exports):
        return {(file, name): node for file, export in exports.items()
                for name, node in render_nodes(export).items()}
    after, before = collect(here), collect(base)

    def parameters(side, key):
        """Moving a node changes its position, not its parameters, and is not a change."""
        return (side.get(key) or {}).get("parameters")

    slack_parent = {}
    for export in here.values():
        for name in render_nodes(export):
            for branch in export["connections"].get(name, {}).get("main", []):
                for edge in branch or []:
                    slack_parent[edge["node"]] = name

    keys = sorted(set(after) | set(before), key=lambda key: (key[1], key[0]))
    if args.node:
        wanted = {slack_parent.get(name, name) for name in args.node}
        unknown = wanted - {name for _, name in keys}
        if unknown:
            raise SystemExit(f"no node named {sorted(unknown)[0]!r} feeds a Slack post")
        keys = [key for key in keys if key[1] in wanted]
    elif args.all:
        keys = [key for key in keys if key in after]
    else:
        keys = [key for key in keys if parameters(after, key) != parameters(before, key)]
        if not keys:
            print(f"no Slack message differs from {base_title[8:-1]}; "
                  "name one with --node, or pass --all")
            sys.exit(0)

    # Copies of one node that are identical on both sides are rendered once, as the two
    # intake workflows share theirs.
    groups = {}
    for file, name in keys:
        signature = json.dumps([parameters(before, (file, name)), parameters(after, (file, name))],
                               sort_keys=True)
        groups.setdefault((name, signature), []).append(file)

    jobs, previews = [], []
    for (name, _), files in sorted(groups.items()):
        key = (files[0], name)
        entry = fixture.get(name)
        if entry is None and key in after:
            raise SystemExit(f"{args.fixture} has no sample input for {name!r}. Add an entry: "
                             "its feeds, in order, the last one being the node's input.")
        changed = parameters(after, key) != parameters(before, key)
        sides = []
        for title, side in ((base_title, before), ("after (this checkout)", after)):
            if not changed and side is before:
                continue
            node = side.get(key)
            if node is None:
                sides.append((title, None))
                continue
            title = title if changed else "unchanged"
            feeds = (entry or {}).get("feeds", {})
            missing = sorted(referenced_nodes(node["parameters"].get("jsCode", "")) - set(feeds))
            if entry is None or missing:
                problem = (f"{args.fixture} has no sample input for {name!r}" if entry is None
                           else f"{name!r} reads $('{missing[0]}'), which {args.fixture} "
                                "has no feed for")
                # Today's fixture describes today's nodes. The old side of a renamed feed
                # cannot be rendered with it, which is worth saying but not worth stopping.
                if side is after:
                    raise SystemExit(problem)
                sides.append((title, {"error": problem, "texts": [], "after": False}))
                continue
            job = {"node": node, "feeds": feeds, "after": side is after}
            jobs.append(job)
            sides.append((title, job))
        previews.append((name, files, sides))

    if jobs:
        run_all(jobs)
    # Every fixture entry is written to produce at least one message, so a node that posts
    # nothing for it has a guard that drops every row, which is a failure worth seeing.
    for job in jobs:
        if job["after"] and not job["error"] and not job["texts"]:
            job["error"] = "the node posted nothing for its sample input"
    failed = [job for job in jobs if job["error"]]
    for name, files, sides in previews:
        for title, job in sides:
            if job is None:
                continue
            state = f"error: {job['error']}" if job["error"] else f"{len(job['texts'])} message(s)"
            print(f"{name} ({', '.join(files)}) [{title}]: {state}")
    print(page(previews, args.out))
    sys.exit(1 if failed else 0)

if __name__ == "__main__":
    main()
