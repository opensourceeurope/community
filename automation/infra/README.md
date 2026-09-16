# Infra: n8n on Postgres behind Caddy

The VPS that runs the application automation. Three containers: `postgres`
(n8n's database, not the default SQLite), `n8n` itself, and `caddy` as a
reverse proxy that terminates TLS and gets certificates automatically.

Throughout this document, **"the box"** means this VPS — the single machine
below that runs all three containers. Nothing else.

## Quick facts

| | |
|---|---|
| Host | the OVH VPS — hostname and IP are in the shared vault, next to the SSH access notes |
| Log in as | `ssh <user>@<vps-ip>` (key only; passwordless sudo). **Not root** |
| Checkout on the box | `~/community`, branch as deployed |
| Compose dir | `~/community/automation/infra` |
| Config | `~/community/automation/infra/.env` (mode 600, never committed) |
| Admin URL | https://automation.opensourceeurope.org |
| Applicant URL | https://apply.opensourceeurope.org |
| Backups | `~/backups/n8n-*.sql.gz`, daily, 14 days |

## What do you need to do?

| Task | Section |
|---|---|
| Install this from nothing | [First install](#first-install-from-a-fresh-vps) |
| Start, stop, look at logs | [Day to day](#day-to-day) |
| Take or verify a backup | [Backup](#backup) |
| Put a backup back | [Restore](#restore) |
| Upgrade n8n | [Upgrading n8n](#upgrading-n8n) |
| Give the workflows Open Collective access | [The Open Collective host-admin credential](#the-open-collective-host-admin-credential) |
| Let the workflows post to Slack | [The Slack credential](#the-slack-credential) |
| Let the workflows send email | [The SMTP credential](#the-smtp-credential) |
| Something is broken | [Troubleshooting](#troubleshooting) |
| Get in when SSH refuses you | [Getting into the box](#getting-into-the-box) |
| Point Open Collective at this box | [Registering the Open Collective webhook](#registering-the-open-collective-webhook) |

## The box (this VPS)

Provisioned 2026-08: OVHcloud **VPS-1 2027** — 2 vCore, 4096 MB, 40 GB NVMe,
**Strasbourg SBG6** (EU). Debian 13 (trixie).

That is roughly 4x what this stack needs. At rest n8n uses ~300-500 MB,
Postgres ~150 MB at this data volume, Caddy ~30 MB; the application load is a
few workflow executions a day. The constraint on a box this size is never
throughput — it is unbounded execution history, which is what
`EXECUTIONS_DATA_PRUNE` below is for.

## Where each secret lives

Three separate stores, and nothing crosses between them. Confusing them is easy
and has already caused a wrong turn.

| Store | Where | Holds | Not this |
|---|---|---|---|
| `~/.ovh.conf` | the operator's own laptop, mode 600, **never in this repo** | OVH API credentials only: `endpoint`, `application_key`, `application_secret`, `consumer_key` — used to manage the VPS itself | nothing about n8n, Postgres or inference. Never add application secrets here |
| `.env` | on the VPS, in `automation/infra/`, mode 600 | `POSTGRES_PASSWORD`, `N8N_ENCRYPTION_KEY`, `AI_API_KEY`, plus all non-secret config | not OVH credentials, not SMTP/Slack/OC credentials |
| n8n's credential store | inside the Postgres database, encrypted with `N8N_ENCRYPTION_KEY` | SMTP, Slack and Open Collective credentials, referenced by name from nodes | anything that has to exist before n8n starts |
| `N8N_API_KEY` in a gitignored `.env` at the **local checkout root** (operator's machine) | on whichever machine calls the API — decided: the repo-root `.env`, which git ignores | the n8n public-API key, used only to build and export workflows programmatically | **not the compose `.env` on the VPS** — that file is injected into the container, and n8n has no use for its own API key. Revoke this key once the workflows are built |

The password vault holds a copy of what cannot be regenerated, as **separate
entries** in a vault shared with more than one admin — see
[the encryption key section](#n8n_encryption_key-back-it-up-off-this-box-before-anything-else).
Recovering this box means finding these three, so they are named here for
whoever is searching the vault later:

- `OSE automation — n8n encryption key` (unrecoverable if lost)
- `OSE automation — Postgres (n8n db)` (resettable)
- `OSE — OVHcloud API` (re-issuable with `ovhcloud login`)

## Variables this compose file needs

This compose file reads two sets of variables, documented in two different
places:

- **Three infra-only variables**, documented right here, in the table below.
  They belong to this directory, not to the n8n workflows.
- **Application variables** (`DRY_RUN`, `AI_API_KEY`, `FORM_URL_OSE`
  and so on), documented in [`automation/.env.example`](../.env.example).
  This README does not duplicate them — two copies of the same list drift,
  and that file is the one that ships with the application config.

| Variable | What it is |
|---|---|
| `N8N_HOST` | The **admin** hostname — the editor, and the Open Collective webhook endpoint. `automation.opensourceeurope.org`. n8n builds `WEBHOOK_URL` and `N8N_EDITOR_BASE_URL` from it, and Caddy serves it as a TLS site. |
| `APPLY_HOST` | The **applicant-facing** hostname — `apply.opensourceeurope.org`, whose bare URL is the OSE application form. Caddy's TLS site address for that name. See "Two hostnames" below. |
| `EXECUTIONS_DATA_PRUNE` | `true`. n8n otherwise keeps the full payload of every execution forever. |
| `EXECUTIONS_DATA_MAX_AGE` | Hours of execution history to keep — `336` (14 days). Long enough to debug what happened to an applicant, short enough that the disk never becomes a question. |
| `POSTGRES_PASSWORD` | Password for the `n8n` role in the bundled Postgres. Generate with `openssl rand -hex 24`. |
| `N8N_ENCRYPTION_KEY` | Encrypts every credential n8n stores. Generate with `openssl rand -hex 32`. **Losing it makes every stored credential unrecoverable** — see the dedicated section below. |

Both sets go into one `.env` file in this directory before bringing the stack
up.

## Two hostnames

Both point at this box; both get their own automatic certificate; one n8n
container serves both.

| Name | Who sees it | What it serves |
|---|---|---|
| `apply.opensourceeurope.org` | applicants | the OSE application form, at the bare URL |
| `automation.opensourceeurope.org` | you | the n8n editor, and the OC webhook endpoint |

The applicant's link is deliberately just `https://apply.opensourceeurope.org`
— no `/form/...` path. n8n serves forms at `/form/<path>` and keeps the editor
at `/`, so the bare-URL form is a Caddy rewrite of `/` only. Page-to-page posts
and assets keep their own paths and stay on the same hostname.

**Verify before trusting it:** the rewrite is transparent only if n8n's form
pages use relative URLs for their posts and assets. If any is absolute and
built from `N8N_HOST`, an applicant jumps from `apply.` to `automation.` on
submitting page one — it works, but it looks broken and leaks the admin
hostname. Check it with the first real form.

The admin name is deliberately not `n8n.…`: every hostname issued a
certificate is published in Certificate Transparency logs, so a
tool-named host permanently advertises what software runs here.

## Registering the Open Collective webhook

Open Collective calls
`https://automation.opensourceeurope.org/webhook/oc-events` when a collective
applies to the host and when a host admin approves or rejects an application.
The `apply 1a — intake` workflow serves that URL.

The webhook belongs on the `europe` host account. On Open Collective the slug
`europe` is Open Source Europe, and it is the only account the automation
watches.

Set up two things before you give Open Collective the URL:

- The `oc-host-admin` credential exists in n8n's credential store.
  [The Open Collective host-admin credential](#the-open-collective-host-admin-credential)
  covers generating and storing it. The webhook payload carries no
  application data, so intake answers every delivery by re-fetching from the
  Open Collective GraphQL API with that credential.
- `apply 1a — intake` is active. n8n serves the production `/webhook/` path
  only while the workflow is active, and an inactive workflow answers 404.
  Open Collective sends each delivery once, so one that lands on a 404 is
  lost until the daily catch-up finds it.

Keep `DRY_RUN=true` throughout, so a real applicant cannot receive a test
message while you wire this up.

Then register:

1. Log in to opencollective.com as an admin of the `europe` host account.
2. Open the account's settings and go to **Webhooks**
   (`https://opencollective.com/dashboard/europe/webhooks`).
3. Add one webhook with the URL above for the activity `collective.apply`,
   and a second one with the same URL for `collective.approved`. Each
   webhook carries exactly one activity, and the picker shows display
   names, so match them to these types.

The picker has no entry for `collective.rejected`. The API dispatches that
activity to webhooks, the dashboard just never offers it. Two ways to cover
rejections:

- Leave it to the daily catch-up. It fetches decisions from the API, so a
  rejection is recorded and the closing email goes out on the next sweep,
  up to a day after the decision.
- Create the third webhook through the `createWebhook` GraphQL mutation.
  It needs a personal token with the `webhooks` scope from an admin of
  `europe`. Issue one for this, run the command in your own terminal, and
  revoke the token afterwards:

  ```bash
  curl -s https://api.opencollective.com/graphql/v2 \
    -H 'Content-Type: application/json' \
    -H "Personal-Token: $TOKEN" \
    -d '{"query":"mutation($w: WebhookCreateInput!) { createWebhook(webhook: $w) { id activityType webhookUrl } }","variables":{"w":{"account":{"slug":"europe"},"activityType":"COLLECTIVE_REJECTED","webhookUrl":"https://automation.opensourceeurope.org/webhook/oc-events"}}}'
  ```

  If the account has two-factor authentication, the API answers with a
  2FA challenge and the request must be repeated with an
  `x-two-factor-authentication: totp <code>` header. A webhook created
  this way appears in the dashboard's webhook list and can be deleted
  there like any other.

Webhooks fire only from the account they are attached to, and the
dashboard opens on whichever profile you managed last, so it is easy to
register them on your personal account by accident. Check the URL bar
shows the `europe` slug before creating, and verify afterwards by listing
what `europe` actually carries (same token as above):

```bash
curl -s https://api.opencollective.com/graphql/v2 \
  -H 'Content-Type: application/json' \
  -H "Personal-Token: $TOKEN" \
  -d '{"query":"{ host(slug: \"europe\") { webhooks(limit: 200, offset: 0) { totalCount nodes { id activityType webhookUrl } } } }"}'
```

Query through `host(slug: ...)`, not `account(slug: ...)`. The `europe`
account is an Organization, that type carries no `webhooks` field, and an
`... on Host` fragment on it silently matches nothing and returns an empty
object that reads like zero webhooks.

The response is noisy, and that is normal. The endpoint returns every
notification subscription on the account, not only webhooks, so expect a
`totalCount` far above three, rows with a null `webhookUrl` from email
subscriptions, and an `errors` array complaining about legacy activity
names the v2 enum cannot represent. The check is that three rows carry the
intake URL as their `webhookUrl`, one per activity. A missing row means
that webhook was created on another account: find it under that account's
webhook settings, delete it there, and recreate it on `europe`.

Nothing else needs configuring on either side. Open Collective sends no
signature, and the endpoint accepts any POST. That is safe by design. Intake
treats every delivery as a ping and re-fetches everything from the API, so a
forged or replayed delivery converges to the same idempotent write.

After the first real delivery, open the execution list of
`apply 1a — intake` and confirm the delivery arrived and wrote its row.

## The n8n community licence is optional

n8n mails a free activation key (Settings → Usage and plan) that unlocks
conveniences — workflow history, folders, debug in editor. **Nothing in this
automation depends on it**, so a missing or unrequested key blocks nothing.

If one is activated it lives in the database (`settings`, key `license.cert`),
not in `.env`, so `pg_dump` already covers it and a restore brings it back. Keep
a copy in the vault anyway for a from-scratch rebuild with no dump to hand.

To check whether one is active:

```bash
docker compose exec -T postgres psql -U n8n -d n8n -tAc "select key from settings order by key"
```

As of 2026-09-01 no licence is activated on this instance and no
`N8N_LICENSE_*` variable is set.

## Sending mail: not from this box

`opensourceeurope.org` publishes `v=spf1 include:_spf.protonmail.ch -all` — a
hard fail for any sender that is not Proton. This VPS is not an authorised
sender and has no reverse DNS, so mail sent directly from it as
`@opensourceeurope.org` will be rejected or filed as spam.

So the SMTP credential the workflows send with must be an authenticated relay
through an authorised sender. Do **not** solve this by adding the VPS to SPF: that
authorises a box running arbitrary workflows to send as the whole domain.
[The SMTP credential](#the-smtp-credential) describes the relay in use.

## Getting into the box

Only two facts here are directly verified; the rest of the first attempt at this
project cost three reinstalls and an IP block, so the verified ones are worth
reading before touching access on any host.

**Verified, and the first one was the actual cause of every failure:**

- **This image logs you in as `debian`, not `root`.** OVH's Debian 13 VPS image
  provisions a non-root `debian` user with passwordless sudo and puts the selected
  SSH key there. `root` gets no key, and Debian's default
  `PermitRootLogin prohibit-password` means no password works for root over SSH
  either — so every `ssh root@…` attempt fails no matter what is configured. The
  delivery email names the user; read it before debugging anything.

- **A passphrase-protected local key plus an empty `ssh-agent` fails exactly like
  a missing remote key.** `ssh -o BatchMode=yes` cannot prompt for a passphrase,
  so it offers the public key, cannot sign, and reports
  `Permission denied (publickey)` — indistinguishable from the server not having
  the key at all. Check the local side first: `ssh-add -l` should list the key,
  and `ssh-keygen -y -f ~/.ssh/<key>` proves the passphrase works. Every access
  conclusion drawn before that check is worthless.
- **Do not poll SSH in a loop.** Retrying every ten seconds while waiting for a
  host to come up is a brute-force pattern; this host's hardening blocked the
  source IP, after which connections are reset with
  `banner line 0: Not allowed at this time` *before* authentication — which looks
  nothing like a permissions problem and invalidates every test until the ban
  expires (typically 10-30 minutes). Wait, then try once.

**Observed on the OVH VPS 2027 range, single occurrences, not established as
general behaviour:**

- `ovhcloud vps set-password` → `403 "This function is not available on your VPS"`.
- `ovhcloud vps edit --keymap us` → reports success, while `GET /vps/<sn>` still
  returns `keymap=null`. A console keyboard may therefore not match yours: with an
  unset keymap it can be AZERTY, so `q` types `a` and `w` types `z`, and the
  password prompt does not echo. Diagnose by typing the password at the `login:`
  prompt, which does echo. The CLI hands you a `vnc_lite.html` console URL with no
  clipboard; swapping it for `vnc.html` gives a clipboard panel that bypasses the
  layout entirely.
- Whether `vps reinstall --public-ssh-key` / `--ssh-key <registered name>`
  pre-install a key is **unresolved** — every test of it ran under the traps
  above and against the wrong user, so the evidence proves nothing either way.
  What is confirmed: selecting the key in the Manager UI's reinstall dialog
  installs it, for the `debian` user, and OVH then sends no password at all —
  a delivery mail with a username and no password is the signal that the key
  went in.

### The `ovhcloud` CLI needs credentials, and they expire

Managing the VPS (console URLs, reinstall, state, keymap) goes through the
`ovhcloud` CLI, which reads API credentials from `~/.ovh.conf` — endpoint,
application key, application secret, consumer key.

- **Keep that file outside this repo.** It began life in the repo's working
  directory, untracked but not ignored, one `git add -A` away from publishing an
  application secret. `~/.ovh.conf` (mode 600) is where the CLI looks by default;
  `ovh.conf` is now gitignored as a backstop.
- **`INVALID_CREDENTIAL` (403) on every call means the consumer key is gone** —
  revoked, expired, or rotated. It is not a permissions problem with the VPS.
  Fix: `ovhcloud login`, which runs a browser consent flow and rewrites the file.
- Unlike `N8N_ENCRYPTION_KEY`, these are cheap to lose: revoke and re-issue at
  will. Vault them for convenience, not as insurance.

**The rule that would have prevented all of it:** never remove one route into a
machine before the replacement is proven. Combining an unverified key flag with
`--do-not-send-password` left no key, no password, and a console login that could
not succeed.

## First install, from a fresh VPS

Every command below was run in this order on 2026-08-31 and is idempotent —
re-running it on a working box changes nothing. Log in as `debian`; use `sudo`
for privileged steps.

```bash
# 1. packages
export DEBIAN_FRONTEND=noninteractive
sudo -E apt-get update -qq && sudo -E apt-get upgrade -y -qq
sudo -E apt-get install -y -qq git ufw unattended-upgrades ca-certificates curl

# 2. firewall — ALLOW BEFORE ENABLE, or you lock yourself out
sudo ufw allow 22/tcp && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
sudo ufw --force enable
# Postgres is never published to the host; it is reachable only on the compose network.

# 3. key-only SSH. Do this ONLY after key login is proven working.
sudo tee /etc/ssh/sshd_config.d/99-ose-hardening.conf >/dev/null <<'CONF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
CONF
sudo sshd -t && sudo systemctl reload ssh     # validate, then reload
# now open a SECOND session to confirm you still have access before closing this one

# 4. docker
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker debian
# log out and back in so the group applies, then `docker ps` works without sudo

# 5. the repo
git clone https://github.com/opensourceeurope/community.git ~/community
cd ~/community && git checkout <branch-or-main>

# 6. config — generate secrets ON THE BOX so they never travel
cd ~/community/automation/infra
umask 077
cp ../.env.example .env
# add the infra-only variables from the table above:
#   N8N_HOST, APPLY_HOST, EXECUTIONS_DATA_PRUNE=true, EXECUTIONS_DATA_MAX_AGE=336
#   POSTGRES_PASSWORD=$(openssl rand -hex 24)
#   N8N_ENCRYPTION_KEY=$(openssl rand -hex 32)   <- save to the password manager NOW
chmod 600 .env
docker compose config >/dev/null   # must report no unset variables

# 7. up
docker compose up -d
```

### Verify the install, in this order

```bash
# (a) Postgres really is the backend. If this is empty, n8n fell back to SQLite —
#     stop and fix DB_TYPE before anything is stored.
docker compose exec -T postgres psql -U n8n -d n8n -c "\dt" | head
docker compose logs n8n | grep -ci sqlite      # expect 0

# (b) certificates and both hostnames, from OUTSIDE the box
curl -sS -o /dev/null -w '%{http_code}\n' https://automation.opensourceeurope.org/   # 200
curl -sS -o /dev/null -w '%{http_code}\n' https://apply.opensourceeurope.org/        # 404 until the form workflow exists — see below

# (c) resource headroom
docker stats --no-stream; free -m
```

**A 404 on `apply.` is correct until the form exists.** The Caddy rewrite sends
`/` to `/form/apply-ose`; n8n answers 404 because no workflow serves that path
yet. The tell that the rewrite works is that the 404 body is an n8n-rendered
page. Measured on first install: 1071 MB of 3826 MB used, n8n 379 MB, Postgres
48 MB, Caddy 17 MB.

Then create the n8n owner account at the admin URL.

**If you create it with the wrong email**, in rough order of preference:

1. **Settings → Personal** in n8n — change the email in place, no reset.
2. `UPDATE "user" SET email='…'` in Postgres — keeps the account and skips
   setup, useful if the UI field is gated behind SMTP.
3. `docker compose exec -T n8n n8n user-management:reset` — clears users and
   brings the setup screen back. It deletes the account, so check first that
   there is nothing to lose:

```bash
for t in workflow_entity credentials_entity; do
  printf "%-20s " "$t"
  docker compose exec -T postgres psql -U n8n -d n8n -tAc "select count(*) from $t"
done
```

Zero in both means a reset costs nothing but the account itself.

## Day to day

```bash
cd ~/community/automation/infra
docker compose ps                  # what is running
docker compose logs -f n8n         # follow n8n
docker compose logs --tail=50 caddy
docker compose restart n8n
docker compose down                # stop everything; named volumes survive
docker compose up -d               # back up
```

**After editing `.env` or `docker-compose.yml`, `restart` is not enough.** `docker compose restart` reuses
the existing container with its existing environment; the file is only re-read
when the container is recreated:

```bash
docker compose up -d n8n           # recreates with the new .env values
```

## Setting the credentials that are not generated here

Before changing any value in `.env`, bring the checkout on the box up to
date. Compose passes into the container only the variables its compose file
names, so a variable added to the repository after the last pull is silently
ignored no matter what `.env` says:

```bash
ssh <user>@<vps-ip> 'git -C ~/community checkout main && git -C ~/community pull --ff-only'
```

`Already up to date.` or a fast-forward are both fine. The `checkout` is
there because the box has been left on a feature branch before: `pull` then
fetches `main` but merges nothing, and reports a missing ref for the old
branch. Anything else means the box has local changes, and that needs a look
before going on.

### Setting a plain configuration value

Most variables in `automation/.env.example` are not secrets: `DRY_RUN`,
`DRY_RUN_RECIPIENT`, `ONLY_SLUGS`, the two `*_AFTER_MINUTES` timers,
`SWEEP_CRON`, `FORM_URL_OSE`, `AI_MODEL`, `AI_BASE_URL`, `SMTP_FROM` and
`SLACK_CHANNEL`. Any of them can be set over `ssh` directly. Replace `<VAR>`
with the variable name and `<value>` with the value:

```bash
ssh <user>@<vps-ip> 'cd ~/community/automation/infra
  grep -q "^<VAR>=" .env || echo "<VAR>=" >> .env
  sed -i "s|^<VAR>=.*|<VAR>=<value>|" .env
  grep "^<VAR>=" .env
  docker compose up -d n8n
  docker compose exec -T n8n printenv <VAR>'
```

The first line appends an empty `<VAR>=` when the variable is missing,
because `sed` replaces only a line that exists and otherwise does nothing
without saying so. The second `grep` must print the line as written.
`docker compose up -d n8n` must print `Recreated`, not `Running`: the
container reads `.env` only when it is created, and `Running` means the value
inside it is still the old one. The final `printenv` shows what the workflows
will actually read. A value containing `#` needs double quotes, as in
`SLACK_CHANNEL=\"#<channel>\"`, because an unquoted `#` can start a comment
in a compose `.env`. Spaces and angle brackets need no quoting.

For example, `DRY_RUN_RECIPIENT` must hold a real mailbox before any
workflow is activated with `DRY_RUN=true`, because every render step throws
when it is empty:

```bash
ssh <user>@<vps-ip> 'cd ~/community/automation/infra
  grep -q "^DRY_RUN_RECIPIENT=" .env || echo "DRY_RUN_RECIPIENT=" >> .env
  sed -i "s|^DRY_RUN_RECIPIENT=.*|DRY_RUN_RECIPIENT=<your address>|" .env
  grep "^DRY_RUN_RECIPIENT=" .env
  docker compose up -d n8n
  docker compose exec -T n8n printenv DRY_RUN_RECIPIENT'
```

### Setting the AI key

`AI_API_KEY` (Scaleway inference) is the one secret this box needs that it
cannot generate for itself.

**Use a key dedicated to this automation** — not the one already in use by
`ose-knowledge-mcp`. Separate keys rotate independently, limit the blast radius
of a leak, and make usage attributable to the right system.

**Two files carry these variable names; only one takes real values.**
`automation/infra/.env` on the box is the real config — edit this.
`automation/.env.example` is a committed template, and every value in it is
empty on purpose. Putting a real key there commits a secret to a public
repository, which is the worst version of this mistake. Both being empty looks
identical, so check the path, not the contents.

Set it by editing the file on the box, and watch what you type:

```bash
ssh -t <user>@<vps-ip> 'nano ~/community/automation/infra/.env'
# Ctrl+W, search AI_API_KEY, paste the secret key after the = (no spaces, no quotes)
# Ctrl+O, Enter to save, Ctrl+X to exit
ssh <user>@<vps-ip> 'cd ~/community/automation/infra && docker compose up -d n8n'
```

**Expect `Recreated`, not `Running`.** That word is the only evidence the value
actually changed: compose recreates the container when the resolved environment
differs, and reports `Running` when it does not.

Verify rather than assume. Print **lengths only** — and be careful how: an
earlier version of this snippet used `${V:+set}${V:-EMPTY}`, which expands to the
value itself whenever the variable is set, and leaked a live key into a
transcript. Use an explicit `if`:

```bash
ssh <user>@<vps-ip> 'cd ~/community/automation/infra
  awk -F= "/^AI_API_KEY=/{print \"env file:\", (length(\$2)?\"set\":\"EMPTY\")}" .env
  V=$(docker compose exec -T n8n printenv AI_API_KEY | tr -d "\r\n")
  if [ -n "$V" ]; then echo "container: set, ${#V} chars"; else echo "container: EMPTY"; fi'
```

If your terminal swallows nano's paste, this prompts **on the box** with a TTY, so
the paste lands where it is read, and it refuses to write an empty value:

```bash
ssh -t <user>@<vps-ip> 'printf "paste key then Enter: "; IFS= read -r K; \
  [ -n "$K" ] && sed -i "s|^AI_API_KEY=.*|AI_API_KEY=$K|" ~/community/automation/infra/.env \
    && echo "written, ${#K} chars" || echo "EMPTY — nothing written"'
```

A piped one-liner (`read -rs KEY` into `ssh … sed`) looks tidier and was tried
first here. **Avoid it.** If the read captures nothing — an interactive paste that
does not land, a shell difference — `sed` writes an empty value, compose sees no
change and prints `Running`, and every step reports success while the key is
still missing. Two attempts failed that way before anyone noticed.

**A GitHub Actions secret is not a place you can read a value back from.** They
are write-only: `gh secret list` returns names and timestamps, there is no `get`,
and no API exposes the value. If a credential exists *only* as a repo secret, it
is effectively lost — keep the copy of record in the shared password vault.

## The Open Collective host-admin credential

The two intake workflows, `apply 1a — intake` and `apply 1b — daily catch-up`,
read pending applications and decisions from the Open Collective GraphQL API.
They authenticate with a personal token, stored in n8n's credential store
under the name `oc-host-admin`.

### Generate the token

The API returns host applications only to an admin of the host, so the token
must come from a user account that is an admin of Open Source Europe (host
slug `europe`) on Open Collective. A token from any other account
authenticates fine and then every fetch fails with an authorization error.

1. Log in to opencollective.com with that account.
2. Open the personal **Dashboard**, then **For developers**, then
   **Personal tokens**, and create a token.
3. Select the scopes **host** and **email**, and leave every other scope
   unselected. A token permits only the scopes selected on it. These two
   cover what the workflows read: `host` is the scope for fiscal-host data,
   and `email` covers the applicant admin addresses that intake stores as
   the contact address. The admin role above is the real gate on application
   data, so additional scopes add exposure without adding capability.
4. An expiration date is optional. If you set one, put a reminder on it: an
   expired token fails exactly like a revoked one, and every sweep run fails
   until the token is replaced.

### Store it in n8n

The token lives in n8n's credential store and nowhere else. It is
re-issuable at will from the same dashboard page, so it needs no vault
entry.

The credential dialog contains two different names. The credential's own
name is the title at the top of the dialog, which opens as
"Header Auth account" and is renamed by clicking it. The **Name** field in
the form is the HTTP header name that n8n sends with every request. Putting
the credential name in the **Name** field makes every API call anonymous.

1. In the n8n editor, open **Credentials** and create a credential of type
   **Header Auth**.
2. Click the title at the top of the dialog and rename the credential to
   exactly `oc-host-admin`. The workflow nodes reference it by that name.
3. In the form, set **Name** to `Personal-Token` and **Value** to the
   token.
4. Change **Allowed HTTP Request Domains** from **All** to
   `api.opencollective.com`, so n8n refuses to attach this header to a
   request going anywhere else. Save.
5. Attach the credential to the workflow nodes that use it. Four HTTP
   Request nodes call the Open Collective API, and they are the only places
   the pipeline calls it:

   | Workflow | Node |
   |---|---|
   | `apply 1a — intake` | **Fetch pending applications** |
   | `apply 1a — intake` | **Fetch application status** |
   | `apply 1b — daily catch-up` | **Fetch pending applications** |
   | `apply 1b — daily catch-up` | **Fetch application status** |

   The workflows reference the credential by name, so once a credential
   named exactly `oc-host-admin` exists, n8n resolves the reference on its
   own. Open each workflow from the workflow list, open the node, and
   confirm that **Credential for Header Auth** shows `oc-host-admin`
   without an error marker. A node that shows an empty or red credential
   field means the name does not match, so fix the credential name rather
   than picking manually, or the next re-import breaks the same way.

### Confirm by effect

Open `apply 1b — daily catch-up` in the editor and execute the
**Fetch pending applications** node. Use `apply 1b` for this test, not
`apply 1a`: the intake workflow starts from the webhook trigger, so an
execute there waits for a webhook call instead of running. To test inside
`apply 1a` anyway, pin an output on the **OC events webhook** node with a
body of `{ "type": "collective.apply" }` first.

A working token returns `data.host`
containing a `hostApplications` object, and a `totalCount` of zero is a
valid answer. A missing, mis-scoped or non-admin token returns an `errors`
array instead, and the Code node that follows throws with the response text.

### Rotate

Create a new token on the same dashboard page, replace the **Value** in the
existing `oc-host-admin` credential, and delete the old token on
opencollective.com. The nodes reference the credential by name, so nothing
else changes.

## The Slack credential

Two workflows post to Slack, and only when a human needs to act:
`apply 3 — follow-up` posts the escalation for an application that went
silent, and `apply 4 — application form` posts "ready for evaluation" after
a form submission. Both use the n8n Slack node, which authenticates with a
Slack app's bot token. An incoming-webhook URL does not work here: the node
offers only **Access Token** and **OAuth2**, and there is no field for a
webhook URL anywhere in the workflows.

The token lives in n8n's credential store under the name `slack-bot`. The
channel is not part of the credential: it comes from `SLACK_CHANNEL` in the
compose `.env`, so the same token can post anywhere the app is a member.

### Create the Slack app and its token

Anyone who can install apps in the OSE Slack workspace can do this.

1. Open api.slack.com/apps while logged in to the OSE workspace, choose
   **Create New App**, then **From scratch**. Name it for what it does, for
   example `OSE applications`, and pick the OSE workspace.
2. Under **Settings**, open **Collaborators** and add at least one other
   admin. A Slack app belongs to the accounts listed here. If the only
   collaborator leaves the workspace, nobody can rotate or reinstall the app
   and the token has to be recreated from scratch under a new app.
3. Under **Features**, open **OAuth & Permissions**, scroll to **Scopes**,
   and add one **Bot Token Scope**: `chat:write`. That is the whole
   requirement: the two nodes only post messages. n8n's documentation
   suggests a much longer list for general use. Do not add it: every extra
   scope widens what a leaked token can do without adding anything the
   pipeline uses.
4. At the top of the same page, choose **Install to Workspace** and allow
   the request. The page then shows a **Bot User OAuth Token**, starting
   with `xoxb-`. That is the value n8n needs. Treat it like a password from
   this point on: do not paste it into chat, an issue, or a commit.
5. In Slack, open the channel the notifications should reach and invite the
   app with `/invite @{bot-username}`. The username is the one shown under
   **App Home** for the app's bot user, not the app's display name. A bot
   can post only into channels it is a member of. Posting without membership fails with `not_in_channel`,
   and inviting the app is the fix, not adding scopes.

### Store it in n8n

The token lives in n8n's credential store and nowhere else. It is
re-issuable at will from the app's **OAuth & Permissions** page by any
collaborator, so it needs no vault entry. The app's existence and its
collaborators are what to record in the vault, as one line, so the next
person knows the app already exists instead of creating a second one.

1. In the n8n editor, open **Credentials** and create a credential of type
   **Slack API**.
2. Click the title at the top of the dialog and rename the credential to
   exactly `slack-bot`, the name this runbook uses.
3. Paste the `xoxb-` token into **Access Token**. Leave
   **Signature Secret** empty. It lets the Slack Trigger node verify events
   that Slack sends to n8n, and the pipeline never receives Slack events.
4. Set **Allowed HTTP Request Domains** to **Specific domains** and enter
   `slack.com`. This setting decides where the credential may be sent when
   it is picked inside an HTTP Request or GraphQL node. Nothing in the
   pipeline does that, so the list exists only to stop a future HTTP
   Request node with a wrong URL from sending this token to another host.
   Do not choose **None**: n8n's own connection test runs through the same
   path, so **None** fails the test with "This credential is configured to
   prevent use within an HTTP Request node" even though the token is fine.
   Save.
5. n8n checks the token against Slack when the credential is saved and
   shows the result in the dialog. A failure here means the token was pasted
   incompletely or the app was not installed to the workspace. A success
   proves only that the token is valid. The scope and the channel are
   proven in [Confirm by effect](#confirm-by-effect-1).
6. Attach the credential to the two nodes that use it. They are the only
   places the pipeline talks to Slack:

   | Workflow | Node |
   |---|---|
   | `apply 3 — follow-up` | **Notify Slack** |
   | `apply 4 — application form` | **Notify Slack** |

   Open each workflow, open the node, pick `slack-bot` under
   **Credential to connect with**, and save the workflow. Then export both
   workflows into `automation/n8n/` so the exports carry the reference. From
   then on a re-import resolves the credential by name, as the Open
   Collective nodes already do.

### Set the channel

`SLACK_CHANNEL` is not a secret, so it can be set over `ssh` directly, once the
checkout on the box is current as described in
[Setting the credentials that are not generated here](#setting-the-credentials-that-are-not-generated-here).
Replace `<user>` and `<vps-ip>` with the values from the vault and
`<channel>` with the channel name, keeping the `#`:

```bash
ssh <user>@<vps-ip> 'cd ~/community/automation/infra
  grep -q "^SLACK_CHANNEL=" .env || echo "SLACK_CHANNEL=" >> .env
  sed -i "s|^SLACK_CHANNEL=.*|SLACK_CHANNEL=\"#<channel>\"|" .env
  grep "^SLACK_CHANNEL=" .env
  docker compose up -d n8n
  docker compose exec -T n8n printenv SLACK_CHANNEL'
```

The first line appends an empty `SLACK_CHANNEL=` when the variable is
missing, because `sed` replaces only a line that exists and otherwise does
nothing without saying so. The second `grep` must print the line. The double
quotes matter: a `#` in a compose `.env` can start a comment, and quoting
removes the ambiguity. Compose strips the quotes, so the container sees the
`#` and the name. `docker compose up -d n8n` must print `Recreated`, not
`Running`: the container reads `.env` only when it is created. The final
`printenv` shows what the workflows will actually read. If it prints nothing
while `.env` has the line, see [Troubleshooting](#troubleshooting).

For a public channel the name works, written with a leading `#`. The node
passes the value straight to Slack's `chat.postMessage`, which resolves
public channel names itself. For a private channel use the channel ID
instead, a string starting with `C` shown at the bottom of the channel's
details pane in Slack. A private channel's name is not resolvable by a bot
and the post fails with `channel_not_found`.

### Confirm by effect

While `DRY_RUN` is true the pipeline never reaches its Slack nodes: the
render step in front of each one redirects the message to
`DRY_RUN_RECIPIENT` by email, with the channel it would have used in the
subject. So a dry-run rehearsal proves the rendering and the routing, and
proves nothing about the token or the channel membership.

To prove those, create a throwaway workflow in n8n with a **Manual
Trigger** and one **Slack** node, resource **Message**, operation **Send**,
credential `slack-bot`, channel set to the same value as `SLACK_CHANNEL`,
and any text. Execute it once. A message appearing in the channel is the
confirmation. Delete the throwaway workflow afterwards so it never shows up
in the workflow list as a real one.

### Rotate

On the app's **OAuth & Permissions** page choose **Revoke All OAuth
Tokens**, then **Install to Workspace** again. Slack issues a new
`xoxb-` token. Replace the **Access Token** in the existing `slack-bot`
credential in n8n and save. The nodes reference the credential by name, so
nothing else changes. The old token stops working the moment it is revoked,
so do the two steps together.

## The SMTP credential

Every applicant email leaves through one SMTP credential in n8n's credential
store, named `smtp-proton`. The relay is Proton Mail, because
`opensourceeurope.org` is hosted there and its SPF record authorizes no other
sender, as [Sending mail: not from this box](#sending-mail-not-from-this-box)
explains. The From address on every email is `SMTP_FROM` from the compose
`.env`. A Proton token sends as its own address only, so the two must name
the same mailbox.

### Create the sending address and its token

Proton attaches an SMTP token to an address on a user account. A Proton
group is a forwarding list, not an address anyone can authenticate as, so
the sending address has to be a real address on the account of whoever runs
this, even if a group with a similar purpose exists.

1. In Proton Mail, open **Settings**, **All settings**, **Identity and
   addresses**, and add `home@opensourceeurope.org` as an additional address
   on your account. The emails carry no Reply-To, so replies from applicants
   arrive in this mailbox. Add a filter that forwards them if other people
   should read them too.
2. Open **IMAP/SMTP**, then **SMTP tokens**, and choose **Generate token**.
   Name it for what uses it, for example `n8n applications`, and select
   `home@opensourceeurope.org` as the address. Proton shows the token once.
   Copy it straight into the n8n credential in the next section. Treat it
   like a password: do not paste it into chat, an issue, or a commit.
3. SMTP tokens exist on all paid Proton Mail plans with a custom domain. If
   the **SMTP tokens** page is missing, the account is on a plan without
   them, and the choice is a plan change or a different relay.

### Store it in n8n

The token lives in n8n's credential store and nowhere else. It is
re-issuable at will from the same Proton page, so it needs no vault entry.
One line in the vault saying which Proton account owns the address is enough
for the next person to find it.

1. In the n8n editor, open **Credentials** and open the existing **SMTP**
   credential, or create one if none exists.
2. Click the title at the top of the dialog and rename the credential to
   exactly `smtp-proton`, the name this runbook uses.
3. Fill in the form:

   | Field | Value |
   |---|---|
   | **User** | `home@opensourceeurope.org` |
   | **Password** | the SMTP token |
   | **Host** | `smtp.protonmail.ch` |
   | **Port** | `587` |
   | **SSL/TLS** | off |
   | **Disable STARTTLS** | off |
   | **Client Host Name** | empty |

   Proton serves port 587 with STARTTLS, so **SSL/TLS** stays off and
   **Disable STARTTLS** stays off. Turning **SSL/TLS** on with port 587
   fails with a TLS handshake error before authentication is attempted.
   Save. n8n then connects to the relay and logs in with these values, and
   shows the result in the dialog. A green result proves the host, port,
   STARTTLS setting and token. It does not send anything, so the From
   address and actual delivery are proven in
   [Confirm by effect](#confirm-by-effect-2).
4. Attach the credential to the five Send Email nodes. They are the only
   places the pipeline sends mail:

   | Workflow | Node |
   |---|---|
   | `apply 1a — intake` | **Send decision email** |
   | `apply 1b — daily catch-up` | **Send decision email** |
   | `apply 3 — follow-up` | **Send form invitation** |
   | `apply 3 — follow-up` | **Send reminder** |
   | `apply 4 — application form` | **Send confirmation email** |

   Open each workflow, open the node, pick `smtp-proton` under
   **Credential to connect with**, and save the workflow. Then export the
   changed workflows into `automation/n8n/` so the exports carry the
   reference.

### Set the From address

`SMTP_FROM` is not a secret, so it can be set over `ssh` directly, once the
checkout on the box is current as described in
[Setting the credentials that are not generated here](#setting-the-credentials-that-are-not-generated-here). Replace
`<user>` and `<vps-ip>` with the values from the vault:

```bash
ssh <user>@<vps-ip> 'cd ~/community/automation/infra
  grep -q "^SMTP_FROM=" .env || echo "SMTP_FROM=" >> .env
  sed -i "s|^SMTP_FROM=.*|SMTP_FROM=Open Source Europe <home@opensourceeurope.org>|" .env
  grep "^SMTP_FROM=" .env
  docker compose up -d n8n
  docker compose exec -T n8n printenv SMTP_FROM'
```

The first line appends an empty `SMTP_FROM=` when the variable is missing,
because `sed` replaces only a line that exists and otherwise does nothing
without saying so. The second `grep` shows the line as written and must print
it. `docker compose up -d n8n` must print
`Recreated`, not `Running`: the container reads `.env` only when it is
created, and `Running` means the value it holds is the old one. The final
`printenv` shows what the workflows will actually read. The variable holds
angle brackets and spaces, and the compose file passes it through
unchanged, so no quoting is needed in `.env`.

The address part must be the address the token was generated for. Proton
issues one token per address and documents that a token sends as that
address, so a From header naming another address is not covered by the
token and fails or gets rewritten at Proton's side, not silently accepted.

### Confirm by effect

Unlike Slack, the dry run exercises SMTP for real: while `DRY_RUN` is true
every email still goes out, to `DRY_RUN_RECIPIENT` instead of the applicant.
So the first dry-run rehearsal is the test. A message arriving at
`DRY_RUN_RECIPIENT` from `home@opensourceeurope.org` proves the token, the
port settings and the From address together.

To test earlier, create a throwaway workflow with a **Manual Trigger** and
one **Send Email** node using `smtp-proton`, From set to the same value as
`SMTP_FROM`, To set to your own address, and any subject. Execute it once
and delete the workflow afterwards. Check the received message's headers:
`spf=pass` and `dkim=pass` show that Proton signed it and the domain's
policy was met.

### Rotate

Generate a new token on Proton's **SMTP tokens** page, replace the
**Password** in the existing `smtp-proton` credential in n8n, save, and
delete the old token on the same Proton page. The nodes reference the
credential by name, so nothing else changes.

## N8N_ENCRYPTION_KEY: back it up, off this box, before anything else

`N8N_ENCRYPTION_KEY` encrypts every credential n8n stores — SMTP, Slack, Open
Collective, everything referenced by name from workflow nodes. **If this key
is lost, every one of those stored credentials is unrecoverable.** There is no
reset or recovery path: each credential has to be re-entered by hand, in every
workflow that references it. Generate it once, store it in the operator's
password manager the moment it's generated, and never regenerate it against
an existing database — a mismatched key does not decrypt what's already
there.

**Store it in a vault shared with at least one other admin, not a personal
one.** A key that exists only in one person's password manager makes OSE's
ability to recover this instance depend on that person's account being
reachable — which is a bus factor of one dressed up as a backup. The same goes
for `POSTGRES_PASSWORD` — but store it as its **own vault entry**, not a field
on the same item. Their lifecycles differ: the Postgres password is rotated by
resetting the role and updating `.env`, while the encryption key must never
change while a database exists. Keeping them separate means routine rotation
never involves editing the item that must not be touched, lets the two carry
their very different "what happens if I lose this" notes, and allows the
database password to be shared with someone doing database work without handing
over the key that decrypts every stored credential.

To read the values back off the box:

```bash
ssh <user>@<vps-ip> 'grep -E "N8N_ENCRYPTION_KEY|POSTGRES_PASSWORD" ~/community/automation/infra/.env'
```

Run that in a human's own terminal. Do not paste either value into a chat,
an issue, a commit, or an agent transcript — anything that records it becomes
another copy to protect.

## Backup

A "dump" here means the whole database written out as one compressed SQL file
(`n8n-<timestamp>.sql.gz` in `~/backups/`) — the dump **is** the backup, and
restoring means feeding that file back to Postgres.

n8n's tables (workflows, credentials, execution history) **and** the
`ose_applications` Data table live in the same Postgres — Data tables are just
tables inside n8n's database. So one `pg_dump` of `n8n` is the entire backup,
covering applicant stage, AI verdicts, form answers and decisions.

The backup of record is this repo's own — `backup.sh` plus the systemd units in
[`systemd/`](systemd/), installed by the steps below. If the timer or service
files change in git, re-copy them to `/etc/systemd/system/` and
`systemctl daemon-reload`; the script itself is picked up by `git pull` alone,
since the service runs it from the checkout.

**OVH also snapshots this VPS daily** (Manager → the VPS → Automated backup;
observed: daily at 14:37 UTC, one restore point kept — the paid Premium tier
would keep seven). Treat it as a bonus safety net, not the backup of record:

- one restore point means yesterday-only, no history;
- the snapshot sits with the same provider as the VM, so it does not survive
  losing the OVH account or region;
- it restores only onto OVH, whereas a dump is a portable file you can inspect
  and load anywhere;
- it is crash-consistent — restoring is like the machine having lost power at
  14:37. Postgres recovers from that by replaying its write-ahead log, so it
  normally comes back, but "normally" is not the standard for the only copy.
- it contains the full disk, secrets in `.env` included.

[`backup.sh`](backup.sh) does it, and refuses to leave a plausible-looking
useless file behind: it fails if the dump is under 10 KiB, fails gzip
integrity, or contains no `CREATE TABLE`.

```bash
~/community/automation/infra/backup.sh          # writes ~/backups/n8n-<stamp>.sql.gz
```

Schedule it with the systemd units in [`systemd/`](systemd/) — 03:17 UTC daily.
Debian 13 minimal ships **no cron package**, and a timer is the better fit
anyway: `Persistent=true` runs a backup that was missed while the box was down,
which cron silently skips.

```bash
sudo cp ~/community/automation/infra/systemd/ose-backup.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ose-backup.timer

systemctl list-timers ose-backup.timer      # when it next fires
sudo systemctl start ose-backup.service     # run one now
tail ~/backups/backup.log
```

**A failed backup is invisible to the journal.** The service sets
`StandardOutput=append:` to `~/backups/backup.log`, which *replaces* journal
output — so `journalctl -u ose-backup.service` shows "No entries" even for runs
that happened, and would show nothing for a run that failed. Check
`tail ~/backups/backup.log`, not the journal, until the units are changed to let
the script append to the log while systemd keeps the journal.

**Dumps will contain applicant personal data.** Not yet — `ose_applications`
does not exist — but once it does, every dump carries applicant email addresses
and form answers. That makes where copies are stored a data-protection question
as well as a durability one: encrypt before uploading anywhere off this box.

Confirmed running unattended: the timer fired on its own at 03:22 UTC on
2026-09-01 and wrote a dump. Installed and actually-runs are different claims,
and this is the second — check `systemctl list-timers ose-backup.timer` and
`tail ~/backups/backup.log` to re-confirm at any time.

**Copy backups off this box.** A backup that only exists on the machine it
protects does not survive that machine. Nothing in this repo does that for you
— it needs a destination someone owns.

## Which backup to restore, when

Two different things exist, and they answer different failures. The nightly
**dump** is a copy of the *database only*; the daily **OVH snapshot** is an image
of the *whole machine*. Rule of thumb: a data problem wants the dump, a machine
problem wants the snapshot or a rebuild.

| What happened | Restore what | How |
|---|---|---|
| Bad data: an n8n upgrade broke things, workflows or rows were deleted or mangled, but the machine itself is fine | The most recent **dump** from before the damage | The Restore steps below, on this box |
| Only one thing is lost (a single workflow, a few rows) and everything else moved on since | The dump, **into a scratch database** | The rehearsal procedure below — then copy just what you need out of `restoretest`, instead of rolling the whole database back and losing everything newer |
| The machine is broken (filesystem damage, botched OS change) but OVH is fine | The **OVH snapshot** (Manager → the VPS → Automated backup) | Restores the whole disk to ~14:37 UTC yesterday, `.env` included. Afterwards: check the stack (`docker compose ps`), and run `backup.sh` once so a fresh dump exists |
| The VPS is gone entirely — terminated, region lost, account problem | A **dump** plus this repo | New VPS → the "First install" steps → load the dump. Needs `N8N_ENCRYPTION_KEY` **from the vault** — this is the moment it exists for |

Two constraints that hold in every row:

- A dump only yields working credentials under the **original**
  `N8N_ENCRYPTION_KEY`. Machine-problem rows keep it automatically (it is in
  `.env` on the restored disk); the last row is why the vault copy matters.
- Today the dumps live **only on this box**, so the last row currently works
  only if you saved a dump elsewhere by hand — losing the box loses the dumps
  with it, and the OVH snapshot (same provider) is then the sole survivor. That
  is the documented off-box gap.

## Restore

```bash
# stop n8n so nothing writes to the database mid-restore
docker compose stop n8n

# drop and recreate the database, then load the dump
docker compose exec -T postgres psql -U n8n -d postgres -c "DROP DATABASE n8n;"
docker compose exec -T postgres psql -U n8n -d postgres -c "CREATE DATABASE n8n OWNER n8n;"
gunzip -c n8n-backup-<date>.sql.gz | docker compose exec -T postgres psql -U n8n -d n8n

docker compose start n8n
docker compose logs -f n8n
```

### What each secret means for a rebuild

The dump contains database objects, not roles — verified: zero `CREATE ROLE` /
`ALTER ROLE` statements in it. That gives the two secrets very different
recovery properties:

| Scenario | `POSTGRES_PASSWORD` | `N8N_ENCRYPTION_KEY` |
|---|---|---|
| Restart, reboot, `down` + `up -d`, n8n upgrade | unchanged — it is a file on disk and the volumes persist | unchanged |
| `docker compose down -v`, box rebuilt, new VPS | gone with the filesystem; **pick a new one freely** | gone; **must be the original or credentials are unreadable** |
| Deliberate rotation on a live box | see below | never rotate against an existing database |

**Rotating `POSTGRES_PASSWORD` takes two steps, not one.** The compose variable
only initialises an *empty* data directory; editing `.env` afterwards does not
change the role, and n8n simply stops being able to connect:

```bash
docker compose exec -T postgres psql -U n8n -d postgres -c "ALTER ROLE n8n PASSWORD 'newvalue';"
# then update .env to match, and recreate so it is re-read
docker compose up -d n8n
```

### Rehearse it without risking the live database

Restore into a scratch database instead of over the real one. An untested backup
is not a backup, and this costs a minute:

```bash
cd ~/community/automation/infra
LATEST=$(ls -t ~/backups/n8n-*.sql.gz | head -1)
docker compose exec -T postgres psql -U n8n -d postgres -qc "CREATE DATABASE restoretest OWNER n8n;"
gunzip -c "$LATEST" | docker compose exec -T postgres psql -U n8n -d restoretest -q 2>&1 | grep -iE '^ERROR' | sort -u
# compare table counts — they should match
for db in restoretest n8n; do
  echo -n "$db: "
  docker compose exec -T postgres psql -U n8n -d "$db" -tAc \
    "select count(*) from information_schema.tables where table_schema = current_schema()"
done
docker compose exec -T postgres psql -U n8n -d postgres -qc "DROP DATABASE restoretest;"
```

Last rehearsed 2026-08-31: 0 errors, 131 tables in both.

A restore only produces a working instance if `N8N_ENCRYPTION_KEY` in `.env`
on the restoring box is the *same* key that was in use when the dump was
taken — the dump's credential rows are encrypted with it. Restoring a dump
under a different key leaves the credentials in the database but unreadable.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ssh root@…` refused, any credential | This image has no root key, and Debian sets `PermitRootLogin prohibit-password` | Log in as the non-root user from the vault |
| `Permission denied (publickey)` with the right key | Key is passphrase-protected and `ssh-agent` is empty; `BatchMode` cannot prompt to sign | `ssh-add ~/.ssh/<key>`, confirm with `ssh-add -l` |
| `banner line 0: Not allowed at this time`, connection reset | Source IP blocked by brute-force protection, usually from polling SSH in a loop | Stop connecting; wait 10-30 min |
| `REMOTE HOST IDENTIFICATION HAS CHANGED` | The box was reinstalled | `ssh-keygen -R <ip>` — only when you know why it changed |
| Certificate not issued | DNS for the hostname does not resolve to this box, or 80/443 blocked | Check `dig`, `sudo ufw status`; `docker compose logs caddy` |
| Caddy 502 for a few seconds after start | n8n still booting; Caddy has no healthcheck to gate on | Wait; it clears itself |
| `apply.` returns 404 | No workflow serves `/form/apply-ose` yet | Expected until the form exists |
| n8n log mentions SQLite | The `DB_TYPE` block is not taking effect | Fix before storing anything — migrating out of SQLite later is painful |
| `.env` has the variable, `docker compose up -d n8n` says `Running`, `printenv` in the container prints nothing | The compose file on the box predates the variable, so compose never passes it. `docker compose config \| grep <VAR>` prints nothing | `git pull --ff-only` in `~/community`, then `docker compose up -d n8n` and expect `Recreated` |
| Disk filling | Execution history unpruned | Check `EXECUTIONS_DATA_PRUNE=true` and `EXECUTIONS_DATA_MAX_AGE` |
| Credentials all broken after a restore | `N8N_ENCRYPTION_KEY` differs from the one in use when the dump was taken | Restore the original key; there is no recovery without it |
| Every `ovhcloud` command returns `INVALID_CREDENTIAL` (403) | The consumer key in `~/.ovh.conf` was revoked, expired or rotated | `ovhcloud login` |
| Stray `.*.swp` / `..env.swp` in `automation/infra/`, or an editor process nobody remembers | A dropped `ssh -t` session left vim/nano open on `.env`; the swap file holds a copy of the secrets and is not gitignored, and a stale editor that later saves overwrites a rotated key with the old one | `pgrep -a 'vim|nano'`, kill the orphan (editors do not save on plain kill), delete the swap file, then verify `.env` by length/hash |
| Applicant emails rejected or spam-filed | Mail sent from this box; the domain's SPF is `-all` for Proton only | Use an authenticated relay — see "Sending mail" above |
| Workflow fails with `access to env vars denied` | The running container predates `N8N_BLOCK_ENV_ACCESS_IN_NODE: "false"` in `docker-compose.yml`, so n8n blocks the `$env` expressions the workflows are built on | `git pull` in `~/community`, then `docker compose up -d n8n` and expect `Recreated` |

## Notes

- `.env` is never committed — see `automation/.env.example` for the
  application variable names, and the table above for the three infra-only
  ones it doesn't cover.
- Caddy issues and renews its own TLS certificate for `N8N_HOST` automatically
  on first request; that only succeeds once DNS for the hostname resolves to
  this box. Caddy only sees `N8N_HOST` because the `caddy` service in
  `docker-compose.yml` passes it through explicitly via its own
  `environment:` block — Compose's `${VAR}` interpolation of the YAML file
  does not, by itself, put a variable inside a container's environment.
