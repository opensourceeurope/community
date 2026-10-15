#!/usr/bin/env python3
"""Report whether the n8n version production runs needs upgrading.

Reads the pinned tag out of automation/infra/docker-compose.yml, then asks
GitHub for two things about n8n-io/n8n: its newest stable release, and every
published security advisory. It prints a Markdown report and exits:

  0  the pinned version has no open advisory
  1  at least one published advisory affects the pinned version
  2  the check itself could not run

Being behind the stable release without an advisory is reported but is not a
failure. n8n releases every week, and an upgrade is a production change that
someone has to watch, so only a security fix makes it urgent.

An advisory lists one range per release line, for example "< 2.44.1" and
"< 2.43.5". A version is fixed when a patched version on its own minor line is
at or below it, or when it is at or above the newest patched version. Otherwise
it is affected when any range contains it.

Stdlib only. GITHUB_TOKEN raises the API rate limit but is not required.
"""
import json
import os
import re
import sys
import urllib.request

COMPOSE = "automation/infra/docker-compose.yml"
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
    if patched and v >= max(patched):
        return False
    return any(in_range(v, x.get("vulnerable_version_range")) for x in vulns)


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    try:
        with open(os.path.join(root, COMPOSE)) as f:
            m = PIN.search(f.read())
        if not m:
            print(f"cannot find a pinned n8n image in {COMPOSE}", file=sys.stderr)
            return 2
        pinned = parse(m.group(1))
        # Newest first. n8n has thousands of releases and ships several a
        # week, so the latest stable one is always on the first page.
        releases = get(f"{API}/releases?per_page=100")
        advisories = get_all(f"{API}/security-advisories?state=published&per_page=100")
    except Exception as exc:  # network, JSON or file: the check did not run
        print(f"check failed: {exc}", file=sys.stderr)
        return 2

    stable = max(
        (parse(r["tag_name"]) for r in releases
         if not r.get("prerelease") and not r.get("draft")
         and re.fullmatch(r"n8n@\d+\.\d+\.\d+", r.get("tag_name", ""))),
        default=None,
    )
    affecting = [a for a in advisories if affects(a, pinned)]
    # An advisory with no patched version describes a risky feature, not a
    # bug, such as the Execute Command node. No upgrade clears it, so it is
    # listed for the record and never fails the check. The mitigation is
    # configuration, and NODES_EXCLUDE already drops that node.
    hits = [a for a in affecting if has_patch(a)]
    unfixable = [a for a in affecting if not has_patch(a)]
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    hits.sort(key=lambda a: (order.get(a.get("severity"), 4), a.get("published_at", "")))

    print(f"Production pins n8n **{show(pinned)}**. "
          f"The newest stable release is **{show(stable) if stable else 'unknown'}**.\n")
    if hits:
        counts = {}
        for a in hits:
            counts[a.get("severity", "unknown")] = counts.get(a.get("severity", "unknown"), 0) + 1
        summary = ", ".join(f"{n} {s}" for s, n in sorted(counts.items(), key=lambda kv: order.get(kv[0], 4)))
        print(f"{len(hits)} published advisories affect it ({summary}). "
              f"Bump the `image:` line in `{COMPOSE}` to the stable release, "
              "following \"Upgrading n8n\" in `automation/infra/README.md`.\n")
        print("| Severity | Advisory | Published | Summary |")
        print("|---|---|---|---|")
        for a in hits:
            summary_text = (a.get("summary") or "").replace("|", "\\|")
            print(f"| {a.get('severity')} | [{a['ghsa_id']}]({a['html_url']}) "
                  f"| {(a.get('published_at') or '')[:10]} | {summary_text} |")
        list_unfixable(unfixable)
        return 1
    print("No published advisory with a fix affects it.")
    if stable and stable > pinned:
        print("\nIt is behind the stable release. That is routine, not urgent.")
    list_unfixable(unfixable)
    return 0


def list_unfixable(advisories):
    if not advisories:
        return
    print("\nAdvisories no release fixes, mitigated by configuration if at all:\n")
    for a in advisories:
        print(f"- [{a['ghsa_id']}]({a['html_url']}): {a.get('summary') or ''}")


if __name__ == "__main__":
    sys.exit(main())
