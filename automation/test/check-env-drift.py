#!/usr/bin/env python3
"""Runs automation/infra/check-env-drift.py against fixed .env files.

No docker: the script reads the running n8n's environment through
OSE_N8N_ENV_FILE. Each case builds a throwaway repo tree holding an
.env.example, a box .env and that environment, then checks the exit code and
the report, which ose-alert@ posts to Slack and so has to stay within the 15
journal lines it shows.

    python3 automation/test/check-env-drift.py
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(ROOT, "automation", "infra", "check-env-drift.py")

EXAMPLE = """# Not compared: the box decides this one.
DRY_RUN=true

# The model the review uses.
# box: must match
AI_MODEL=mistral-medium-3.5-128b

# box: must match
SWEEP_CRON=0 * * * *
AI_API_KEY=
"""

MATCHING = """DRY_RUN=false
AI_MODEL=mistral-medium-3.5-128b
SWEEP_CRON=0 * * * *
AI_API_KEY=not-a-real-key
"""

N8N = ["AI_MODEL=mistral-medium-3.5-128b", "SWEEP_CRON=0 * * * *", "AI_API_KEY=not-a-real-key"]

# name, .env.example, box .env, n8n environment, expected exit, text the report
# must contain (one string or a list), text it must not
CASES = [
    ("every marked value matches", EXAMPLE, MATCHING, N8N, 0, "in .env and in the running n8n", ""),
    ("an unmarked variable may differ", EXAMPLE, MATCHING.replace("DRY_RUN=false", "DRY_RUN=whatever"),
     N8N, 0, "match", "DRY_RUN"),
    ("a value the box never set is drift", EXAMPLE, MATCHING.replace("AI_MODEL=mistral-medium-3.5-128b\n", ""),
     N8N, 1, "AI_MODEL: the box has nothing, git says 'mistral-medium-3.5-128b'", ""),
    ("an old value names both and the recipe", EXAMPLE,
     MATCHING.replace("medium-3.5-128b", "small-3.2-24b-instruct-2506"),
     [p.replace("medium-3.5-128b", "small-3.2-24b-instruct-2506") for p in N8N], 1,
     ["AI_MODEL: the box has 'mistral-small-3.2-24b-instruct-2506', git says",
      "Setting a plain configuration value"], ""),
    ("the last line for a name wins", EXAMPLE, MATCHING + "AI_MODEL=mistral-small\n", N8N, 1, "'mistral-small'", ""),
    ("quotes and a trailing comment are read the way compose reads them", EXAMPLE,
     'export AI_MODEL="mistral-medium-3.5-128b"\nSWEEP_CRON=0 * * * *   # hourly\n', N8N, 0, "match", ""),
    ("a value set in .env but not in the running n8n says to recreate it", EXAMPLE, MATCHING,
     ["AI_MODEL=mistral-small", "SWEEP_CRON=0 * * * *"], 1, "docker compose up -d n8n", "the box has"),
    ("no n8n container compares .env alone", EXAMPLE, MATCHING, None, 0, "only .env was read", ""),
    ("a marker with another comment under it stops the check",
     EXAMPLE.replace("# box: must match\nAI_MODEL", "# box: must match\n# Production: medium\nAI_MODEL"),
     MATCHING, N8N, 2, "must sit directly above", ""),
    ("a marker with a blank line under it stops the check",
     EXAMPLE.replace("# box: must match\nSWEEP_CRON", "# box: must match\n\nSWEEP_CRON"),
     MATCHING, N8N, 2, "must sit directly above", ""),
    ("an escaped quote is read the way compose reads it", EXAMPLE.replace("AI_MODEL=mistral-medium-3.5-128b", 'AI_MODEL=a"b'),
     MATCHING.replace("AI_MODEL=mistral-medium-3.5-128b", 'AI_MODEL="a\\"b"'), None, 0, "match", ""),
    ("a marked value compose would expand stops the check", EXAMPLE.replace("AI_MODEL=mistral", "AI_MODEL=$PREFIX-mistral"),
     MATCHING, N8N, 2, "contains $", ""),
    ("a marked URL carrying a login stops the check without printing it",
     EXAMPLE.replace("# box: must match\nSWEEP_CRON=0 * * * *", "# box: must match\nFORM_URL=https://user:hunter2@example.org"),
     MATCHING, N8N, 2, "FORM_URL looks like it holds a credential", "hunter2"),
    ("a marked secret stops the check without printing it",
     EXAMPLE.replace("AI_API_KEY=", "# box: must match\nAI_API_KEY="), MATCHING, N8N, 2,
     "AI_API_KEY looks like it holds a credential", "not-a-real-key"),
    ("an example with no marker cannot pass by comparing nothing",
     EXAMPLE.replace("# box: must match\n", ""), MATCHING, N8N, 2, "nothing to compare", ""),
    ("a box with no .env cannot run the check", EXAMPLE, None, N8N, 2, "could not run", ""),
]


def run(example, box, n8n, workdir):
    os.makedirs(os.path.join(workdir, "automation", "infra"), exist_ok=True)
    with open(os.path.join(workdir, "automation", ".env.example"), "w") as handle:
        handle.write(example)
    env_path = os.path.join(workdir, "automation", "infra", ".env")
    if box is None:
        if os.path.exists(env_path):
            os.remove(env_path)
    else:
        with open(env_path, "w") as handle:
            handle.write(box)
    n8n_path = os.path.join(workdir, "n8n-env.json")
    with open(n8n_path, "w") as handle:
        json.dump(n8n, handle)
    env = dict(os.environ, OSE_N8N_ENV_FILE=n8n_path)
    result = subprocess.run([sys.executable, "-I", SCRIPT, workdir], capture_output=True, text=True, env=env)
    return result.returncode, result.stdout + result.stderr


def main():
    failures = 0
    with tempfile.TemporaryDirectory(prefix="ose-env-drift-") as workdir:
        for name, example, box, n8n, want_code, must, must_not in CASES:
            code, out = run(example, box, n8n, workdir)
            problems = []
            if code != want_code:
                problems.append(f"exit {code}, wanted {want_code}")
            for text in [must] if isinstance(must, str) else must:
                if text and text not in out:
                    problems.append(f"no '{text}'")
            if must_not and must_not in out:
                problems.append(f"says '{must_not}'")
            if len(out.strip().splitlines()) > 14:
                problems.append(f"{len(out.strip().splitlines())} lines, more than the alert shows")
            failures += 1 if problems else 0
            print(f"{'ok  ' if not problems else 'FAIL'} {name}" + ("" if not problems else ": " + "; ".join(problems)))
            if problems:
                print("     | " + out.strip().replace("\n", "\n     | "))
    print("check-env-drift: all cases passed" if not failures else f"check-env-drift: {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
