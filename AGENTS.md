# OSE Community Repository — Agent Instructions

This repository is the home for community-related discussions, governance processes, and shared resources for projects under the Open Source Europe (OSE) umbrella. Content is licensed under CC-BY-4.0, except `automation/`, which holds software licensed under MIT.

## Authorship Rules

- **NEVER add `Co-Authored-By:` with yourself as a co-author of any commit.** Agents are assistants and tools — they are not authors. Only humans can be authors of commits.
- **Keep the pull request description under 350 prose words.** CI enforces it
  (`.github/scripts/check-pr-body.py`), and the limit was calibrated against every
  description in this repository. Code blocks, tables, headings and URLs are not
  counted, so move detail into them rather than cutting the evidence. An empty or
  near-empty description fails too.
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
- **A squash merge ends the branch. Check before you push to it again.** Pull
  requests here are squash-merged, so the branch's own commits never appear in
  `main` and `git log origin/main..HEAD` keeps listing them as if nothing had
  landed. Work pushed to that branch afterwards belongs to no open pull request
  and is invisible until someone asks. This has already happened: seven commits
  sat orphaned while every one of them was live on the n8n instance, so `main`
  described a pipeline that no longer existed. Before continuing on a branch,
  run `gh pr view <n> --json state`, and open a new pull request rather than
  pushing into a merged one. Compare content, not commits: the squashed commits
  will always look missing.

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
- **No address column is ever given to the model.** The intake review sees the
  public Open Collective page. The summary in apply 5 additionally sees the
  applicant's form answers and the project's README, because a reviewer needs
  the form read for them and the form is where the thin descriptions get filled
  in. `contact_email` and `applicant_email` are never included: every render
  node selects from a fixed key list rather than iterating the row, so sending
  an address takes a deliberate edit rather than an oversight. State the rule
  this way round and not as "no personal data reaches the model", which is a
  promise the pipeline cannot keep: form answers and READMEs are free text, and
  an applicant who signs a comment with their name and address has sent them.
- **A URL an applicant supplied is a request they are asking this server to
  make.** apply 5 fetches a README from the repository they named, which is the
  only place the pipeline reaches a host of someone else's choosing. It is
  `https` only, never an IP literal, never `localhost` or a `.local` or
  `.internal` name, and no redirects at all: a repository link resolves in one
  hop or it does not resolve, because every host check runs on the first URL and
  a redirect would hand the applicant's server the choice of where the request
  lands. The body is fetched as text so that nothing parses it, and only its
  first 8000 characters reach a model. n8n's HTTP helper has no response size
  option to set, so the ten second timeout and that truncation are the only
  bounds on it. A Code node cannot resolve DNS, so a hostname pointing at a
  private address still passes; what makes that tolerable is that nothing on the
  box listens on a private interface over HTTPS. Widen this and that reasoning
  has to be redone.
- **Every applicant-facing email site checks `DRY_RUN`, and the check fails safe.** Each
  email node is fed by a render Code node that reads `DRY_RUN`: the pipeline suppresses
  applicant email unless the variable is explicitly and exactly `false`, after trimming
  and lowercasing. A suppressed message goes to `DRY_RUN_RECIPIENT` with the intended
  recipient named in the subject, and the row update that follows sets `dry_run`. Unset,
  empty, `1`, `yes` and `true ` with a trailing space all suppress, so a dropped or
  mistyped line in `.env` cannot quietly start mailing real applicants. Going live is the
  deliberate act of writing `DRY_RUN=false`. Copy this pattern, the inversion included,
  for every new email send. Slack messages and reactions are internal, carry no applicant
  address, and always post to `SLACK_CHANNEL`, in a dry run as well.
- **Applicant text is data, not Slack markup.** Slack reads `<...>` as a control
  sequence, so an applicant who writes `<!channel>` or
  `<https://evil.example|opencollective.com/theirs>` into a collective description or a
  form answer gets a mention or a masked link in the reviewers' channel. Every Code node
  that builds Slack text escapes `&` to `&amp;`, `<` to `&lt;` and `>` to `&gt;`, `&`
  first, on the values that come from an applicant, a collective's public page or a
  model. The fixed wording is left alone so it keeps its `*bold*` and its emoji. Copy the
  `escapeSlack` helper the render nodes already share rather than writing a second
  spelling of it.
- **Every stage change replies in the application's Slack thread.** The parent message is
  posted by apply 1a or 1b and its channel and timestamp live on the row in
  `slack_channel_id` and `slack_thread_ts`. A stage that has no reply in Slack is
  invisible to reviewers, so add one with the stage. A row without a thread anchor is
  skipped rather than posted loose in the channel.
- **Continuing on a Slack error protects the stage, not whatever follows.**
  Slack nodes carry `onError: continueRegularOutput` so one unreachable row
  cannot abort a run and starve the others. It does NOT mean the work after the
  post should proceed as if it had succeeded. A failed post continues as an
  error item with no `message.ts`, so any data table write after a Slack node
  needs a guard that drops those items first. Both intake workflows and apply 5
  have one. Without it the row records something the thread never received, and
  because the sweeps select on that same field being empty, it is never retried.
- **The row is the record, and anything triggered directly also has a sweep.**
  Workflows coordinate through the `ose_applications` data table, and a hand-off
  passes no data because the callee selects its own rows. Four hand-offs move an
  application through in one pass: apply 1a and apply 1b call apply 2 once the
  row has its Slack thread anchor, apply 2 calls apply 3 once it has written
  verdicts, and apply 4 calls apply 5 so that a summary is immediate. Each one is
  an Execute Workflow node with `waitForSubWorkflow` false and
  `onError: continueRegularOutput`, so nothing blocks on a later stage and a
  failed hand-off never fails the caller. Adding one requires the callee to be
  activated first: n8n refuses to save an active workflow that references a
  sub-workflow that has never been activated. Every stage keeps its schedule,
  and that is what makes a direct trigger safe to add: a lost hand-off must cost
  one sweep interval, never an application. Stages move forward only; writes are
  idempotent, so insert only when the slug is new and guard terminal updates on
  the current stage.
- **A stage claims its row before it does the work.** Between selecting the
  candidate rows and the first expensive step, the model call in apply 2 and
  apply 5 or the email in apply 3, the workflow stamps its claim column with a
  conditional update whose filter names the value it just read, null included.
  That makes it a compare and swap rather than a hopeful write: a non-empty
  result means this run won the row and does the work, an empty result means
  another run claimed it first and the row is skipped in silence. Without it,
  sweeps sharing one cron work the same rows in parallel, which is how one
  application got two summaries in its thread. A claim older than
  `STALE_CLAIM_MINUTES` is claimable again, so a run that dies cannot strand an
  application. The three claim columns, and what separates a claim from a
  result, are in `automation/docs/data-tables.md`.
- **An active workflow cannot reference an unpublished sub-workflow.** n8n only
  creates a published version of a workflow the first time it is activated, and
  saving a change to an active workflow requires every sub-workflow its Execute
  Workflow nodes point at to already have one. Wiring apply 4 to call apply 5
  failed against the live instance for exactly this reason: apply 5 had never
  been activated, so apply 4 could not be saved. Activating a sub-workflow ahead
  of its caller is not a way around "never activate a workflow without asking."
  It is the same rule: ask first, every time.
- **The table reference in the workflows must always match the live table.**
  Every data table node references `ose_applications` the same way (currently
  by name). If the table is renamed, recreated, or the reference mode is ever
  changed, that same change must update **every** data table node in **every**
  workflow, refresh the exports, and re-validate — a half-updated reference
  fails silently, not loudly.
- **A new form question lands in five places, not one.** The form pages do not
  accumulate answers: every storage node rebuilds the whole answers object from
  a hardcoded key list. So a field added to page 2 must also go into
  `Persist page 2`, `Persist page 3` and `Record submission`, and a field added
  to page 3 into `Persist page 3` and `Record submission`. Then two
  hardcoded label lists read those keys and will silently omit anything missing
  from them: `Render submission thread reply` in apply 4, which is what a
  reviewer reads in Slack, and `Render summary request` in apply 5, which is what
  the model is told. Miss one storage expression and the answer vanishes when the
  applicant clicks to the next page, with no error anywhere. A form field has
  no description property, so a question that needs a line of explanation is
  two entries: the field, then a Custom HTML element holding the sentence.
  Leave that element's `elementName` empty and it stays out of the form
  output, which is what keeps it out of `answers` and off the five places
  above.
- **Page 2 is two nodes, and nothing may reach back past that branch.** The page
  1 question `What are you applying as?` sends a community, meetup or events
  applicant to `Form page 2 for communities` and everyone else to `Form page 2`,
  so on any given run one of the two never executes. A `$('Form page 2')`
  reference on the community branch resolves to nothing, and the answer is gone
  with no error anywhere. That is why `Persist page 2` reads its page 2 answers
  from `$json`, whichever node just ran, and why `Persist page 3` and
  `Record submission` read them from `Read the stored answers`, the row both
  branches wrote. Page 3 and page 4 are on every path, so those two are still
  referenced by node name. A new page 2 question goes on whichever variant it
  belongs to, and its key still lands in all three storage expressions and both
  label lists.
- **You cannot check the form by fetching it, and n8n filters what it renders.**
  The form is a client-rendered app, so the served HTML contains none of the
  questions, the styling or the custom HTML. Grepping it proves nothing about
  what an applicant sees, and claiming otherwise has already been done here and
  been wrong. n8n also filters both the `customCss` and the Custom HTML
  elements before rendering: a raw `<svg>` is stripped, which is why the
  wordmark is a percent-encoded data URI. So anything beyond plain text in a
  form field is verified by opening the page, not by reading the export back.
  Saving without an error only proves n8n stored it.
- **The form's field labels are part of the emailed link.** n8n keys prefill
  query parameters on a field's label, so the invitation and reminder emails
  build a link containing the page 1 label `Your collective's Open Collective
  URL`, percent-encoded. Rename that field and the prefill stops working
  silently: the form still loads, just empty. Change the label and the two
  render nodes in apply 3 in the same PR.
- **Timers are derived from timestamps** by the scheduled runs, never from
  Wait nodes.
- **Config comes from the env vars in `automation/.env.example`**; credentials
  are referenced by name from n8n's credential store, never inline.
- **Before any test with shortened timers: keep `DRY_RUN=true`.** The
  instance still runs against production Open Collective data, and DRY_RUN is
  the control that keeps mail off real applicants. It is not a control on state:
  a test run still advances real rows, so an application marked `form_invited`
  during a test never receives a real invitation afterwards. Worse, submitting
  the form during a rehearsal writes the tester's address into `contact_email`,
  and the closing email prefers that over `applicant_email`, so the real
  applicant would never be told the outcome. Delete any row a rehearsal
  touched rather than trying to repair it: the sweep recreates it from Open
  Collective, and clearing timestamps alone leaves the address behind.
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
- **n8n is credited in the documentation, not in every message.** The
  `appendAttribution` option is off on the form, the applicant emails and the
  Slack messages. That is a supported n8n feature, documented and shipped in the
  free product, so switching it off is not a licence breach: the clause about not
  removing the licensor's notices is aimed at stripping them from the software,
  not at using a setting n8n built and labelled. n8n's white-labelling agreement
  is a separate matter and covers embedding the n8n editor, which this project
  does not do. The credit belongs somewhere a reader will actually see it, so
  name n8n in the public documentation and in the AI and tooling policy, and keep
  it accurate as the stack changes.
- **Six of these rules are enforced, the rest are on you.**
  `automation/scripts/check-workflows.py` runs on every pull request and checks
  the exports for disabled nodes, truncated expressions, an email send whose
  render step does not read `DRY_RUN`, an answer key missing from a label list,
  a data table referenced any way but by name, and a workflow with no timezone.
  Run it locally before you push. Each check is there because that defect
  reached main at least once. It reads the exports, so it only sees what you
  exported: refresh them first or it is checking yesterday's workflow.
- **There is no staging. Editing an active workflow publishes it.** One n8n
  instance runs this pipeline and it is production. A save against an active
  workflow becomes the running version immediately, so a change is live before
  its pull request exists. The PR is the record, not the gate: it exists so the
  exports match what is running, which is what a restore rebuilds from. Two
  things follow. Never leave a workflow saved in a state that would be wrong if
  a sweep fired that minute, which means giving a callee its trigger before
  giving a caller its hand-off, and writing each workflow in one atomic update
  rather than a sequence of partial ones. And refresh the exports in the same
  sitting: a change that is live but unexported is a change that a restore
  silently undoes.
- **Never activate a workflow without asking.** New and changed workflows are
  deployed inactive; activation is the user's explicit call, every time.

## Commit Conventions

- Use conventional commits: `feat:`, `fix:`, `docs:`, `ci:`, `chore:`
- Two licences apply by path: everything under `automation/` is MIT (software);
  everything else is CC-BY-4.0 (content). Do not introduce material that is
  incompatible with the licence of the path you are touching.
