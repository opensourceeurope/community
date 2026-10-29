#!/usr/bin/env python3
"""Say whether the n8n version this box runs has a published security advisory.

Reads the pinned tag out of automation/infra/docker-compose.yml, then asks
GitHub for n8n's newest stable release and every published security advisory.
It prints a short plain-text report and exits:

  0  no advisory with a fix affects the pinned version
  1  at least one does, so upgrade
  2  the check itself could not run

ose-n8n-advisories.service runs it daily. A non-zero exit fails the unit, and
ose-alert@ posts the last lines of this output to the alerts channel, once,
then posts again when an upgrade clears it. So the report is written to be
read in Slack: the verdict first, then the worst advisories, at most 15 lines.

Being behind the stable release without an advisory is reported but is not a
failure. n8n releases every week, and an upgrade is a production change that
someone has to watch, so only a security fix makes it urgent.

An advisory lists one range per release line, for example "< 2.44.1" and
"< 2.43.5". A version is fixed when a patched version on its own minor line is
at or below it, when its minor line is newer than every patched line of its
major, or when it is at or above the newest patched version. Otherwise it is
affected when any range contains it.

The newer-line rule is there because n8n lists only the lines it backported
to. A fix in 2.43.3 is in 2.44.0 and every line after it, yet the same
advisory's "< 3.0.1" for the 3.x line also contains 2.45.5, which once kept
the alert firing after an upgrade and named the running version as the one to
move to. A line between two patched lines is still read as affected.

Stdlib only. GITHUB_TOKEN raises the API rate limit but is not required: a
daily run makes a handful of unauthenticated calls. The test passes fixture
files through OSE_N8N_RELEASES_FILE and OSE_N8N_ADVISORIES_FILE instead.

    check-n8n-version.py [repo root]
"""
import json
import os
import re
import sys
import urllib.request

COMPOSE = "automation/infra/docker-compose.yml"
ADVISORIES_PAGE = "https://github.com/n8n-io/n8n/security/advisories"
SHOWN = 8
PIN = re.compile(r"^\s*image:\s*docker\.n8n\.io/n8nio/n8n:(\d+\.\d+\.\d+)\s*$", re.M)
API = "https://api.github.com/repos/n8n-io/n8n"
VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")
CONSTRAINT = re.compile(r"(<=|>=|<|>|=)?\s*(\d+\.\d+\.\d+)")


def parse(text):
    m = VERSION.search(text or "")
    return tuple(int(x) for x in m.groups()) if m else None


def show(v):
    return ".".join(map(str, v))


def request(url):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp), resp.headers.get("Link", "")


def get(url):
    return request(url)[0]


def get_all(url, limit=50):
    """Follow the Link header. The advisories endpoint pages by cursor and
    ignores page=, so counting pages would fetch the first one forever."""
    out = []
    for _ in range(limit):
        batch, link = request(url)
        out.extend(batch)
        m = re.search(r'<([^>]+)>;\s*rel="next"', link)
        if not m:
            return out
        url = m.group(1)
    raise RuntimeError(f"more than {limit} pages at {url}")


def in_range(v, rng):
    """True when v satisfies every constraint in a range such as '>= 2.0.0, < 2.4.1'."""
    parts = [p for p in (rng or "").split(",") if p.strip()]
    if not parts:
        return False
    for part in parts:
        m = CONSTRAINT.search(part)
        if not m:
            return False
        op, bound = m.group(1) or "=", parse(m.group(2))
        ok = {
            "<": v < bound, "<=": v <= bound, ">": v > bound,
            ">=": v >= bound, "=": v == bound,
        }[op]
        if not ok:
            return False
    return True


def n8n_vulns(advisory):
    return [x for x in advisory.get("vulnerabilities") or []
            if (x.get("package") or {}).get("name") == "n8n"]


def has_patch(advisory):
    return any(parse(x.get("patched_versions")) for x in n8n_vulns(advisory))


def affects(advisory, v):
    vulns = n8n_vulns(advisory)
    if not vulns:
        return False
    patched = [p for p in (parse(x.get("patched_versions")) for x in vulns) if p]
    if any(p[:2] == v[:2] and v >= p for p in patched):
        return False
    same_major = [p[:2] for p in patched if p[0] == v[0]]
    if same_major and v[:2] > max(same_major):
        return False
    if patched and v >= max(patched):
        return False
    return any(in_range(v, x.get("vulnerable_version_range")) for x in vulns)


def load(env, fetch):
    path = os.environ.get(env)
    if path:
        with open(path) as handle:
            return json.load(handle)
    return fetch()


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
    try:
        with open(os.path.join(root, COMPOSE)) as f:
            m = PIN.search(f.read())
        if not m:
            print(f"cannot find a pinned n8n image in {COMPOSE}")
            return 2
        pinned = parse(m.group(1))
        # Newest first. n8n has thousands of releases and ships several a
        # week, so the latest stable one is always on the first page.
        releases = load("OSE_N8N_RELEASES_FILE", lambda: get(f"{API}/releases?per_page=100"))
        advisories = load("OSE_N8N_ADVISORIES_FILE",
                          lambda: get_all(f"{API}/security-advisories?state=published&per_page=100"))
    except Exception as exc:  # network, JSON or file: the check did not run
        print(f"the n8n advisory check could not run: {exc}")
        return 2

    stable = max(
        (parse(r["tag_name"]) for r in releases
         if not r.get("prerelease") and not r.get("draft")
         and re.fullmatch(r"n8n@\d+\.\d+\.\d+", r.get("tag_name", ""))),
        default=None,
    )
    affecting = [a for a in advisories if affects(a, pinned)]
    # An advisory with no patched version describes a risky feature, not a
    # bug, such as the Execute Command node. No upgrade clears it, so it never
    # fails the check. The mitigation is configuration: n8n's default
    # NODES_EXCLUDE already drops that node.
    hits = [a for a in affecting if has_patch(a)]
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    hits.sort(key=lambda a: (order.get(a.get("severity"), 4), a.get("published_at", "")))
    target = show(stable) if stable else "the newest stable release"

    if not hits:
        line = f"n8n {show(pinned)}: no published advisory with a fix affects it."
        if stable and stable > pinned:
            line += f" Stable is {show(stable)}, a routine upgrade, not an urgent one."
        print(line)
        return 0

    counts = {}
    for a in hits:
        counts[a.get("severity", "unknown")] = counts.get(a.get("severity", "unknown"), 0) + 1
    summary = ", ".join(f"{n} {s}" for s, n in sorted(counts.items(), key=lambda kv: order.get(kv[0], 4)))
    print(f"n8n {show(pinned)} is affected by {len(hits)} published advisories ({summary}).")
    print(f"Upgrade: set the n8n image in {COMPOSE} to {target} in a pull request.")
    for a in hits[:SHOWN]:
        print(f"{a.get('severity', '?'):<8} {a['ghsa_id']}  {(a.get('summary') or '')[:90]}")
    if len(hits) > SHOWN:
        print(f"... and {len(hits) - SHOWN} more: {ADVISORIES_PAGE}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
