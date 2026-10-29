#!/usr/bin/env python3
"""Runs automation/infra/check-n8n-version.py against fixed releases and advisories.

No network: the script reads the fixtures through OSE_N8N_RELEASES_FILE and
OSE_N8N_ADVISORIES_FILE. Each case pins a version in a throwaway compose file and
checks the exit code and the report, which ose-alert@ posts to Slack and so has to
stay within the 15 journal lines it shows.

    python3 automation/test/check-n8n-version.py
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(ROOT, "automation", "infra", "check-n8n-version.py")

RELEASES = [
    {"tag_name": "n8n@2.44.2", "prerelease": True},
    {"tag_name": "n8n@2.43.8", "prerelease": False},
    {"tag_name": "n8n@1.123.85", "prerelease": False},
    {"tag_name": "stable", "prerelease": False},
]


def advisory(ghsa, severity, ranges, summary="an advisory"):
    return {"ghsa_id": ghsa, "severity": severity, "summary": summary,
            "html_url": f"https://example.org/{ghsa}", "published_at": "2026-10-01T00:00:00Z",
            "vulnerabilities": [{"package": {"name": "n8n"}, "vulnerable_version_range": r,
                                 "patched_versions": p} for r, p in ranges]}


ADVISORIES = [
    # Fixed per release line, the way n8n publishes them.
    advisory("GHSA-line", "high", [("< 2.44.1", ">= 2.44.1"), ("< 2.43.5", ">= 2.43.5")]),
    # Fixed only on a line newer than 2.37.
    advisory("GHSA-old", "medium", [("< 2.38.2", ">= 2.38.2")]),
    # Backported to two 2.x lines and fixed on 3.x. Its "< 3.0.1" also holds
    # every later 2.x release, which already has the fix.
    advisory("GHSA-major", "high", [("< 2.41.6", ">= 2.41.6"), ("< 2.42.3", ">= 2.42.3"),
                                      ("< 3.0.1", ">= 3.0.1")]),
    # Patched on 2.39 and 2.41 but not 2.40, which stays affected.
    advisory("GHSA-gap", "medium", [("< 2.39.4", ">= 2.39.4"), ("< 2.41.1", ">= 2.41.1")]),
    # No fix at all: a risky feature. It must never fail the check.
    advisory("GHSA-nofix", "high", [("> 0.0.1", "")]),
    # Another package entirely.
    {"ghsa_id": "GHSA-other", "severity": "critical", "summary": "x", "html_url": "u",
     "vulnerabilities": [{"package": {"name": "n8n-nodes-base"},
                          "vulnerable_version_range": "< 9.9.9", "patched_versions": "9.9.9"}]},
] + [advisory(f"GHSA-many{i}", "medium", [("< 2.40.0", ">= 2.40.0")]) for i in range(20)]

# name, pinned version, expected exit, text the report must contain, text it must not
CASES = [
    ("a version past every fix is clear", "2.43.8", 0, "no published advisory", "GHSA-"),
    ("the patched version on its own line is clear", "2.43.5", 0, "Stable is 2.43.8", "GHSA-line"),
    ("one patch short on its line is affected", "2.43.4", 1, "GHSA-line", "GHSA-nofix"),
    ("an old line names the stable release to move to", "2.37.3", 1, "to 2.43.8", "GHSA-other"),
    ("high comes before medium", "2.37.3", 1, "affected by 24 published advisories (2 high, 22 medium)", ""),
    ("a line newer than every patched one is clear", "2.43.8", 0, "no published advisory", "GHSA-major"),
    ("a patched line still needs its own patch", "2.42.2", 1, "GHSA-major", ""),
    ("a line between two patched ones is affected", "2.40.9", 1, "GHSA-gap", ""),
    ("the next major is judged on its own line", "3.0.0", 1, "GHSA-major", ""),
]


def run(pinned, workdir):
    os.makedirs(os.path.join(workdir, "automation", "infra"), exist_ok=True)
    with open(os.path.join(workdir, "automation", "infra", "docker-compose.yml"), "w") as handle:
        handle.write(f"services:\n  n8n:\n    image: docker.n8n.io/n8nio/n8n:{pinned}\n")
    env = dict(os.environ,
               OSE_N8N_RELEASES_FILE=os.path.join(workdir, "releases.json"),
               OSE_N8N_ADVISORIES_FILE=os.path.join(workdir, "advisories.json"))
    result = subprocess.run([sys.executable, "-I", SCRIPT, workdir], capture_output=True, text=True, env=env)
    return result.returncode, result.stdout


def main():
    failures = 0
    with tempfile.TemporaryDirectory(prefix="ose-n8n-version-") as workdir:
        json.dump(RELEASES, open(os.path.join(workdir, "releases.json"), "w"))
        json.dump(ADVISORIES, open(os.path.join(workdir, "advisories.json"), "w"))
        for name, pinned, want_code, must, must_not in CASES:
            code, out = run(pinned, workdir)
            problems = []
            if code != want_code:
                problems.append(f"exit {code}, wanted {want_code}")
            if must and must not in out:
                problems.append(f"no '{must}'")
            if must_not and must_not in out:
                problems.append(f"says '{must_not}'")
            if len(out.strip().splitlines()) > 14:
                problems.append(f"{len(out.strip().splitlines())} lines, more than the alert shows")
            if code == 1 and "high" not in out.splitlines()[2]:
                problems.append("the first advisory listed is not the worst")
            failures += 1 if problems else 0
            print(f"{'ok  ' if not problems else 'FAIL'} {name}" + ("" if not problems else ": " + "; ".join(problems)))
            if problems:
                print("     | " + out.strip().replace("\n", "\n     | "))

        os.remove(os.path.join(workdir, "advisories.json"))
        code, out = run("2.43.8", workdir)
        ok = code == 2 and "could not run" in out
        failures += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'} a check that cannot read the advisories exits 2 and says so")
    print("check-n8n-version: all cases passed" if not failures else f"check-n8n-version: {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
