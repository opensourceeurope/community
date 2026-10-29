# OSE Community Repository — Agent Instructions

Community discussions, governance processes and shared resources for projects
under the Open Source Europe (OSE) umbrella. Content is CC-BY-4.0, except
`automation/`, which is MIT-licensed software.

## Authorship Rules

- **NEVER add `Co-Authored-By:` with yourself as a co-author of any commit.**
  Agents are tools. Only humans author commits.
- **Keep the pull request description under 350 prose words.** CI enforces it
  (`.github/scripts/check-pr-body.py`). Code blocks, tables, headings and URLs
  do not count, so move detail there rather than cutting evidence. An empty or
  near-empty description fails too.
- **Disclose AI assistance in the pull request description**, never in commit
  metadata, in exactly this format:
  ```
  Generated-by: <Agent Name and Version> following [AI Policy](https://github.com/opensourceeurope/.github/blob/main/AI-POLICY.md)
  ```

## Development Workflow

- **Never edit or develop on `main`.** Every change goes through a topic branch
  and a pull request. A `PreToolUse` hook makes the shared main checkout
  read-only and denies edits on `main`/`master` in any worktree.
- **Work in a worktree per topic.** `.claude/scripts/worktree.sh new
  <branch-name>` makes `.worktrees/<branch-name>` off a freshly fetched
  `origin/main`, so concurrent sessions do not fight over one `HEAD`. `list` and
  `rm <branch-name>` manage them. `.worktrees/` is gitignored.
- **A squash merge ends the branch. Check before you push to it again.**
  `git log origin/main..HEAD` still lists a squash-merged branch's commits, and
  later pushes belong to no open pull request. Run `gh pr view <n> --json state`
  first, and open a new pull request rather than pushing into a merged one.
  Compare content, not commits.

## Handling Secrets

- **Never print a secret to check it.** Print its length with an explicit `if`.
  `${V:+set}${V:-EMPTY}` and similar expansions print the *value* whenever the
  variable is set. Use
  `if [ -n "$V" ]; then echo "set, ${#V} chars"; else echo EMPTY; fi`.
- **Never paste a secret into a chat, issue, commit or agent transcript.** One
  that lands there is burned. Rotate it.
- **Set a secret where it is read**, by a TTY prompt on the target host or an
  editor on the file, never through a pipeline you cannot see. A piped `read`
  that captures nothing writes an empty value and still reports success.
- **Confirm by effect, not by echo**, for example `docker compose up -d`
  printing `Recreated` rather than `Running`.
- **A secret must never be tracked by git.** That is the hard rule. `.env`,
  `.env.*` and `ovh.conf` are gitignored, and `.env.example` holds names only.
- **Acceptable homes** are `.env` on the host (mode 600), n8n's credential
  store, the shared password vault, or a mode-600 file on the machine that uses
  it: `~/.ovh.conf`, `~/.n8n-api-key` or a gitignored `.env` in a local
  checkout. Anything handling a whole repo tree sees a gitignored file in it,
  which `$HOME` avoids.
- **Never put a credential in the compose `.env` unless the container needs
  it.** Every variable there reaches n8n's environment.
- **A credential belongs on the machine that uses it.** Do not move one onto a
  server for a feeling of safety. TLS protects the calls either way.
- **An agent never fetches a secret for the operator.** n8n's API does not
  return a credential's value, and what an agent reads lands in its
  transcript. Point the operator at the source, such as the Slack
  app page or the Scaleway console, to type it into the file on the machine that
  reads it. An agent may create a short-lived test key, written straight to a
  mode-600 file and never printed, and must delete it afterwards even when the
  test fails.
- **Never write the box's IP address or provider hostname into this
  repository.** It is public. Use `{VPS_IP}` or `{VPS_HOST}`. The vault holds
  the real values.

## Shell Scripts

- **Task scripts use `set -euo pipefail`, hooks only `set -u`.** Task scripts are
  `automation/infra/*.sh` and `.claude/scripts/*.sh`. A hook
  (`.claude/hooks/*.sh`) that aborts partway emits a wrong allow/deny decision.
- **Under `pipefail`, never use `grep -q` in a pipeline.** The upstream command
  dies of SIGPIPE on the first match and the pipeline fails *because* the
  pattern was found. Use `grep -c` and test the count.
- **Run `shellcheck` at its default severity before you push.** CI does, and
  `-S info` hides notes such as SC2001 and SC2015.
- **Commands for the operator's own machine carry no inline `#` comments.** Their
  zsh lacks `interactivecomments`, so `grep -c x # expect 3` passes `#`,
  `expect` and `3` as file names. Put the expectation in the prose. Commands
  run on the box, in bash, may keep comments.

## Automation Workflows (`automation/n8n/`)

The n8n instance at automation.opensourceeurope.org runs the collective
application pipeline.

**Always invoke the `n8n-skills` plugin's `n8n` skill before touching any
workflow.** Never build or edit workflows from memory. If the skill or its
`n8n_*` tools are absent, do not work around it: tell the user the plugin is
missing or not connected, and recommend installing or enabling it and setting
`N8N_API_URL` / `N8N_API_KEY` (see "Providing the n8n API key to Claude Code" in
`automation/README.md`).

The OSE-specific invariants on top of that skill:

- **The AI review is advisory only.** No workflow may approve, reject or close
  an application. A person does that on Open Collective, per the
  [AI policy](https://github.com/opensourceeurope/.github/blob/main/AI-POLICY.md).
- **No address column is ever given to the model.** The intake review sees the
  public Open Collective page, the apply 5 summary also the form answers and
  README. Render nodes select a fixed key list, never the whole row, and leave
  out `contact_email`, `applicant_email` and both `alternative_admin_*` columns.
  Both pass the whole user message through `redactAddresses`, which strips `@`
  addresses and their encoded forms in any script, and `(at)`, `[at]`, `{at}`
  and `<at>`, but not bare-word `name at example dot org` or a line-broken
  address. `check-workflows.py` check 9 fails a user message that is not one
  `redactAddresses(...)` call, a node naming an address column or reading the
  row whole, and differing copies of the helper. Widening the helper needs a
  case in `automation/test/model-input-redaction.mjs`. Never restate this as "no
  personal data reaches the model": free text can carry a name and phone number.
- **A URL an applicant supplied is a request they are asking this server to
  make.** apply 2 fetches repository evidence from the collective's page or
  application message, apply 5 a README from the form's repository, both through
  the same `Choose the repository URL` checks, changed together. `https` only.
  Refused: an IP literal in any spelling the resolver accepts (`127.1`,
  `0x7f.1`, `0177.0.0.1`), `localhost`, `.local` and `.internal` names, a port,
  a `user@host` prefix, a `.` or `..` path segment, and any redirect, since the
  host checks see only the first URL. The body is fetched as text with a ten
  second timeout, and only its first 8000 characters reach a model. n8n's HTTP
  helper has no response size option, so that timeout and truncation are the
  only bounds. apply 5 fetches the host's licence metadata under the same
  checks and keeps only an SPDX-shaped id. DNS is not checked, which
  is safe only while nothing on the box listens on a private interface over
  HTTPS. Widen this and redo that reasoning.
- **A Code node has no `URL`, no `fetch` and no `crypto`.** It has `Buffer`,
  `require`, `atob`, `TextEncoder` and the ECMAScript built-ins, but not `URL`,
  `URLSearchParams`, `fetch`, `AbortController`, `structuredClone`, `crypto`,
  `process`, `queueMicrotask`, `performance`, `Blob`, `Headers`, `Request` or
  `Response`. Each throws `ReferenceError`, so never wrap a global in a
  `try/catch` that also handles bad input, where it becomes a silent wrong
  answer. Parse URLs with a regex, as `Derive slug` (apply 4) and
  `Choose the repository URL` (apply 5) do. Plain `node` and
  `validate_workflow` miss this. `automation/test/readme-fetch.py` runs the node
  inside n8n in a container.
- **Test a model-facing change before merge, and keep it out of the PR
  description.** For a prompt, render node, licence check or `AI_MODEL` change,
  run `licence-check.py`, `readme-fetch.py` and, with the operator's key,
  `review-eval.mjs`. The eval covers apply 2 only, so check a summary change by
  running sample answers through the committed nodes.
- **Every applicant-facing email site checks `DRY_RUN`, and the check fails
  safe.** The render Code node feeding each email node mails the applicant only
  when `DRY_RUN` is exactly `false` after trimming and lowercasing. Otherwise
  the message goes to `DRY_RUN_RECIPIENT` with the intended recipient in the
  subject, and the following row update sets `dry_run`. Unset, empty, `1`, `yes`
  and `true ` with a trailing space all suppress. Going live is deliberately
  writing `DRY_RUN=false`. Copy this pattern, inversion included, for every new
  email send. Slack is internal, carries no applicant address and always posts
  to `SLACK_CHANNEL`, in a dry run too.
- **Applicant text is data, not Slack markup.** Slack reads `<...>` as control
  sequences, so `<!channel>` or
  `<https://evil.example|opencollective.com/theirs>` becomes a mention or masked
  link. Every Code node building Slack text escapes `&` to `&amp;`, `<` to
  `&lt;` and `>` to `&gt;`, `&` first, on values from an applicant, a
  collective's public page or a model. Fixed wording stays unescaped for its
  `*bold*` and emoji. Copy the shared `escapeSlack` helper, never a second
  spelling.
- **Every stage change replies in the application's Slack thread.** apply 1a or
  1b posts the parent, anchored by `slack_channel_id` and `slack_thread_ts` on
  the row. A stage without a reply is invisible to reviewers. A row without an
  anchor is skipped, never posted loose in the channel.
- **Continuing on a Slack error protects the stage, not whatever follows.**
  `onError: continueRegularOutput` keeps one bad row from aborting a run, NOT
  permission to proceed as if the post succeeded. A failed post continues with
  no `message.ts`, so a data table write after a Slack node needs a guard that
  drops those items first, as both intake workflows and apply 5 have. Otherwise
  the row records a post that never happened, and the sweep never retries it.
- **The row is the record, and anything triggered directly also has a sweep.**
  Workflows coordinate through the `ose_applications` data table, and hand-offs
  pass no data because the callee selects its own rows. apply 1a and 1b call
  apply 2 once the thread anchor exists, apply 2 calls apply 3 after verdicts,
  apply 4 calls apply 5. Each is an Execute Workflow node with
  `waitForSubWorkflow` false and `onError: continueRegularOutput`, so a failed
  hand-off never fails the caller. n8n refuses to save an active caller whose
  callee was never activated, so the callee must be activated first, which you
  ask for like any activation. Every stage keeps its schedule, so a lost
  hand-off must cost one sweep interval, never an application. Stages move
  forward only. Writes are idempotent: insert only for a new slug, and guard
  terminal updates on the current stage.
- **A stage claims its row before it does the work.** Before the first expensive
  step (the model call in apply 2 and apply 5, the email in apply 3), it stamps
  its claim column with an update filtered on the value it just read, null
  included. A non-empty result means this run won the row. An empty one means it
  is skipped in silence. Otherwise sweeps on one cron work rows twice. A claim
  older than `STALE_CLAIM_MINUTES` is claimable again. The claim columns are in
  `automation/docs/data-tables.md`.
- **The column table in `automation/docs/data-tables.md` builds the data
  table.** `automation/scripts/create-data-table.py` creates `ose_applications`
  from it, so document a column a workflow writes in the same change.
- **Workflow ids belong to one instance.** Execute Workflow targets and
  `settings.errorWorkflow` name workflows by id, and `export-workflows.py` maps
  ids to files. Replace them in git after the first
  `deploy.sh --allow-create` on a new instance, per the first-install checklist
  in `automation/infra/README.md`.
- **An active workflow cannot reference an unpublished sub-workflow.** n8n
  publishes a workflow only on first activation, and saving an active workflow
  needs every sub-workflow it calls published. Activating a sub-workflow first
  still falls under "never activate a workflow without asking". Ask first,
  every time.
- **The table reference in the workflows must always match the live table.**
  Every data table node references `ose_applications` the same way, currently
  by name. A rename, a recreation or a new reference mode must update **every**
  data table node in **every** workflow, refresh the exports and re-validate in
  one change. A half-updated reference fails silently.
- **A new form question lands in several places, not one.** Storage nodes rebuild
  the answers object from hardcoded keys, so a page 2 field goes into
  `Persist page 2`, `Persist page 3` and `Record submission`, a page 3 field
  into the last two, or it silently vanishes on the next page. It goes into the
  `Form page 6` field list so the applicant can correct it, and into the label
  lists that silently omit missing keys: `Render submission thread reply`
  (apply 4, for Slack) and `Render summary request` (apply 5, for the model).
  A question that is not about the application, today only `form_feedback`,
  goes in the Slack list alone, and `check-workflows.py` fails if the summary
  list has it. A field has no
  description property, so explanation goes in a following Custom HTML element
  with an empty `elementName`, which keeps it out of `answers`.
- **The last page is built from the row, and it is the only way back.** n8n
  forms have no back button, so `Form page 6` shows every answer prefilled
  through `defaultValue` for correction. It alone sets `defineForm: json`, its
  fields an expression over `Read the stored answers again`, covering both
  page 2 variants and both legal shapes. Shown as text, not fields: the page 1
  dropdown, the Open Collective URL and the legal entity answer, which earlier
  branches switched on, and the contact address `Mark form started` wrote.
  `Record submission` reads that page with `??`, not `||`, so a cleared
  optional answer stays cleared and the row fills only fields the page lacked.
- **Two form pages are branched, and nothing may reach back past a branch.**
  Page 2 splits on `What are you applying as?`, page 4 on
  `Is the project already registered as a legal entity?`. One node of each pair
  never runs, so `$('Form page 2')` or `$('Form page 4')` silently resolves to
  nothing. A persist node reads its own page from `$json`. Earlier pages come
  from the row, via `Read the stored answers` before page 3 and
  `Read the stored answers again` before page 5, never by node name. Pages 1, 3
  and 5 are on every path and may be named. A new question goes on whichever
  variant it belongs to, and its key still lands in every downstream storage
  expression and in the label lists.
- **A form field's label is its lookup key.** Storage expressions read
  `$json['the exact label']`, so a rename silently drops the answer. Update the
  readers in the same change, checked by walking the connection graph, since
  validation misses this. A label with an apostrophe uses `$json["..."]`.
- **You cannot check the form by fetching it, and n8n filters what it renders.**
  The served HTML of this client-rendered form holds no questions, styling or
  custom HTML. n8n filters `customCss` and Custom HTML: a raw `<svg>` is
  stripped, hence the percent-encoded data URI wordmark, and only `http` and
  `https` links keep their `href`, so a `mailto:` link does nothing.
  `target="_blank"` is kept, so a link to another site opens in a new tab and
  never takes the applicant off the form. Write an email address as text, in
  `<strong>` if needed. A `class` attribute is stripped too, so style an element
  through the `.form-group.html` wrapper n8n renders around it. Verify anything
  beyond plain text by opening the page. A throwaway local container on the
  production image, with the form node imported and published, renders it
  faithfully. Saving without an error only proves n8n stored it.
- **The form's field labels are part of the emailed link.** Prefill parameters
  are keyed on labels, so the invitation and reminder emails embed the page 1
  label `Your collective's Open Collective URL`, percent-encoded. A rename
  silently loads the form empty. Change the invitation and reminder render nodes
  in apply 3 in the same PR. `check-workflows.py` check 7 catches a missed one,
  and the dropdown options the Switch nodes compare against.
- **A decision is read from the applicant's public account fields**, never from
  `account.hostApplicationRequests`, which needs collective admin the host-admin
  token cannot have. `host.slug` matching the row's `host_slug` with
  `isApproved` is an approval, a null `host` a rejection, and a different `host`
  `resigned`. Any node reading an Open Collective response must throw on an
  `errors` array rather than skip the item, except
  `No collective found with slug <slug>` for the row's own slug, which closes
  that deleted collective as `resigned`. An error about one row must never stop
  the others, so such an exception is matched exactly and covers that row alone.
- **An outcome the applicant did not ask for tells them nothing.** `approved`
  and `rejected` email the applicant, who is owed the answer. `resigned` is not
  a decision, so its branch ends at the data table and Slack thread with no
  email node, and a misread host costs only a row edit. Keep any future outcome
  of this kind on a branch that cannot reach a send.
- **Timers are derived from timestamps** by the scheduled runs, never from Wait
  nodes.
- **Config comes from the env vars in `automation/.env.example`.** Credentials
  are referenced by name from n8n's credential store, never inline.
- **Before any test with shortened timers: keep `DRY_RUN=true`.** The instance
  uses production Open Collective data. DRY_RUN protects mail, not state: a test
  still advances real rows, so a row marked `form_invited` never gets its real
  invitation, and a rehearsal submission puts the tester's address in
  `contact_email`, which the closing email prefers over `applicant_email`.
  Delete any row a rehearsal touched rather than repairing it. The sweep
  recreates it, and clearing timestamps alone leaves the address behind.
- **Email copy lives in `automation/emails/*.md`** and is embedded verbatim in
  the sending workflow's render step. Change both in the same PR, and refresh
  every changed workflow's export into `automation/n8n/` with
  `automation/scripts/export-workflows.py`, which keeps node and key order for
  a clean diff.
- **Name workflows `apply <step> — <what it does>`**, long and descriptive, in
  pipeline order. A workflow that is not a step uses `ops — <what it does>`.
- **What an OC webhook carries depends on its type.** `collective.apply` has
  `data: {}` and only the top-level `CollectiveId`, so the application is
  re-fetched from the GraphQL API. `collective.approved` and
  `collective.rejected` carry `data.collective` (id, slug, name) and
  `data.host`. apply 1a picks the row by that slug and confirms with a single
  API call for that collective, never acting on the payload alone, because the
  webhook is unauthenticated and its URL public. An event naming no collective
  matches no row, and the apply 1b sweep catches anything missed. Check a real
  webhook execution before writing a rule about the payload.
- **Never write adjacent closing braces inside a `{{ }}` expression.** n8n ends
  the expression at the first `}}`, so space them (`} }`) in inline GraphQL or
  nested JSON. The UI shows `[ERROR: invalid syntax]` and `validate_workflow`
  misses it. The expression's own closing delimiter stays `}}`.
- **n8n is credited in the documentation, not in every message.**
  `appendAttribution` is off on the form, applicant emails and Slack messages, a
  documented setting and no licence breach. n8n's white-labelling agreement
  covers embedding the editor, which this project does not do. Name n8n in the
  public documentation and the AI and tooling policy, kept accurate.
- **Some of these rules are enforced, the rest are on you.**
  `automation/scripts/check-workflows.py` runs on every pull request and fails
  disabled nodes, truncated expressions, an email render step not reading
  `DRY_RUN`, an answer key missing from a label list, a data table not
  referenced by name, a workflow with no timezone, a stale form label or
  dropdown option, a `$env` read missing from `docker-compose.yml` or
  `automation/.env.example`, and a model request that skips redaction or reads
  an address column. Refresh the exports, which is all it reads, and run it
  locally before you push.
- **There is no staging. Editing an active workflow publishes it.** The one n8n
  instance is production, so a save is live before its pull request exists. The
  PR is the record, not the gate. Never leave a workflow saved in a state that
  would be wrong if a sweep fired that minute: give a callee its trigger before
  a caller its hand-off, and write each workflow in one atomic update. Refresh
  the exports in the same sitting. The deploy timer restores the version from
  `main` within ten minutes, so merge the export first or the edit vanishes.
- **Never activate a workflow without asking.** New and changed workflows are
  deployed inactive. Activation is the user's explicit call, every time.

## Infra on the VPS (`automation/infra/`)

- **A merge to `main` changes production infra, not only workflows.** With
  `ose-deploy.timer` installed, `deploy.sh` applies a changed
  `docker-compose.yml`, `Caddyfile`, unit file or `apt/` setting within ten
  minutes, switches on every timer in git but `ose-deploy.timer`, then syncs the
  workflows. A new n8n tag upgrades production on the next run, after a backup.
  Write the pull request knowing it deploys itself.
- **Nothing is set up on the box by hand.** A unit, timer or setting the box
  needs is a file in `automation/infra/` that `deploy.sh` installs. Never write
  "run this on the box" into a pull request as a step. Prefer a check that runs
  on every deploy over one triggered by a file change, because the run that
  brings a feature still executes the previous `deploy.sh`.
- **A new variable in the compose file must reach the box's `.env` before the
  merge.** `deploy.sh` never edits `.env`, and a compose file naming a variable
  `.env` lacks stops the whole run. Say so in the pull request. Mark a
  non-secret variable `# box: must match` in `.env.example` when the box must
  run its value, and `ose-env-drift.timer` alerts hourly until it does. A
  workflow reading a new `$env` variable needs it in the compose file and
  `.env.example` too, or CI fails. Read it as `$env.NAME`, because CI fails a `$env` use it
  cannot name, such as destructuring.
- **A new data table column is added by the deploy, from the docs.** Every run
  calls `create-data-table.py` from `automation/docs/data-tables.md` before the
  workflow sync, and stops if it cannot add one. Never add a column by hand
  ahead of a merge.
- **An empty required value stops the deploy too.** `REQUIRED_VALUES` in
  `deploy.sh` lists what the stack needs, plus `DRY_RUN_RECIPIENT` while
  `DRY_RUN` is not `false`. A new variable that must never be empty goes in that
  list in the same pull request. The run stops before anything changes.
- **The infra marker is what makes a failed apply retry.**
  `~/.local/state/ose-deploy/infra-applied` names the last commit whose infra
  was applied, and the next run applies everything after it. It moves as soon as
  the infra apply succeeds, before the workflow sync, so a failing export never
  re-applies the infra. Deleting it
  forces a full apply, which recreates Caddy.
- **Run the infra tests before you push a change to a script.**
  `automation/test/deploy-infra.sh`, `alert-slack.sh`, `backup-upload.sh`,
  `check-workflows-env.sh`, `check-n8n-version.py` and `check-env-drift.py`
  stub every external command. `automation/test/bootstrap-smoke.sh` and
  `caddy-routes.sh` need Docker. CI runs all of them and `tofu validate`. Each test fails loudly when a
  helper dies under `set -e`, because a silent stop reads as a pass.
- **Back up off the box before a manual change to it.** A change the deploy does
  not make, such as moving an existing box onto the timer, starts with a fresh
  dump and a verified off-box copy, per `automation/infra/README.md`. Local
  dumps share the database's disk.
- **Each hostname lets through only what its users need.** The applicant host
  serves the form paths and 404s the rest. The admin host leaves `/webhook/*`,
  `/form/*`, `/form-waiting/*`, `/api/v1/*` and `/healthz` open and puts
  everything else, editor and login included, behind the gate in
  `editor-auth.caddy`. Opening a path changes the Caddyfile and
  `automation/test/caddy-routes.sh` in the same pull request, never a catch-all
  `handle`. Caddy matches the cleaned path but forwards the raw one, so
  `clean_path_only` refuses dot segments and encoded separators before any rule
  runs. Keep it first in both sites. The gate's password is
  only in the vault and its bcrypt hash in git. An agent never sees the
  password: the operator generates it and hands over the hash.
- **`tofu/vps/` is a template and is never applied to the production box.**
  Applying it orders and bills a new VPS, and destroying it terminates one.
  `tofu/backup-storage/` is applied from the operator's laptop. State for both
  stays local and gitignored.

## Writing Docs

- **Never write a count of things that change.** "Nine checks" goes stale when
  one is added. Name what the things do, or say "every check" or "the rest". A
  number that names something, such as "check 9" or "apply 5", is fine.

## Commit Conventions

- **Use conventional commits:** `feat:`, `fix:`, `docs:`, `ci:`, `chore:`.
- **Two licences apply by path.** `automation/` is MIT (software), everything
  else CC-BY-4.0 (content). Do not introduce material incompatible with the
  licence of the path you are touching.
