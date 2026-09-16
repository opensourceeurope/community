# OSE Community Repository — Agent Instructions

This repository is the home for community-related discussions, governance processes, and shared resources for projects under the Open Source Europe (OSE) umbrella. Content is licensed under CC-BY-4.0, except `automation/`, which holds software licensed under MIT.

## Authorship Rules

- **NEVER add `Co-Authored-By:` with yourself as a co-author of any commit.** Agents are assistants and tools — they are not authors. Only humans can be authors of commits.
- AI assistance disclosure belongs in the pull request description using the exact format below — not in commit authorship metadata:
  ```
  Generated-by: <Agent Name and Version> following [AI Policy](https://github.com/opensourceeurope/.github/blob/main/AI-POLICY.md)
  ```

## Development Workflow

- **Never edit or develop on `main`.** Every change goes through a topic branch and a
  pull request. A `PreToolUse` hook enforces this: the shared main checkout is read-only
  for edits, and edits on `main`/`master` are denied in any worktree.
- Create a worktree per topic — `.claude/scripts/worktree.sh new <branch-name>` makes
  `.worktrees/<branch-name>` off a freshly fetched `origin/main` — then work in that
  directory. A single checkout has one `HEAD`, so concurrent sessions sharing it fight
  over the branch; a worktree pins one branch to one directory.
- `.claude/scripts/worktree.sh list` / `rm <branch-name>` manage them. `.worktrees/` is
  gitignored.

## Handling Secrets

- **Never print a secret to check it.** Print a length, with an explicit `if` —
  `${V:+set}${V:-EMPTY}` and similar expansions resolve to the *value* whenever
  the variable is set, and that leaked a live API key into a session transcript
  here. Use `if [ -n "$V" ]; then echo "set, ${#V} chars"; else echo EMPTY; fi`.
- **Never paste a secret into a chat, issue, commit or agent transcript.** If one
  lands there, it is burned: rotate it rather than hoping. Two host passwords and
  one API key were lost this way in a single session.
- **Set a secret where it is read**, not through a pipeline you cannot see: a TTY
  prompt on the target host, or an editor on the file. A piped `read` that
  captures nothing writes an empty value and every step still reports success.
- **Confirm by effect, not by echo** — e.g. `docker compose up -d` printing
  `Recreated` rather than `Running` proves the value changed.
- **A secret must never be tracked by git.** That is the hard rule. `.env`,
  `.env.*` and `ovh.conf` are gitignored, and `.env.example` holds names only.
- Acceptable homes: `.env` on the host (mode 600), n8n's own credential store,
  the shared password vault, or a mode-600 file on the machine that uses the
  credential — `~/.ovh.conf`, `~/.n8n-api-key`, or a gitignored `.env` in a local
  checkout. A gitignored file inside a repo tree is fine; note only that anything
  handling the whole tree — an archive, a backup, an agent reading the repo — sees
  it, which `$HOME` avoids.
- **Never put a credential in the compose `.env` unless the container needs it.**
  Every variable there is injected into n8n's environment, so parking n8n's own
  API key in it hands the container a key to its own API for no benefit.
- A credential belongs on whichever machine actually uses it. Do not move one
  onto a server for the feeling of safety: the API calls are TLS-protected either
  way, and the detour just forces every command through `ssh`.

## Shell Scripts

- Task scripts (`automation/infra/*.sh`, `.claude/scripts/*.sh`) use
  `set -euo pipefail`. Hooks (`.claude/hooks/*.sh`) deliberately use only
  `set -u`: a hook that aborts partway through would emit a wrong allow/deny
  decision instead of no decision.
- Under `pipefail`, **never use `grep -q` in a pipeline**: it exits on the first
  match, the upstream command dies of SIGPIPE, and the pipeline reports failure
  *because* the pattern was found. Use `grep -c` and test the count. This has
  already caused a verification script to declare a perfectly good backup
  broken.

## Automation Workflows (`automation/n8n/`)

The n8n instance at automation.opensourceeurope.org runs the collective
application pipeline.

**Always invoke the `n8n-skills` plugin's `n8n` skill before touching any
workflow** — never build or edit workflows from memory. If the skill is not
listed, or its `n8n_*` management tools are absent, do not work around it:
tell the user the plugin is missing or not connected, and recommend
installing/enabling it and setting `N8N_API_URL` / `N8N_API_KEY` first (see
"Providing the n8n API key to Claude Code" in `automation/README.md`).

The rules below are the OSE-specific invariants on top of that skill:

- **The AI review is advisory only.** No workflow may approve, reject or
  close an application — that happens on Open Collective, by a person, per
  the [AI policy](https://github.com/opensourceeurope/.github/blob/main/AI-POLICY.md).
- **Only public project material goes to the model.** Never a name or an
  email address.
- **Every applicant-facing email site checks `DRY_RUN`.** Each email node is fed by a
  render Code node that reads `DRY_RUN`: when true, the message goes to
  `DRY_RUN_RECIPIENT` with the intended recipient named in the subject, and the row
  update that follows sets `dry_run`. Copy this pattern for every new email send. Slack
  messages and reactions are internal, carry no applicant address, and always post to
  `SLACK_CHANNEL`, in a dry run as well.
- **Every stage change replies in the application's Slack thread.** The parent message is
  posted by apply 1a or 1b and its channel and timestamp live on the row in
  `slack_channel_id` and `slack_thread_ts`. A stage that has no reply in Slack is
  invisible to reviewers, so add one with the stage. A row without a thread anchor is
  skipped rather than posted loose in the channel.
- **Workflows coordinate only through the `ose_applications` data table**
  and never call each other. Stages move forward
  only; writes are idempotent — insert only when the slug is new, guard
  terminal updates on the current stage.
- **The table reference in the workflows must always match the live table.**
  Every data table node references `ose_applications` the same way (currently
  by name). If the table is renamed, recreated, or the reference mode is ever
  changed, that same change must update **every** data table node in **every**
  workflow, refresh the exports, and re-validate — a half-updated reference
  fails silently, not loudly.
- **Timers are derived from timestamps** by the scheduled runs, never from
  Wait nodes.
- **Config comes from the env vars in `automation/.env.example`**; credentials
  are referenced by name from n8n's credential store, never inline.
- **Before any test with shortened timers: set `ONLY_SLUGS`, keep
  `DRY_RUN=true`.** The instance runs against production Open Collective data.
- **Email copy lives in `automation/emails/*.md`** and is embedded verbatim
  in the render step of whichever workflow sends it — change both in the same
  PR, and refresh the export of every changed workflow into `automation/n8n/`
  using `automation/scripts/export-workflows.py`, which preserves the existing
  node and key order so the diff shows what changed rather than how the API
  ordered its response.
- **Name workflows `apply <step> — <what it does>`** — long and descriptive,
  so the list reads in pipeline order.
- **OC webhooks carry no application data** (`data: {}`). Treat every event
  as a ping and re-fetch from the GraphQL API.
- **Never write adjacent closing braces inside a `{{ }}` expression.** n8n
  ends the expression at the first `}}`, so inline GraphQL or nested JSON
  must space consecutive closing braces (`} }`). The truncation shows up as
  `[ERROR: invalid syntax]` in the UI and `validate_workflow` does not catch
  it. This is about braces nested inside the expression body. The expression's
  own closing delimiter is always `}}`.
- **Never activate a workflow without asking.** New and changed workflows are
  deployed inactive; activation is the user's explicit call, every time.

## Commit Conventions

- Use conventional commits: `feat:`, `fix:`, `docs:`, `ci:`, `chore:`
- Two licences apply by path: everything under `automation/` is MIT (software);
  everything else is CC-BY-4.0 (content). Do not introduce material that is
  incompatible with the licence of the path you are touching.
