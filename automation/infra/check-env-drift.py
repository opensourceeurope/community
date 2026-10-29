#!/usr/bin/env python3
"""Say whether this box's .env still holds the values git says it should.

deploy.sh never edits automation/infra/.env, so a merged change that needs a
new value there, such as a different AI_MODEL, switches nothing until a person
sets it, and nothing used to say so. A variable in automation/.env.example
whose line follows the comment line

    # box: must match

is one the box is meant to run exactly as written there. This script compares
each of them with the box's .env, and with the environment of the running n8n
container, which reads .env only when it is created. It exits:

  0  every marked value matches
  1  at least one differs, so set it on the box
  2  the check itself could not run

ose-env-drift.service runs it hourly. A non-zero exit fails the unit, and
ose-alert@ posts the report to the alerts channel, once, then posts recovered
when the values match again. So the report is written to be read in Slack.

Values are printed, so the marker belongs only on configuration that is not a
secret. A marked name that looks like a credential stops the check rather than
post its value.

When no n8n container is running, only .env is compared: n8n being down is
for the deploy and the workflows to report, not this check.

Stdlib only. The test passes the n8n environment through OSE_N8N_ENV_FILE, a
JSON list of NAME=value strings, or null for no container, instead of docker.

    check-env-drift.py [repo root]
"""
import json
import os
import re
import subprocess
import sys

EXAMPLE = "automation/.env.example"
BOX_ENV = "automation/infra/.env"
MARKER = "# box: must match"
RECIPE = 'automation/infra/README.md, "Setting a plain configuration value"'
SECRET = re.compile(r"KEY|SECRET|PASS|TOKEN|AUTH|CREDENTIAL|PRIVATE|CERT", re.I)
URL_LOGIN = re.compile(r"://[^/@\s]+@")
ASSIGN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def compose_value(raw):
    """A value the way docker compose reads it from .env: a quoted value ends
    at its closing quote, an unquoted one where a space and `#` start a comment."""
    raw = raw.strip()
    if raw[:1] == "'":
        end = raw.find("'", 1)
        return raw[1:end] if end != -1 else raw[1:]
    if raw[:1] == '"':
        out, i = [], 1
        while i < len(raw) and raw[i] != '"':
            if raw[i] == "\\" and i + 1 < len(raw):
                i += 1
            out.append(raw[i])
            i += 1
        return "".join(out)
    return re.split(r"\s+#", raw, maxsplit=1)[0].strip()


def read_env(path):
    """Every assignment in a .env file. The last line for a name wins."""
    values = {}
    with open(path) as handle:
        for line in handle:
            m = ASSIGN.match(line.rstrip("\r\n"))
            if m:
                values[m.group(1)] = compose_value(m.group(2))
    return values


def marked(path):
    """The variables in .env.example whose line follows the marker, in order.

    A marker followed by anything but an assignment, such as a blank line or
    another comment, raises: skipping it would quietly stop checking a value
    someone meant to check."""
    out = []
    previous = ""
    with open(path) as handle:
        for number, line in enumerate(handle, 1):
            line = line.rstrip("\r\n")
            if previous.strip() == MARKER:
                m = ASSIGN.match(line)
                if not m:
                    raise ValueError(f"{EXAMPLE} line {number}: '{MARKER}' must sit directly above the variable it marks")
                out.append((m.group(1), compose_value(m.group(2))))
            previous = line
    return out


def n8n_env(infra_dir):
    """The running n8n container's environment, or None when there is none.

    Only this checkout's compose project counts, by the working directory
    compose labels its containers with, so a throwaway n8n started by a test
    on the same box is never read instead."""
    path = os.environ.get("OSE_N8N_ENV_FILE")
    if path:
        with open(path) as handle:
            pairs = json.load(handle)
        if pairs is None:
            return None
    else:
        ids = subprocess.run(
            ["docker", "ps", "-q", "--filter", "label=com.docker.compose.service=n8n",
             "--filter", f"label=com.docker.compose.project.working_dir={infra_dir}"],
            capture_output=True, text=True, check=True).stdout.split()
        if not ids:
            return None
        pairs = json.loads(subprocess.run(
            ["docker", "inspect", "--format", "{{json .Config.Env}}", ids[0]],
            capture_output=True, text=True, check=True).stdout)
    return dict(p.split("=", 1) for p in pairs if "=" in p)


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
    try:
        expected = marked(os.path.join(root, EXAMPLE))
        box = read_env(os.path.join(root, BOX_ENV))
    except Exception as exc:  # a missing file or a misplaced marker: the check did not run
        print(f"the .env drift check could not run: {exc}")
        return 2
    # Docker failing must not hide drift in .env, so .env is still compared
    # and the check fails on the docker error afterwards.
    docker_error = None
    try:
        running = n8n_env(os.path.realpath(os.path.join(root, os.path.dirname(BOX_ENV))))
    except Exception as exc:
        running, docker_error = None, f"could not read the running n8n's environment: {exc}"

    if not expected:
        print(f"no variable in {EXAMPLE} carries '{MARKER}', so there is nothing to compare")
        return 2
    risky = [name for name, value in expected if SECRET.search(name) or URL_LOGIN.search(value)]
    if risky:
        print(f"{', '.join(risky)} looks like it holds a credential, so its value is not compared or posted. "
              f"Take '{MARKER}' off it in {EXAMPLE}")
        return 2
    interpolated = [name for name, value in expected if "$" in value]
    if interpolated:
        print(f"{', '.join(interpolated)} contains $, which compose expands, so this check cannot compare it. "
              f"Take '{MARKER}' off it in {EXAMPLE}")
        return 2

    in_env = []
    in_n8n = []
    for name, want in expected:
        have = box.get(name)
        if have != want:
            in_env.append(f"{name}: the box has {'nothing' if have is None else repr(have)}, git says {want!r}")
        elif running is not None and name in running and running[name] != have:
            in_n8n.append(f"{name}: .env has {have!r}, the running n8n still has {running[name]!r}")

    if not in_env and not in_n8n and not docker_error:
        where = "in .env and in the running n8n" if running is not None else "in .env. No n8n container is running, so only .env was read"
        print(f"Every marked value matches {EXAMPLE}, {where}.")
        return 0

    if in_env:
        print(f"This box's .env differs from {EXAMPLE}. A merged change expects these, "
              f"and the deploy never edits .env:")
        for line in in_env:
            print(f"  {line}")
        print(f"Set each one with {RECIPE}.")
    if in_n8n:
        print("n8n was not recreated after .env changed, so it still runs the old values:")
        for line in in_n8n:
            print(f"  {line}")
        print("Run docker compose up -d n8n in automation/infra and check it prints Recreated.")
    if docker_error:
        print(docker_error)
        return 1 if in_env else 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
