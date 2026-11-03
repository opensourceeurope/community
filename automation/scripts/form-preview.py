#!/usr/bin/env python3
"""Screenshots pages of the application form as an applicant sees them, from an export.

The form is client-rendered and n8n filters its custom HTML and CSS, so the only faithful
picture of a page is one taken from a running n8n. This starts one in a throwaway container
on the image production runs, imports a preview workflow per page, and drives a headless
browser in a second container. Nothing is published anywhere and no browser opens.

Each preview workflow is a one-field start form, a stub Code node for every node the page's
expressions read by name, then a copy of the page. The stubs return the sample data in the
fixture, so a page built from the row, such as Form page 6, renders the way it would for
that row.

    python3 automation/scripts/form-preview.py --page "Form page 5" --page "Form page 6"
    python3 automation/scripts/form-preview.py --export /path/to/edited-form.json --all

Needs docker. Writes one PNG per page into --out, plus an index.html showing every PNG
there, and prints each path. --serve PORT then serves that gallery on 127.0.0.1 until
interrupted, and --serve-only serves an existing --out without a new run.
"""
import argparse
import functools
import html
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXPORT = os.path.join(ROOT, "automation", "n8n", "form-ose.json")
FIXTURE = os.path.join(ROOT, "automation", "test", "fixtures", "form-preview.json")
COMPOSE = os.path.join(ROOT, "automation", "infra", "docker-compose.yml")
# The image ships the browsers for this release but not the Python package, so the
# package is installed at the same version on every run.
PLAYWRIGHT_VERSION = "1.55.0"
PLAYWRIGHT_IMAGE = f"mcr.microsoft.com/playwright/python:v{PLAYWRIGHT_VERSION}-noble"
START_LABEL = "Preview"

SHOOT = r'''
import json, sys
from playwright.sync_api import sync_playwright

jobs = json.load(open("/work/jobs.json"))
failed = 0
with sync_playwright() as p:
    browser = p.chromium.launch()
    for job in jobs:
        page = browser.new_page(viewport={"width": 900, "height": 900}, device_scale_factor=2)
        try:
            page.goto(job["url"], wait_until="networkidle")
            page.get_by_label("%(label)s").fill("go")
            page.locator("button[type=submit]").first.click()
            page.wait_for_function(
                "() => !document.body.innerText.includes('%(label)s')", timeout=30000)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(500)
            page.screenshot(path="/work/out/" + job["file"], full_page=True)
            print("ok", job["file"])
        except Exception as error:
            failed += 1
            page.screenshot(path="/work/out/failed-" + job["file"], full_page=True)
            print("FAILED", job["file"], error, file=sys.stderr)
        page.close()
    browser.close()
sys.exit(1 if failed else 0)
''' % {"label": START_LABEL}


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


def referenced_nodes(parameters):
    """Every node name the page's expressions read with $('...')."""
    text = json.dumps(parameters)
    return sorted(set(re.findall(r"\$\(\\?['\"]([^'\"\\]+)\\?['\"]\)", text)))


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def build(page, start_options, stubs, index):
    """A preview workflow: start form, stubs in a chain, then the page."""
    workflow_id = f"formpreview{index:04d}"
    path = f"p-{slugify(page['name'])}"
    start = {
        "id": "start", "name": "Preview start", "type": "n8n-nodes-base.formTrigger",
        "typeVersion": 2.6, "position": [0, 0], "webhookId": str(uuid.uuid4()),
        "parameters": {
            "formTitle": "Preview",
            "formDescription": f"Preview: {page['name']}",
            "formFields": {"values": [{"fieldLabel": START_LABEL}]},
            "options": dict(start_options, path=path, buttonLabel="Show the page"),
        },
    }
    nodes = [start]
    chain = ["Preview start"]
    for offset, (name, data) in enumerate(stubs.items(), start=1):
        nodes.append({
            "id": f"stub{offset}", "name": name, "type": "n8n-nodes-base.code",
            "typeVersion": 2, "position": [offset * 200, 0],
            "parameters": {"mode": "runOnceForEachItem",
                           "jsCode": "return { json: " + json.dumps(data) + " };"},
        })
        chain.append(name)
    target = json.loads(json.dumps(page))
    target["id"] = "target"
    target["position"] = [(len(chain)) * 200, 0]
    target["webhookId"] = str(uuid.uuid4())
    target.pop("disabled", None)
    nodes.append(target)
    chain.append(page["name"])
    connections = {a: {"main": [[{"node": b, "type": "main", "index": 0}]]}
                   for a, b in zip(chain, chain[1:])}
    return workflow_id, path, {
        "id": workflow_id, "name": f"preview {page['name']}", "active": False,
        "settings": {"executionOrder": "v1"}, "nodes": nodes, "connections": connections,
    }


GALLERY = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Form preview</title>
<style>
body {{ margin: 0; background: #0A242B; color: #fff; font: 15px/1.5 system-ui, sans-serif; }}
nav {{ position: sticky; top: 0; background: #0E282F; padding: 12px 16px;
       border-bottom: 1px solid rgba(255,255,255,.16); display: flex; gap: 16px; flex-wrap: wrap; }}
nav a {{ color: #9FE7F5; }}
main {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 560px), 1fr));
        gap: 24px; padding: 24px 16px; align-items: start; }}
h2 {{ font-size: 16px; margin: 0 0 8px; }}
img {{ display: block; width: 100%; height: auto; border-radius: 8px;
       border: 1px solid rgba(255,255,255,.16); }}
</style></head><body>
<nav>{nav}</nav>
<main>{sections}</main>
</body></html>
"""


def gallery(out):
    """An index.html in --out showing every PNG there, newest run included."""
    shots = sorted(name for name in os.listdir(out) if name.endswith(".png"))
    nav = "".join(f'<a href="#{html.escape(name)}">{html.escape(name[:-4])}</a>' for name in shots)
    sections = "".join(
        f'<section id="{html.escape(name)}"><h2>{html.escape(name[:-4])}</h2>'
        f'<a href="{html.escape(name)}"><img src="{html.escape(name)}" alt=""></a></section>'
        for name in shots)
    path = os.path.join(out, "index.html")
    open(path, "w").write(GALLERY.format(nav=nav, sections=sections))
    return path


def serve(out, port):
    """Serve the gallery on localhost only, until interrupted."""
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=out)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"serving http://127.0.0.1:{port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def run(cmd, **kwargs):
    return subprocess.run(cmd, check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--export", default=EXPORT, help="form workflow export to preview")
    parser.add_argument("--fixture", default=FIXTURE, help="sample data for the stub nodes")
    parser.add_argument("--page", action="append", default=[], help="form node name, repeatable")
    parser.add_argument("--all", action="store_true", help="every form page in the export")
    parser.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "form-preview"))
    parser.add_argument("--serve", type=int, metavar="PORT",
                        help="after the run, serve the gallery on 127.0.0.1:PORT until interrupted")
    parser.add_argument("--serve-only", action="store_true",
                        help="skip the run and serve what --out already holds")
    args = parser.parse_args()

    if args.serve_only:
        gallery(args.out)
        serve(args.out, args.serve or 8765)
        return

    export = json.load(open(args.export))
    fixture = json.load(open(args.fixture))
    by_name = {node["name"]: node for node in export["nodes"]}
    pages = [node for node in export["nodes"] if node["type"] == "n8n-nodes-base.form"
             and node["parameters"].get("operation", "page") == "page"]
    names = [node["name"] for node in pages] if args.all else args.page
    if not names:
        raise SystemExit("name a page with --page, or pass --all")
    trigger = next(node for node in export["nodes"]
                   if node["type"] == "n8n-nodes-base.formTrigger")
    start_options = {key: value for key, value in trigger["parameters"].get("options", {}).items()
                     if key in ("customCss", "appendAttribution")}

    work = tempfile.mkdtemp(prefix="form-preview-")
    os.makedirs(os.path.join(work, "workflows"))
    os.makedirs(os.path.join(work, "out"))
    jobs, ids = [], []
    for index, name in enumerate(names):
        if name not in by_name:
            raise SystemExit(f"{args.export}: no node named {name!r}")
        page = by_name[name]
        if page["type"] == "n8n-nodes-base.formTrigger":
            raise SystemExit(f"{name!r} is the start page, open it on the instance instead")
        stubs = {}
        for ref in referenced_nodes(page["parameters"]):
            if ref not in fixture:
                raise SystemExit(f"{name!r} reads $('{ref}'), which {args.fixture} has no data for")
            stubs[ref] = fixture[ref]
        workflow_id, path, workflow = build(page, start_options, stubs, index)
        json.dump(workflow, open(os.path.join(work, "workflows", f"{workflow_id}.json"), "w"))
        jobs.append({"url": f"http://n8n:5678/form/{path}", "file": f"{slugify(name)}.png"})
        ids.append(workflow_id)
    json.dump(jobs, open(os.path.join(work, "jobs.json"), "w"))
    open(os.path.join(work, "shoot.py"), "w").write(SHOOT)

    tag = uuid.uuid4().hex[:8]
    network, container = f"form-preview-{tag}", f"form-preview-n8n-{tag}"
    publish = " && ".join(f"n8n publish:workflow --id={i}" for i in ids)
    entry = ("n8n import:workflow --separate --input=/workflows >/dev/null && "
             f"{publish} >/dev/null && exec n8n start")
    try:
        run(["docker", "network", "create", network], stdout=subprocess.DEVNULL)
        run(["docker", "run", "-d", "--rm", "--name", container, "--network", network,
             "--network-alias", "n8n", "-v", f"{work}/workflows:/workflows:ro",
             "-e", "WEBHOOK_URL=http://n8n:5678/", "-e", "N8N_EDITOR_BASE_URL=http://n8n:5678/",
             "-e", "N8N_DIAGNOSTICS_ENABLED=false", "-e", "N8N_PERSONALIZATION_ENABLED=false",
             "-e", "N8N_SECURE_COOKIE=false", "-e", "GENERIC_TIMEZONE=Europe/Berlin",
             "--entrypoint", "sh", image(), "-c", entry], stdout=subprocess.DEVNULL)
        wait = ("import time, urllib.request\n"
                "for _ in range(120):\n"
                "    try:\n"
                "        urllib.request.urlopen('http://n8n:5678/healthz/readiness', timeout=2); break\n"
                "    except Exception: time.sleep(1)\n"
                "else: raise SystemExit('n8n never became ready')\n")
        open(os.path.join(work, "wait.py"), "w").write(wait)
        shot = subprocess.run(
            ["docker", "run", "--rm", "--network", network, "-v", f"{work}:/work",
             "--entrypoint", "sh", PLAYWRIGHT_IMAGE, "-c",
             f"pip install -q --break-system-packages playwright=={PLAYWRIGHT_VERSION} "
             "&& python3 /work/wait.py && python3 /work/shoot.py"])
        if shot.returncode:
            logs = subprocess.run(["docker", "logs", "--tail", "40", container],
                                  capture_output=True, text=True)
            print(logs.stdout + logs.stderr, file=sys.stderr)
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
        subprocess.run(["docker", "network", "rm", network], capture_output=True)

    os.makedirs(args.out, exist_ok=True)
    for name in sorted(os.listdir(os.path.join(work, "out"))):
        target = os.path.join(args.out, name)
        shutil.move(os.path.join(work, "out", name), target)
        print(target)
    shutil.rmtree(work, ignore_errors=True)
    print(gallery(args.out))
    if args.serve and not shot.returncode:
        serve(args.out, args.serve)
    sys.exit(shot.returncode)


if __name__ == "__main__":
    main()
