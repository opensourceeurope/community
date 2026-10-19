# Infra: n8n on Postgres behind Caddy

The VPS that runs the application automation. Three containers: `postgres`
(n8n's database, not the default SQLite), `n8n` itself, and `caddy` as a
reverse proxy that terminates TLS and gets certificates automatically.

Throughout this document, "the box" means this VPS, the one machine that runs
all three containers.

## Quick facts

| | |
|---|---|
| Host | the OVH VPS. Its hostname and IP are in the shared vault, with the SSH access notes |
| Log in as | `ssh debian@{VPS_IP}`, key only, passwordless sudo. Not root |
| Checkout on the box | `~/community` on `main`, kept current by the deploy timer |
| Compose dir | `~/community/automation/infra` |
| Config | `~/community/automation/infra/.env` (mode 600, never committed) |
| Admin URL | https://automation.opensourceeurope.org |
| Applicant URL | https://apply.opensourceeurope.org |
| Backups | `~/backups/n8n-*.sql.gz`, daily, 14 days. An encrypted copy in the `ose-n8n-backups` bucket at Scaleway `pl-waw`, 90 days |
| Deploys | `ose-deploy.timer`, every 10 minutes, from `origin/main` |
| Failure alerts | the reviewers' Slack channel, from `ose-alert@.service` |

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
| Ship a workflow or infra change from `main` | [Deploying on merge](#deploying-on-merge) |
| Find out when a deploy or backup fails | [Alerts when the box fails](#alerts-when-the-box-fails) |
| Copy backups off the box | [The copy off the box](#the-copy-off-the-box) |
| Build a new box with OpenTofu | [`tofu/vps/README.md`](tofu/vps/README.md) |
| Find out when a workflow breaks | [Alerts when a workflow fails](#alerts-when-a-workflow-fails) |
| Find out when the whole box is down | [The external uptime monitor](#the-external-uptime-monitor) |
| Something is broken | [Troubleshooting](#troubleshooting) |
| Get in when SSH refuses you | [Getting into the box](#getting-into-the-box) |
| Change the image people see when they share the form link | [The form's link preview image](#the-forms-link-preview-image) |
| Point Open Collective at this box | [Registering the Open Collective webhook](#registering-the-open-collective-webhook) |

## The box (this VPS)

OVHcloud VPS-1 2027 (plan code `vps-2027-model1`): 2 vCore, 4096 MB, 40 GB
NVMe, in Strasbourg, running Debian 13.

That is about four times what the stack needs. At rest n8n uses 300 to 500 MB,
Postgres about 150 MB and Caddy about 30 MB, for a few workflow executions a
day. What fills a box this size is execution history, not load, and
`EXECUTIONS_DATA_PRUNE` below keeps it bounded.

## Where each secret lives

Each store holds its own secrets, and nothing crosses between them. Confusing
them is easy and has already caused a wrong turn.

| Store | Where | Holds | Not this |
|---|---|---|---|
| `~/.ovh.conf` | the operator's own laptop, mode 600, outside this repo | OVH API credentials only: `endpoint`, `application_key`, `application_secret` and `consumer_key`, used by the `ovhcloud` CLI and the OpenTofu VPS template | nothing about n8n, Postgres or inference |
| `.env` | on the VPS, in `automation/infra/`, mode 600 | `POSTGRES_PASSWORD`, `N8N_ENCRYPTION_KEY`, `AI_API_KEY`, plus all non-secret config | not OVH credentials, not SMTP/Slack/OC credentials |
| n8n's credential store | inside the Postgres database, encrypted with `N8N_ENCRYPTION_KEY` | SMTP, Slack and Open Collective credentials, referenced by name from nodes | anything that has to exist before n8n starts |
| `~/.n8n-api-key`, `~/.slack-bot-token`, `~/.config/ose-backup/backup.env` | on the VPS, mode 600 each | the scoped n8n API key the deploy uses, the Slack bot token the failure alert uses, and the write-only bucket key and age public key the backup uses | not the compose `.env`, which the container receives. None of these is needed inside n8n |
| `~/.config/scw/config.yaml` | the operator's own laptop, mode 600 | the Scaleway API key OpenTofu uses to manage the backup bucket | not the bucket upload key, which lives only on the VPS |
| `~/.n8n-api-key` on the operator's machine | mode 600, read by the shell profile into `N8N_API_KEY` | the n8n API key that exports and edits workflows, as [`automation/README.md`](../README.md) describes | not the compose `.env` on the VPS, which the container receives, and not the box's own deploy key, which has fewer scopes |

The password vault holds a copy of what cannot be regenerated, as separate
entries in a vault shared with more than one admin. See
[the encryption key section](#n8n_encryption_key-back-it-up-off-this-box-before-anything-else).
Recovering this box means finding these entries, so they are named here for
whoever searches the vault later:

- `OSE automation — n8n encryption key` (unrecoverable if lost)
- `OSE automation — Postgres (n8n db)` (resettable)
- `OSE — OVHcloud API` (re-issuable with `ovhcloud login`)
- `OSE automation — backup decryption key` (unrecoverable if lost: every dump in the bucket becomes unreadable)

## Variables this compose file needs

The compose file reads two sets of variables from one `.env` in this
directory:

- Infra variables, in the table below. They belong to this directory, not to
  the workflows. `bootstrap.sh` writes all six.
- Application variables (`DRY_RUN`, `AI_API_KEY`, `FORM_URL_OSE` and so on),
  documented in [`automation/.env.example`](../.env.example) and not repeated
  here, so the two lists cannot drift apart.

| Variable | What it is |
|---|---|
| `N8N_HOST` | The admin hostname, `automation.opensourceeurope.org`: the editor and the Open Collective webhook. n8n builds `WEBHOOK_URL` and `N8N_EDITOR_BASE_URL` from it, and Caddy serves it as a TLS site. |
| `APPLY_HOST` | The applicant hostname, `apply.opensourceeurope.org`, whose bare URL is the application form. See [Two hostnames](#two-hostnames). |
| `EXECUTIONS_DATA_PRUNE` | `true`. Without it n8n keeps the full payload of every execution forever. |
| `EXECUTIONS_DATA_MAX_AGE` | Hours of execution history to keep: `336`, which is 14 days. Long enough to debug what happened to an applicant. |
| `POSTGRES_PASSWORD` | Password for the `n8n` role in the bundled Postgres, from `openssl rand -hex 24`. |
| `N8N_ENCRYPTION_KEY` | Encrypts every credential n8n stores, from `openssl rand -hex 32`. Losing it makes every stored credential unrecoverable. See [the encryption key section](#n8n_encryption_key-back-it-up-off-this-box-before-anything-else). |

## Two hostnames

Both point at this box, each gets its own automatic certificate, and one n8n
container serves both.

| Name | Who sees it | What it serves |
|---|---|---|
| `apply.opensourceeurope.org` | applicants | the OSE application form, at the bare URL |
| `automation.opensourceeurope.org` | you | the n8n editor, and the OC webhook endpoint |

The applicant's link is just `https://apply.opensourceeurope.org`, with no
`/form/...` path. n8n serves forms at `/form/<path>` and keeps the editor at
`/`, so the bare URL is a Caddy rewrite of `/` alone. Page-to-page posts and
assets keep their own paths and stay on the same hostname. That holds only
while n8n's form pages use relative URLs. If an n8n upgrade makes them absolute
and builds them from `N8N_HOST`, an applicant lands on `automation.` after page
one, so check a form submission after an upgrade.

The admin name is not `n8n.` on purpose. Every hostname issued a certificate is
published in Certificate Transparency logs, so a host named after its software
advertises what runs there.

## The form's link preview image

The image a chat or a social post shows for the application link comes from an
Open Graph tag that n8n writes into every form page:

```html
<meta property="og:image" content="/static/og_image.png" />
```

n8n hardcodes that path in its form template. No node parameter and no
environment variable changes it, and the file behind it carries n8n branding.
Caddy therefore serves this one path itself, from
`automation/infra/public/static/og_image.png`, and proxies everything else to
n8n. The form page loads no other `/static/` asset, so Caddy intercepts nothing
else that n8n serves.

The image is the Open Source Europe logo on the brand background, sized
1200x630, the same as the file it replaces.

To change the image, replace that file in a pull request. The deploy pulls it
onto the box, and Caddy reads it from a mounted directory on every request, so
nothing restarts. Confirm it by comparing the byte count of the served file
with the file in git:

```bash
curl -s https://apply.opensourceeurope.org/static/og_image.png | wc -c
```

Social platforms cache what they scraped, so a link someone already shared
keeps the old image until that cache expires.

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
  covers generating and storing it. Intake confirms every delivery against the
  Open Collective GraphQL API with that credential.
- `apply 1a — intake` is active. n8n serves the production `/webhook/` path
  only while the workflow is active, and an inactive workflow answers 404.
  Open Collective sends each delivery once, so one that lands on a 404 is
  lost until the catch-up finds it.

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

- Leave it to the catch-up. It reads each open application's current status
  from the API, so a rejection is recorded and the closing email goes out on
  the next sweep, up to one sweep interval after the decision.
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

A decision webhook makes a decision arrive sooner, and the catch-up is what
guarantees it arrives at all: it asks the API for the status of every
application still open in the data table.

Nothing else needs configuring on either side. Open Collective sends no
signature, and the endpoint accepts any POST, so intake never acts on a payload
alone. A `collective.apply` delivery carries no application data and intake
re-fetches the pending applications. A decision delivery names the collective,
and intake confirms the decision with one API call for that collective before
writing anything. A forged or replayed delivery therefore changes nothing.

After the first real delivery, open the execution list of
`apply 1a — intake` and confirm the delivery arrived and wrote its row.

## The n8n community licence is optional

n8n mails a free activation key (Settings → Usage and plan) that unlocks
workflow history, folders and debugging in the editor. Nothing in this
automation depends on it.

An activated key lives in the database (`settings`, key `license.cert`), not in
`.env`, so the dump covers it and a restore brings it back. Keep a copy in the
vault for a rebuild with no dump to hand.

To check whether one is active:

```bash
docker compose exec -T postgres psql -U n8n -d n8n -tAc "select key from settings order by key"
```

## Sending mail: not from this box

`opensourceeurope.org` publishes `v=spf1 include:_spf.protonmail.ch -all`, a
hard fail for any sender that is not Proton. This VPS is not an authorised
sender and has no reverse DNS, so mail sent directly from it as
`@opensourceeurope.org` will be rejected or filed as spam.

So the workflows send through an authenticated relay at an authorised sender.
Keep the VPS out of SPF: adding it would let a box running arbitrary workflows
send as the whole domain.
[The SMTP credential](#the-smtp-credential) describes the relay in use.

## Getting into the box

Three things cause almost every failed login here:

- The login user is `debian`, not `root`. OVH's Debian 13 image creates
  `debian` with passwordless sudo and puts the SSH key there. `root` gets no
  key, and Debian's `PermitRootLogin prohibit-password` refuses root passwords,
  so `ssh root@…` fails whatever is configured.
- A passphrase-protected key with an empty `ssh-agent` looks like a missing
  key. `ssh -o BatchMode=yes` cannot ask for the passphrase, so it reports
  `Permission denied (publickey)`. Check the local side first: `ssh-add -l`
  lists the key, and `ssh-keygen -y -f ~/.ssh/{KEY}` proves the passphrase.
- Polling SSH in a loop gets your IP blocked. The box then resets the
  connection with `banner line 0: Not allowed at this time` before
  authentication. Stop, wait 10 to 30 minutes, then try once.

When SSH is out of reach, the `ovhcloud` CLI hands you a console URL. The
console keyboard can be AZERTY, so `q` types `a`, and the password prompt does
not echo. Type the password at the `login:` prompt, which does echo, to see
what arrives. Swapping `vnc_lite.html` for `vnc.html` in the console URL adds a
clipboard panel that bypasses the keyboard layout.

To put a key back on the box, choose it in the OVH Manager's reinstall dialog.
OVH then installs it for `debian` and sends no password, and a delivery mail
with a user name and no password confirms the key went in. A reinstall wipes the
disk, so restore from [the copy off the box](#the-copy-off-the-box) afterwards.

Never remove one way into a machine before its replacement is proven. Turning
off passwords before a key login works leaves no way in.

### The `ovhcloud` CLI needs credentials, and they expire

The `ovhcloud` CLI and the OpenTofu VPS template read OVH API credentials from
`~/.ovh.conf` (mode 600): endpoint, application key, application secret and
consumer key. Keep the file in your home directory. `ovh.conf` is gitignored as
a backstop.

`INVALID_CREDENTIAL` or `This credential is not valid` (403) on every call means
the consumer key was revoked or expired. It is not a permissions problem with
the VPS. Run `ovhcloud login`, which opens a browser consent flow and rewrites
the file. These credentials are cheap to lose: revoke and re-issue them at will.

## First install, from a fresh VPS

To rebuild this box after losing it, restore a dump onto a fresh install, as
[Which backup to restore, when](#which-backup-to-restore-when) describes. The
dump brings back the workflows with their ids, the credentials, the data table
and every application. The rest of this section builds an instance with none of
that: a copy of this system somewhere else, or a rebuild with no dump.

### Accounts you need first

| Service | What it is for | Where its secret ends up |
|---|---|---|
| OVHcloud | the VPS and the DNS zone for both hostnames | `~/.ovh.conf` on your own machine |
| Scaleway | the model behind the review and the summary, and the backup bucket | `AI_API_KEY` in `.env`, and `~/.config/scw/config.yaml` on your own machine |
| Open Collective | an admin of the host account, for its API token | the n8n credential `oc-host-admin` |
| Slack | an app with a bot token, invited to the reviewers' channel | the n8n credential `slack-bot`, and `~/.slack-bot-token` on the box |
| Proton Mail | an SMTP token for the sending address | the n8n credential `smtp-proton` |
| a shared password vault | the encryption key, the backup decryption key and the bucket key | the vault |
| an uptime service | an outside check on the applicant hostname | that service's account |

### Install the box

[`bootstrap.sh`](bootstrap.sh) installs the box. To order a new VPS and run
the script in one step, use the OpenTofu module in [`tofu/vps/`](tofu/vps/).
To install onto a VPS you already have, log in as `debian` and run the script
yourself.

The script needs `git` and `sudo`. A minimal Debian 13 image lacks `git`, so
install it first:

```bash
sudo apt-get update -qq && sudo apt-get install -y -qq git
```

The following commands clone the repository and run the script. Pass the two
hostnames the box serves:

```bash
git clone https://github.com/opensourceeurope/community.git ~/community
~/community/automation/infra/bootstrap.sh all --n8n-host {ADMIN_HOSTNAME} --apply-host {APPLY_HOSTNAME}
```

Each step checks first and skips work that is already done. A second run
changes nothing, and the script never edits an existing `.env`. In order, it:

1. Installs `git`, `ufw`, `unattended-upgrades`, `ca-certificates`, `curl`,
   `openssl`, `python3` and `age`.
2. Allows ports 22, 80 and 443 in `ufw`, then enables the firewall. Postgres is
   never published to the host. Only the compose network reaches it.
3. Turns off password login for SSH. It skips this step and warns you when
   `~/.ssh/authorized_keys` holds no key, because turning passwords off without
   a key locks you out.
   Then it switches on daily security updates, Docker's own packages included,
   with a reboot at 04:30 when a kernel update needs one. See
   [Keeping the box patched](#keeping-the-box-patched).
4. Installs Docker and adds `debian` to the `docker` group.
5. Clones the repository into `~/community`.
6. Writes `.env` from `automation/.env.example`, with the hostnames you passed
   and a generated `POSTGRES_PASSWORD` and `N8N_ENCRYPTION_KEY`. The secrets are
   generated on the box so they never travel, and the script never prints them.
   `DRY_RUN` stays at its safe default.
7. Checks that `docker compose config` reports no unset variable, then starts
   the stack.
8. Adds `debian` to `systemd-journal` so the failure alert can read other units'
   logs, installs every unit in [`systemd/`](systemd/), and enables the backup
   timer. It enables the deploy timer only once `~/.n8n-api-key` exists, because
   without the key every run fails.

### After the script

The script ends with the steps it cannot do for you. Do them in this order:

1. Copy `N8N_ENCRYPTION_KEY` from `.env` into the password vault. See
   [the encryption key section](#n8n_encryption_key-back-it-up-off-this-box-before-anything-else).
2. Create the n8n owner account at the admin URL.
3. Create two n8n API keys. Your own key stays on your own machine, as
   [`automation/README.md`](../README.md#providing-the-n8n-api-key-to-claude-code)
   describes. The box's deploy key goes in `~/.n8n-api-key`, as in
   [The API key the box uses](#the-api-key-the-box-uses).
4. Create the `ose_applications` data table from your own machine, with your own
   key in `N8N_API_URL` and `N8N_API_KEY` or `N8N_API_KEY_FILE`. The script
   reads the columns from [`automation/docs/data-tables.md`](../docs/data-tables.md),
   only ever adds, and a second run changes nothing:

   ```bash
   python3 automation/scripts/create-data-table.py --dry-run
   python3 automation/scripts/create-data-table.py
   ```

5. Create the n8n credentials `oc-host-admin`, `slack-bot` and `smtp-proton`,
   as the credential sections below describe. The workflows find them by those
   names.
6. Set `AI_API_KEY`, `SLACK_CHANNEL`, `SMTP_FROM` and `DRY_RUN_RECIPIENT` in
   `.env`, as in
   [Setting the credentials that are not generated here](#setting-the-credentials-that-are-not-generated-here).
   The deploy refuses to run while any of them is empty. Leave `DRY_RUN` alone,
   so it stays at `true`.
7. Write the Slack bot token, as in [The token](#the-token), and run
   `bootstrap.sh units`. Then stop the deploy timer with
   `sudo systemctl stop ose-deploy.timer` until step 9 is merged.
8. Create the workflows once with `deploy.sh --allow-create`, as in
   [An export the instance does not have](#an-export-the-instance-does-not-have).
9. Point the workflows at each other. The exports carry no workflow id, so the
   new instance gave every workflow a new one, and the exports still name the
   old ids in three places. Nothing reports an error for them, apart from a
   warning for the first:
   - each Execute Workflow node's target, which is the workflow it hands off to
   - each `apply` workflow's `errorWorkflow` setting, which names
     `ops — alert Slack when any apply workflow fails`
   - the id map at the top of [`automation/scripts/export-workflows.py`](../scripts/export-workflows.py)

   Read each new id from the workflow's URL in the editor, replace every old id
   in a branch, and merge it. Fixing them only in the editor does not last,
   because the next deploy pushes the exported ids back. Then start the timer
   again with `sudo systemctl start ose-deploy.timer`.
10. Ask whoever owns the instance before activating anything. Activate each
    callee before its caller, because n8n refuses to save an active workflow
    whose callee has never been active: apply 5 and apply 3 first, then
    apply 2 and apply 4, then apply 1a and apply 1b.
11. [Register the Open Collective webhook](#registering-the-open-collective-webhook).
12. Set up [the copy off the box](#the-copy-off-the-box) and
    [the external uptime monitor](#the-external-uptime-monitor).
13. Rehearse with `DRY_RUN=true`, as the
    [testing section](../README.md#testing-safely) describes, then delete every
    row the rehearsal touched. Going live is writing `DRY_RUN=false` into `.env`
    and running `docker compose up -d`. It is a decision, not a setup step.

The unit files run as `debian` out of `/home/debian/community`, which is what
OVH's Debian image provides. On another account, change those lines in git
first. The script stops when the account or the path does not match.

### Verify the install

Run these checks in order. The first proves Postgres is the backend: if the
table list is empty, n8n fell back to SQLite, so stop and fix `DB_TYPE` before
anything is stored.

```bash
cd ~/community/automation/infra
docker compose exec -T postgres psql -U n8n -d n8n -c "\dt" | head
docker compose logs n8n | grep -ci sqlite      # expect 0
```

Then check the certificates and both hostnames from outside the box. The
admin hostname must answer `200`, and the applicant hostname `404` until the
form workflow is active:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://{ADMIN_HOSTNAME}/
curl -sS -o /dev/null -w '%{http_code}\n' https://{APPLY_HOSTNAME}/
```

A 404 on the applicant hostname is correct until the form workflow is active.
Caddy rewrites `/` to `/form/apply-ose`, and n8n answers 404 while no active
workflow serves that path. An n8n-rendered 404 page proves the rewrite works.

If the owner account was created with the wrong email, change it under
**Settings → Personal** in n8n. If that field is unavailable without SMTP,
update the `user` table in Postgres instead. As a last resort,
`docker compose exec -T n8n n8n user-management:reset` deletes the account and
brings the setup screen back. Check first that there is nothing to lose:

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

After editing `.env`, `restart` is not enough. `docker compose restart` reuses
the container with its old environment, and only a recreated container reads
`.env` again:

```bash
docker compose up -d n8n           # recreates with the new .env values
```

Change `docker-compose.yml`, the `Caddyfile` and the units in git, never on the
box. The deploy refuses to run over local changes in the checkout, and it
applies the merged version within ten minutes.

## Deploying on merge

A merge to `main` reaches the box because the box pulls it. No n8n API key sits
in a GitHub secret, and nothing is opened inbound for a deploy.

`ose-deploy.timer` runs [`deploy.sh`](deploy.sh) every 10 minutes. One run:

1. Fast-forwards `~/community` to `origin/main`.
2. Applies what changed in `automation/infra/`, then records the commit it
   applied.
3. Runs the same eight export checks that CI runs on every pull request, then
   pushes each file in `automation/n8n/` into the running n8n over the public
   API.

Infra comes before workflows because a workflow change can depend on an
environment variable that only a recreated n8n container carries.

### What an infra change does

The script compares the commit it last applied with the new one. It records
that commit in `~/.local/state/ose-deploy/infra-applied` after a successful
apply, so a failed apply is retried on the next run. The table lists what each
changed path triggers:

| Changed path | What the run does |
|---|---|
| `docker-compose.yml` | Checks the file, then runs `docker compose up -d` |
| `Caddyfile` | Checks the file, then recreates the `caddy` container |
| `systemd/*` | Copies each changed unit into `/etc/systemd/system/`, reloads systemd, and enables and starts each timer it copied |
| `apt/*` | Installs `unattended-upgrades` if it is missing, then copies each changed file into `/etc/apt/apt.conf.d/` |

Two things are checked on every run, changed or not, because a run can miss
them: each timer in `systemd/` must be enabled, and each file in `apt/` must
match the copy in `/etc/apt/apt.conf.d/`. A run puts right whichever is not.
`ose-deploy.timer` is the exception: switching it off is how a person pauses
deploys, so the deploy never switches it back on.

When a fast-forward changes `deploy.sh` itself, the run starts again as the new
version before it applies anything. Bash has already read the old script, and
without the restart the old version would apply the new commit and record it,
so whatever the new version does differently would never happen.
| `public/*` | Nothing. The directory is mounted, so the pull alone makes it live |
| a script | Nothing. The units run the scripts out of the checkout |

The Caddyfile needs a new container rather than a restart. Git replaces a file
with a new one, and a mount of a single file keeps showing the old one.

Every run first checks that the values the stack cannot work without are set
in `.env`: `N8N_HOST`, `APPLY_HOST`, `POSTGRES_PASSWORD`, `N8N_ENCRYPTION_KEY`,
`AI_API_KEY`, `SLACK_CHANNEL` and `SMTP_FROM`, and `DRY_RUN_RECIPIENT` while
`DRY_RUN` is anything but `false`. An empty one stops the run and names it,
without printing any value.

Before it changes a running container, the run checks two more things. Compose
must report no variable missing from `.env`. Compose only warns about a missing
variable and passes an empty string, which is how a new variable would reach
production empty. When the Caddyfile changed, `caddy validate` must accept it.

A failed check stops the run before anything changes, workflow updates
included, and posts an alert. Fix `.env` or the file, and the next run applies
the change. CI makes the compose check cover every variable a workflow reads:
check 8 of `check-workflows.py` fails a pull request whose workflow reads a
variable the compose file does not pass to n8n.

Sometimes the n8n image in the compose file differs from the image the running
container uses. Then the run calls `backup.sh` first, and stops if the backup
fails. That covers a version bump merged to `main`, and a box whose n8n never
matched the compose file.

After it restarts n8n or Caddy, the run waits up to two minutes for n8n's
public API to answer, with the deploy key, before it syncs the workflows. A
recreated n8n takes 30 to 60 seconds to boot, and until then Caddy answers 502.
It waits for the API rather than `/healthz` because n8n answers `/healthz` a few
seconds before it mounts `/api/v1`, and a sync started in that gap fails with
`Cannot GET /api/v1/workflows`.

Before it syncs the workflows, every run adds any column that
[`automation/docs/data-tables.md`](../docs/data-tables.md) lists and the
`ose_applications` table lacks, with `create-data-table.py`. A pull request
that adds a column ships the workflow that writes it, and that workflow would
fail on every row it touched against a table without the column. So a run
that cannot add a column stops before the workflows change, and posts an
alert. The script only adds. It never removes, renames or retypes a column,
and it does not create a missing table: that is step 4 of the rebuild, with
your own key.

A run never stops or removes a container, never pulls an image whose tag did
not change, and never edits `.env`. Deleting a unit file in git does not remove
it from the box. The run warns, and you remove it by hand.

### What it never does to a workflow

Each export goes to the workflow of the same name, updated in place. The
workflow keeps its id, so every Execute Workflow node that calls it keeps
working, and a second run in a row changes nothing.

| It never | What stops it |
|---|---|
| Activates or deactivates a workflow | `active` is read-only in the public API, and the deploy key needs no activation scope |
| Adds a second workflow under a name the instance already has | It matches by name and updates the workflow it matched |
| Reads or writes a credential | An export names a credential by id, and the script never calls the credential endpoints |
| Deploys a branch or local edits | It stops unless the checkout is on `main` and clean |

### The API key the box uses

The deploy calls the instance's own public API, so the key belongs on the box,
in `~/.n8n-api-key`, mode 600. Keep it out of `.env`: every variable in that
file is injected into the n8n container, and nothing in the container reads
this key.

Create the key in the n8n UI under **Settings**, **n8n API**. Where the dialog
offers scopes, give it `workflow:list`, `workflow:read`, `workflow:update`,
`dataTable:read` and `dataTableColumn:create`, and nothing else. The last two
let the deploy add the columns a merge brings, as
[What an infra change does](#what-an-infra-change-does) describes. A key
without `workflow:activate` cannot start a workflow even if something asks it
to, and a key without `dataTable:create` cannot create a second table.

The following commands create the file with mode 600 before the key goes in,
then check its length rather than its content:

```bash
touch ~/.n8n-api-key && chmod 600 ~/.n8n-api-key
nano ~/.n8n-api-key    # paste the key as one line, no quotes
wc -c ~/.n8n-api-key   # the key length plus one for the newline
```

### Install the timer

[`bootstrap.sh`](bootstrap.sh) installs the units. On a box that already runs
the stack, follow this order. Nothing in it changes a running container until
the first real deploy.

1. Take a dump and get a verified copy off the box, either in the bucket or as
   in [Take a verified copy by hand](#take-a-verified-copy-by-hand).
2. Bring the checkout up to date, because the scripts arrive with it:

   ```bash
   cd ~/community && git checkout main && git pull --ff-only
   ```

3. Put the deploy key in `~/.n8n-api-key`, as in
   [The API key the box uses](#the-api-key-the-box-uses), and the Slack token in
   `~/.slack-bot-token`, as in [The token](#the-token).
4. Install the units, then stop the deploy timer at once, so that nothing
   deploys before you have read a dry run:

   ```bash
   ~/community/automation/infra/bootstrap.sh units
   sudo systemctl stop ose-deploy.timer
   ```

5. Send a test alert, as in [Test the alert](#test-the-alert).

`units` installs `python3`, `curl` and `age`, adds `debian` to
`systemd-journal`, copies every file in [`systemd/`](systemd/) into
`/etc/systemd/system/`, and enables the backup timer. It enables the deploy
timer only when `~/.n8n-api-key` exists, which is why step 4 stops it. The
`systemd-journal` group applies from the next login, so until then read the
journal with `sudo journalctl`.

Prove the deploy works before the timer runs it. The dry run reads the
instance, prints the commits it would fast-forward past, the infra actions and
the workflows it would change, and changes nothing:

```bash
~/community/automation/infra/deploy.sh --dry-run
```

The dry run checks the compose file and the Caddyfile from `origin/main`, not
the checkout, so a check that fails here fails the real run too. A variable
the compose file names but `.env` lacks stops it. Add each one it names. To
keep today's behaviour for a variable whose workflows fall back to a default,
write that default explicitly, using the value in `automation/.env.example`. An error names
the assumption that is wrong: the key file, its mode, `N8N_HOST` in `.env`, a
required value that is empty, a variable missing from `.env`, or a checkout on
another branch or with local changes. The export checks run from `origin/main`
as well, against the compose file they will deploy with.

> [!CAUTION]
> Read the `n8n image` line of the dry run before the first real run. When it
> shows a running image that differs from the configured one, the real run
> backs up the database and then upgrades n8n. Read
> [Upgrading n8n](#upgrading-n8n) first if you did not plan that upgrade.

The following commands deploy by hand, twice:

```bash
~/community/automation/infra/deploy.sh
~/community/automation/infra/deploy.sh
```

The first run of a full apply recreates n8n and Caddy, so the form and the
editor are away for about a minute. The second run must report
`no infra changes` and every export as unchanged. Anything else means the
comparison is wrong, and the timer would rewrite those workflows every ten
minutes for as long as it is enabled. Stop here if that happens.

Then start the timer, and watch its first run:

```bash
sudo systemctl start ose-deploy.timer
sudo journalctl -u ose-deploy.service -f
```

A merge to `main` is the end-to-end test. Within about ten minutes the journal
shows the fast-forward to the merge commit, followed by the same report.

### See what it did

The following commands show when the timer fires next, start one run now, and
print what the last runs did:

```bash
systemctl list-timers ose-deploy.timer
sudo systemctl start ose-deploy.service
journalctl -u ose-deploy.service -n 50
```

A run prints the commits it fast-forwarded past, the infra actions, then one
line per export. An updated export names the nodes that changed, and every line
says whether that workflow is active.

### Turn it off

The following command stops the timer:

```bash
sudo systemctl disable --now ose-deploy.timer
```

Merges to `main` stop reaching the box, and nothing else changes. Deploy by
hand with `~/community/automation/infra/deploy.sh` whenever you want one. To
remove it completely, delete `ose-deploy.service` and `ose-deploy.timer` from
`/etc/systemd/system/` and run `sudo systemctl daemon-reload`.

### An export the instance does not have

The run reports that file as failed, exits non-zero, and deploys the rest. A
name with no match is either a new workflow or one that was renamed in the n8n
UI, and the script cannot tell which. Guessing wrong leaves two copies of one
pipeline stage on the instance.

Deploy that file once by hand, then let the timer take over again:

```bash
~/community/automation/infra/deploy.sh --allow-create
```

The new workflow arrives inactive, with an id of its own. An Execute Workflow
node names its target by id, so open any hand-off into the new workflow in the
n8n UI and point it at the id that now exists. Then add that id to the map in
`automation/scripts/export-workflows.py`, or the next export skips the file.

## Alerts when the box fails

The deploy and backup units both name `ose-alert@.service` in `OnFailure=`.
When either one fails, [`alert-slack.sh`](alert-slack.sh) posts the unit name,
the host and the last 15 lines of its journal to `SLACK_CHANNEL`. That is the
channel the application threads and the workflow failure alerts use. Text from
the log is escaped, so a line containing `<!channel>` cannot notify the
channel.

The deploy timer fires every ten minutes, and some failures last until a person
acts. So a failure alerts once. The script records a delivered alert in
`~/.local/state/ose-alert/`, and the next successful run of the unit posts that
it recovered and clears the note. An alert that Slack refused is retried on the
next failure.

### The token

The script posts with the Slack bot token that n8n uses for the `slack-bot`
credential. n8n's API never returns a credential's value, so take it from the
source: the **Bot User OAuth Token** on the Slack app's **OAuth & Permissions**
page. Paste it into a file that is already mode 600, and check its length rather
than its content:

```bash
touch ~/.slack-bot-token && chmod 600 ~/.slack-bot-token
nano ~/.slack-bot-token    # paste the xoxb- token as one line
wc -c ~/.slack-bot-token
```

The token reaches `curl` on its standard input, never as an argument, so it
does not appear in a process list. The script refuses a token file that other
users can read.

### Test the alert

The following command sends a test alert for a unit named `test`:

```bash
sudo systemctl start ose-alert@test.service
```

The channel shows `test failed on` the host name. A second start posts nothing,
because the alert for `test` is already delivered. The following command clears
it and posts the recovery:

```bash
~/community/automation/infra/alert-slack.sh --recovered test
```

When nothing arrives, read `journalctl -u ose-alert@test.service`. The script
names the reason: a missing or readable token file, no `SLACK_CHANNEL`, or the
error Slack returned. `not_in_channel` means the bot was never invited to that
channel.

## Setting the credentials that are not generated here

Compose passes into the container only the variables its compose file names,
so a value set in `.env` for a variable the box's compose file does not know yet
is silently ignored. The deploy timer keeps the checkout current. Without it,
bring the checkout up to date before changing `.env`:

```bash
ssh debian@{VPS_IP} 'git -C ~/community checkout main && git -C ~/community pull --ff-only'
```

`Already up to date.` or a fast-forward are both fine. Anything else means the
box has local changes, and they need a look before going on.

### Setting a plain configuration value

Most variables in `automation/.env.example` are not secrets: `DRY_RUN`,
`DRY_RUN_RECIPIENT`, the two `*_AFTER_MINUTES` timers, `SWEEP_CRON`,
`FORM_URL_OSE`, `AI_MODEL`, `AI_BASE_URL`, `SMTP_FROM` and `SLACK_CHANNEL`.
Any of them can be set over `ssh` directly. Replace `{VAR}`
with the variable name and `{VALUE}` with the value:

```bash
ssh debian@{VPS_IP} 'cd ~/community/automation/infra
  grep -q "^{VAR}=" .env || echo "{VAR}=" >> .env
  sed -i "s|^{VAR}=.*|{VAR}={VALUE}|" .env
  grep "^{VAR}=" .env
  docker compose up -d n8n
  docker compose exec -T n8n printenv {VAR}'
```

The first line appends an empty `{VAR}=` when the variable is missing,
because `sed` replaces only a line that exists and otherwise does nothing
without saying so. The second `grep` must print the line as written.
`docker compose up -d n8n` must print `Recreated`, not `Running`: the
container reads `.env` only when it is created, and `Running` means the value
inside it is still the old one. The final `printenv` shows what the workflows
will actually read. A value containing `#` needs double quotes, as in
`SLACK_CHANNEL=\"#{CHANNEL}\"`, because an unquoted `#` can start a comment
in a compose `.env`. Spaces and angle brackets need no quoting.

Set `DRY_RUN_RECIPIENT` to a real mailbox this way before activating any
workflow. While applicant email is suppressed, which is any value of `DRY_RUN`
other than `false`, every render step throws when it is empty.

### Setting the AI key

`AI_API_KEY` (Scaleway inference) is the one secret this box needs that it
cannot generate for itself.

Use a key dedicated to this automation, not the one `ose-knowledge-mcp` uses.
Separate keys rotate independently, limit what a leak exposes, and show which
system spent what.

Two files carry these variable names, and only one takes real values.
`automation/infra/.env` on the box is the real config. `automation/.env.example`
is a committed template whose secret values are empty on purpose, and a key
written there is published in a public repository. Check the path, not the
contents.

Set it by editing the file on the box, and watch what you type. In `nano`,
press `Ctrl+W`, search for `AI_API_KEY`, paste the key after the `=` with no
spaces and no quotes, then save with `Ctrl+O` and `Enter` and leave with
`Ctrl+X`. Then recreate n8n so it reads the new value:

```bash
ssh -t debian@{VPS_IP} 'nano ~/community/automation/infra/.env'
ssh debian@{VPS_IP} 'cd ~/community/automation/infra && docker compose up -d n8n'
```

Expect `Recreated`, not `Running`. Compose recreates the container only when
the resolved environment differs, so `Running` means the value did not change.

Then check lengths, never values. Use an explicit `if`: an expansion such as
`${V:+set}` resolves to the value itself whenever the variable is set.

```bash
ssh debian@{VPS_IP} 'cd ~/community/automation/infra
  awk -F= "/^AI_API_KEY=/{print \"env file:\", (length(\$2)?\"set\":\"EMPTY\")}" .env
  V=$(docker compose exec -T n8n printenv AI_API_KEY | tr -d "\r\n")
  if [ -n "$V" ]; then echo "container: set, ${#V} chars"; else echo "container: EMPTY"; fi'
```

If your terminal swallows nano's paste, this prompts on the box with a TTY, so
the paste lands where it is read, and it refuses to write an empty value:

```bash
ssh -t debian@{VPS_IP} 'printf "paste key then Enter: "; IFS= read -r K; \
  [ -n "$K" ] && sed -i "s|^AI_API_KEY=.*|AI_API_KEY=$K|" ~/community/automation/infra/.env \
    && echo "written, ${#K} chars" || echo "EMPTY, nothing written"'
```

Avoid a piped one-liner such as `read -rs KEY` into `ssh … sed`. When the read
captures nothing, `sed` writes an empty value, compose prints `Running`, and
every step reports success while the key is missing.

## The Open Collective host-admin credential

The two intake workflows, `apply 1a — intake` and `apply 1b — catch-up`,
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
   | `apply 1b — catch-up` | **Fetch pending applications** |
   | `apply 1b — catch-up` | **Fetch application status** |

   The workflows reference the credential by name, so once a credential
   named exactly `oc-host-admin` exists, n8n resolves the reference on its
   own. Open each workflow from the workflow list, open the node, and
   confirm that **Credential for Header Auth** shows `oc-host-admin`
   without an error marker. A node that shows an empty or red credential
   field means the name does not match, so fix the credential name rather
   than picking manually, or the next re-import breaks the same way.

### Confirm by effect

Open `apply 1b — catch-up` in the editor and execute the
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

Every application gets a parent message in the reviewers' channel, each stage
replies in its thread, and a failed workflow posts an alert. All of it goes
through n8n Slack nodes, which authenticate with a Slack app's bot token. An
incoming-webhook URL does not work: the node offers only **Access Token** and
**OAuth2**. The box's own failure alert, in
[Alerts when the box fails](#alerts-when-the-box-fails), posts with the same
token.

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
   and add two **Bot Token Scopes**: `chat:write` and `reactions:write`.
   That is the whole requirement: the pipeline posts a message per
   application and a reply per stage, and marks the parent message with a
   reaction as each stage lands. n8n's documentation suggests a much longer
   list for general use. Do not add it: every extra scope widens what a
   leaked token can do without adding anything the pipeline uses.
4. At the top of the same page, choose **Install to Workspace** and allow
   the request. The page then shows a **Bot User OAuth Token**, starting
   with `xoxb-`. That is the value n8n needs. Treat it like a password from
   this point on: do not paste it into chat, an issue, or a commit.
5. In Slack, open the channel the notifications should reach and invite the
   app with `/invite @{BOT_USERNAME}`. The username is the one shown under
   **App Home** for the app's bot user, not the app's display name. A bot
   can post only into channels it is a member of. Posting without membership fails with `not_in_channel`,
   and inviting the app is the fix, not adding scopes.

### Store it in n8n

The token lives in n8n's credential store and in `~/.slack-bot-token` on the
box. Any collaborator can re-issue it from the app's **OAuth & Permissions**
page, so it needs no vault entry. The app's existence and its
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
6. Attach the credential to the twenty-two Slack nodes. They are the only
   places the workflows talk to Slack, and every one of them needs it:

   | Workflow | Node |
   |---|---|
   | `apply 1a — intake` | **Post application parent message to Slack** |
   | `apply 1a — intake` | **Reply with the application message in thread** |
   | `apply 1a — intake` | **Reply with the decision in thread** |
   | `apply 1a — intake` | **React with the decision on the parent** |
   | `apply 1a — intake` | **Reply that the applicant went elsewhere** |
   | `apply 1a — intake` | **React with an amber light on the parent** |
   | `apply 1b — catch-up` | **Post application parent message to Slack** |
   | `apply 1b — catch-up` | **Reply with the application message in thread** |
   | `apply 1b — catch-up` | **Reply with the decision in thread** |
   | `apply 1b — catch-up` | **React with the decision on the parent** |
   | `apply 1b — catch-up` | **Reply that the applicant went elsewhere** |
   | `apply 1b — catch-up` | **React with an amber light on the parent** |
   | `apply 2 — AI review` | **Reply with the AI review in thread** |
   | `apply 2 — AI review` | **React with eyes on the parent** |
   | `apply 3 — follow-up` | **Reply that the invitation was sent** |
   | `apply 3 — follow-up` | **Reply that the reminder was sent** |
   | `apply 3 — follow-up` | **Reply that the applicant went silent** |
   | `apply 3 — follow-up` | **React with an alarm on the parent** |
   | `apply 4 — application form` | **Reply that the form was submitted** |
   | `apply 4 — application form` | **React that a decision is needed** |
   | `apply 5 — application summary` | **Reply with the summary in thread** |
   | `ops — alert Slack when any apply workflow fails` | **Post failure alert to Slack** |

   Open each workflow, open the node, pick `slack-bot` under
   **Credential to connect with**, and save the workflow. Then export every
   changed workflow into `automation/n8n/` so the exports carry the
   reference. From then on a re-import resolves the credential by name, as
   the Open Collective nodes already do.

   Count the table against what you have attached before moving on. A node
   left without the credential does not complain: every Slack node carries
   `onError: continueRegularOutput`, so one unreachable row cannot abort a
   run, and the same setting turns a missing credential into a green
   execution that posted nothing.

### Set the channel

Set `SLACK_CHANNEL` with the recipe in
[Setting a plain configuration value](#setting-a-plain-configuration-value).
Keep the `#` and wrap the value in double quotes, as in `"#{CHANNEL}"`: an
unquoted `#` can start a comment in a compose `.env`, and compose strips the
quotes before the container sees the value.

For a public channel the name works, written with a leading `#`. The node
passes the value straight to Slack's `chat.postMessage`, which resolves
public channel names itself. For a private channel use the channel ID
instead, a string starting with `C` shown at the bottom of the channel's
details pane in Slack. A private channel's name is not resolvable by a bot
and the post fails with `channel_not_found`.

### Confirm by effect

`DRY_RUN` does not gate Slack. The thread is the reviewers' own log, internal
to the team and carrying no applicant address, so every message and reaction
posts to `SLACK_CHANNEL` for real, a dry-run rehearsal included. That makes the
rehearsal the test. Let one application through intake and watch the channel: a
parent message appears, and each stage that follows replies in its thread and
marks the parent with a reaction. That proves the token, both scopes and the
channel membership together.

Watch for the silent case. Every Slack node carries
`onError: continueRegularOutput`, so a missing credential, a revoked token or a
channel the bot was never invited to fails without failing the run. The
execution is green and the channel is empty. An application that reached a stage
with nothing in Slack to show for it is that symptom. Open the execution and the
Slack node carries the real error, usually `not_in_channel`, `invalid_auth` or
`missing_scope`.

To test before an application exists, create a throwaway workflow in n8n with a
**Manual Trigger** and one **Slack** node, resource **Message**, operation
**Send**, credential `slack-bot`, channel set to the same value as
`SLACK_CHANNEL`, and any text. Execute it once. A message appearing in the
channel is the confirmation. Delete the throwaway workflow afterwards so it
never shows up in the workflow list as a real one.

### Rotate

On the app's **OAuth & Permissions** page choose **Revoke All OAuth
Tokens**, then **Install to Workspace** again. Slack issues a new
`xoxb-` token. Replace the **Access Token** in the existing `slack-bot`
credential in n8n and save, and write it into `~/.slack-bot-token` on the box.
The nodes reference the credential by name, so nothing else changes. The old
token stops working the moment it is revoked, so do these steps together.

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
   | `apply 1b — catch-up` | **Send decision email** |
   | `apply 3 — follow-up` | **Send form invitation** |
   | `apply 3 — follow-up` | **Send reminder** |
   | `apply 4 — application form` | **Send confirmation email** |

   Open each workflow, open the node, pick `smtp-proton` under
   **Credential to connect with**, and save the workflow. Then export the
   changed workflows into `automation/n8n/` so the exports carry the
   reference.

### Set the From address

Set `SMTP_FROM` with the recipe in
[Setting a plain configuration value](#setting-a-plain-configuration-value), to
`Open Source Europe <home@opensourceeurope.org>`. The angle brackets and spaces
need no quoting.

The address part must be the address the token was generated for. Proton
issues one token per address and documents that a token sends as that
address, so a From header naming another address is not covered by the
token and fails or gets rewritten at Proton's side, not silently accepted.

### Confirm by effect

A dry run exercises SMTP for real: while applicant email is suppressed, which
is any value of `DRY_RUN` other than `false`, every message still goes out, to
`DRY_RUN_RECIPIENT` instead of the applicant. So the first dry-run rehearsal is
the test. A message arriving at `DRY_RUN_RECIPIENT` from
`home@opensourceeurope.org` proves the token, the port settings and the From
address together.

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

`N8N_ENCRYPTION_KEY` encrypts every credential n8n stores: SMTP, Slack, Open
Collective, everything workflow nodes reference by name. **If this key is lost,
every stored credential is unrecoverable**, and each one has to be re-entered by
hand. Store it in the vault the moment `bootstrap.sh` generates it, and never
regenerate it against an existing database, because a different key does not
decrypt what is already there.

Use a vault shared with at least one other admin. A key in one person's
password manager makes recovering this instance depend on that person. Store
`POSTGRES_PASSWORD` as a separate entry, not a field on the same item. The
Postgres password is rotated by resetting the role, while the encryption key
must never change, so routine rotation never touches the entry that must stay
fixed, and the database password can be shared without the key that decrypts
every credential.

To read the values back off the box:

```bash
ssh debian@{VPS_IP} 'grep -E "N8N_ENCRYPTION_KEY|POSTGRES_PASSWORD" ~/community/automation/infra/.env'
```

Run that in your own terminal. Do not paste either value into a chat, an
issue, a commit or an agent transcript, because anything that records it
becomes another copy to protect.

## Backup

A dump is the whole database written out as one compressed SQL file,
`n8n-{STAMP}.sql.gz` in `~/backups/`. Restoring means feeding that file back to
Postgres.

n8n's own tables (workflows, credentials, execution history) and the
`ose_applications` data table live in the same Postgres database. So one
`pg_dump` of `n8n` is the entire backup, covering applicant stage, AI verdicts,
form answers and decisions.

The backup of record is this repo's own: `backup.sh` and the systemd units in
[`systemd/`](systemd/). `bootstrap.sh` installs the units, and the deploy
copies a unit again whenever it changes in git. The service runs the script out
of the checkout, so a pull alone updates it.

OVH also snapshots the VPS daily, around 14:37 UTC, and keeps one restore
point (Manager → the VPS → Automated backup). Treat it as a safety net, not the
backup of record:

- One restore point means yesterday only, with no history.
- It sits with the same provider as the VPS, so it does not survive losing the
  OVH account or region.
- It restores only onto OVH. A dump is a portable file.
- It is crash-consistent: restoring it is like the machine losing power at
  14:37. Postgres normally recovers from that by replaying its write-ahead log.
- It contains the whole disk, `.env` and its secrets included.

[`backup.sh`](backup.sh) writes the dump and refuses to leave a useless file
behind: it fails if the dump is under 10 KiB, fails the gzip integrity check, or
contains no `CREATE TABLE`. It keeps 14 days of dumps on the box. To take one by
hand:

```bash
~/community/automation/infra/backup.sh
```

`ose-backup.timer` runs it at 03:17 UTC daily. Debian 13 minimal ships no cron
package, and a timer is the better fit anyway: `Persistent=true` runs a backup
that was missed while the box was down, which cron silently skips.

The following commands show when the timer fires next, start one backup now,
and print what the last runs did:

```bash
systemctl list-timers ose-backup.timer
sudo systemctl start ose-backup.service
journalctl -u ose-backup.service -n 20
```

The script also appends every run to `~/backups/backup.log`, which keeps the
history longer than the journal does. A failed backup posts to Slack, as in
[Alerts when the box fails](#alerts-when-the-box-fails).

Dumps contain applicant personal data. `ose_applications` holds applicant
email addresses and the free text of their form answers, so every dump carries
both. That is why the copy off the box is encrypted, and why any other copy has
to be.

### The copy off the box

A backup that exists only on the machine it protects does not survive that
machine. After the checks above pass, `backup.sh` encrypts the dump with `age`
and uploads it to a Scaleway Object Storage bucket in Warsaw (`pl-waw`). That is
a different provider and a different country from the VPS, so one incident
cannot take both. The local dump is written first and kept either way, so a
failed upload costs the off-box copy and nothing else. It fails the run, which
posts an alert.

The dump is encrypted before it leaves the box, so the bucket holds nothing
readable. The private key exists only in the password vault, as
`OSE automation — backup decryption key`.

The upload key can create objects and cannot delete them. Writing over an
existing name would be a delete by another route, so the bucket keeps every
version. A lifecycle rule deletes dumps after 90 days, and replaced versions 90
days after that. The bucket, its project, the rule and the key's permissions
are in [`tofu/backup-storage/`](tofu/backup-storage/).

To set it up, follow the steps in order:

1. Create the bucket with the steps in
   [`tofu/backup-storage/README.md`](tofu/backup-storage/README.md).
2. On your own machine, generate the key pair. On macOS, install `age` first
   with `brew install age`.

   ```bash
   age-keygen -o ~/ose-backup.key
   chmod 600 ~/ose-backup.key
   ```

   The command prints the public key, which starts with `age1` and is not a
   secret. Store the whole content of the file in the vault entry named above:
   the `# created`, the `# public key` and the `AGE-SECRET-KEY-1` lines. On
   macOS, `pbcopy < ~/ose-backup.key` copies it without printing it. Keep the
   file until step 6 has proved a restore.

3. In the Scaleway console, open **IAM**, **Applications**, choose
   `ose-n8n-backups-uploader`, and generate an API key. Choose
   `ose-n8n-backups` as its preferred Object Storage project. Scaleway runs an
   Object Storage request in the key's preferred project, so a key that names
   another project gets `AccessDenied` on this bucket.
4. On the box, create the settings file with mode 600 before anything goes in,
   then type the values in an editor:

   ```bash
   mkdir -p ~/.config/ose-backup
   touch ~/.config/ose-backup/backup.env && chmod 600 ~/.config/ose-backup/backup.env
   nano ~/.config/ose-backup/backup.env
   ```

   The file holds these four settings. `BACKUP_S3_REGION` is optional and
   defaults to `pl-waw`:

   ```bash
   BACKUP_S3_BUCKET=ose-n8n-backups
   BACKUP_S3_ACCESS_KEY={ACCESS_KEY}
   BACKUP_S3_SECRET_KEY={SECRET_KEY}
   BACKUP_AGE_RECIPIENT={AGE_PUBLIC_KEY}
   ```

5. Start one backup and read its last line:

   ```bash
   sudo systemctl start ose-backup.service
   journalctl -u ose-backup.service -n 3
   ```

   `ok uploaded n8n-{STAMP}.sql.gz.age to ose-n8n-backups` confirms the copy. The
   object also appears in the bucket in the Scaleway console. The console lists
   buckets per project, so switch to the `ose-n8n-backups` project to see it.

6. Prove the copy restores. Download that object from the console, then
   decrypt it on your own machine with the key from step 2. The pipe never
   writes the decrypted dump to disk:

   ```bash
   F=~/Downloads/n8n-{STAMP}.sql.gz.age
   age -d -i ~/ose-backup.key "$F" | gunzip | grep -c '^CREATE TABLE'
   rm -f "$F"
   ```

   The count must match the table count of the `ok` line on the box. Then
   delete `~/ose-backup.key`, because the vault holds it.

Without `~/.config/ose-backup/backup.env`, the script skips the upload with a
warning and the dumps stay only on the box.

### Take a verified copy by hand

Before a change to the box, or while the bucket is not set up, copy a fresh
dump to your own machine and prove it arrived whole. On the box, take the dump
and print its checksum:

```bash
~/community/automation/infra/backup.sh
F=$(ls -t ~/backups/n8n-*.sql.gz | head -1); echo "$F"; sha256sum "$F"
```

On your own machine, copy that file and compare. The two checksums must match
character for character, and the table count must match the `ok` line:

```bash
mkdir -p ~/ose-backups && chmod 700 ~/ose-backups
scp "debian@{VPS_HOST}:{DUMP_PATH}" ~/ose-backups/
shasum -a 256 ~/ose-backups/n8n-*.sql.gz
gunzip -c ~/ose-backups/n8n-*.sql.gz | grep -c '^CREATE TABLE'
```

The copy holds applicant addresses and form answers in the clear, so delete it
once the bucket holds an encrypted copy of the same day.

## Which backup to restore, when

The nightly dump copies the database only. The daily OVH snapshot is an image
of the whole machine. A data problem wants the dump, and a machine problem wants
the snapshot or a rebuild.

| What happened | Restore what | How |
|---|---|---|
| Bad data: an n8n upgrade broke things, workflows or rows were deleted or mangled, but the machine itself is fine | The most recent **dump** from before the damage | The Restore steps below, on this box |
| Only one thing is lost (a single workflow, a few rows) and everything else moved on since | The dump, **into a scratch database** | The rehearsal procedure below, then copy what you need out of `restoretest` instead of rolling the whole database back |
| The machine is broken (filesystem damage, botched OS change) but OVH is fine | The **OVH snapshot** (Manager → the VPS → Automated backup) | Restores the whole disk to ~14:37 UTC yesterday, `.env` included. Afterwards: check the stack (`docker compose ps`), and run `backup.sh` once so a fresh dump exists |
| The VPS is gone entirely: terminated, region lost, or an account problem | The latest **dump** from the bucket, plus this repo | New VPS → the "First install" steps → decrypt the dump → load it. Needs `N8N_ENCRYPTION_KEY` and the backup decryption key **from the vault**. This is the moment they exist for |

Two constraints that hold in every row:

- A dump only yields working credentials under the original
  `N8N_ENCRYPTION_KEY`. The machine-problem rows keep it in `.env` on the
  restored disk. The last row is why the vault copy matters.
- The last row works only once [the copy off the box](#the-copy-off-the-box)
  is set up. Without it, losing the box loses the dumps with it, and the OVH
  snapshot at the same provider is the only copy left.

## Restore

A dump from the bucket is encrypted. Download it in the Scaleway console, then
decrypt it with the key from the vault. Write the key to a file only for the
restore, and delete it afterwards:

```bash
age -d -i {KEY_FILE} -o n8n-{STAMP}.sql.gz n8n-{STAMP}.sql.gz.age
```

> [!CAUTION]
> The following commands drop the live `n8n` database, with every workflow,
> credential and application row in it, and replace it with the dump. Anything
> written after the dump was taken is lost. Run the
> [rehearsal](#rehearse-it-without-risking-the-live-database) on the same dump
> first, and confirm `N8N_ENCRYPTION_KEY` in `.env` is the key the dump was
> taken under.

Stop both timers first. A deploy that fires mid-restore runs
`docker compose up -d` and starts n8n against a half-loaded database:

```bash
sudo systemctl stop ose-deploy.timer ose-backup.timer
cd ~/community/automation/infra
docker compose stop n8n
docker compose exec -T postgres psql -U n8n -d postgres -c "DROP DATABASE n8n;"
docker compose exec -T postgres psql -U n8n -d postgres -c "CREATE DATABASE n8n OWNER n8n;"
gunzip -c n8n-{STAMP}.sql.gz | docker compose exec -T postgres psql -U n8n -d n8n
docker compose start n8n
docker compose logs -f n8n
```

When n8n is up and the workflows look right, start the timers again:

```bash
sudo systemctl start ose-deploy.timer ose-backup.timer
```

### What each secret means for a rebuild

The dump contains database objects, not roles: it has no `CREATE ROLE` or
`ALTER ROLE` statement. That gives the two secrets very different recovery
properties:

| Scenario | `POSTGRES_PASSWORD` | `N8N_ENCRYPTION_KEY` |
|---|---|---|
| Restart, reboot, `down` + `up -d`, n8n upgrade | unchanged, because the volumes persist | unchanged |
| `docker compose down -v`, box rebuilt, new VPS | gone with the filesystem, so **pick a new one freely** | gone, and it **must be the original or credentials are unreadable** |
| Deliberate rotation on a live box | see below | never rotate against an existing database |

Rotating `POSTGRES_PASSWORD` takes two steps. The compose variable only
initialises an empty data directory, so editing `.env` alone does not change the
role, and n8n stops being able to connect. Change the role first:

```bash
docker compose exec -T postgres psql -U n8n -d postgres -c "ALTER ROLE n8n PASSWORD 'newvalue';"
# then update .env to match, and recreate so it is re-read
docker compose up -d n8n
```

### Rehearse it without risking the live database

Restore into a scratch database instead of over the real one. It takes a minute
and proves the dump loads:

```bash
cd ~/community/automation/infra
LATEST=$(ls -t ~/backups/n8n-*.sql.gz | head -1)
docker compose exec -T postgres psql -U n8n -d postgres -qc "CREATE DATABASE restoretest OWNER n8n;"
gunzip -c "$LATEST" | docker compose exec -T postgres psql -U n8n -d restoretest -q 2>&1 | grep -iE '^ERROR' | sort -u
# the two table counts should match
for db in restoretest n8n; do
  echo -n "$db: "
  docker compose exec -T postgres psql -U n8n -d "$db" -tAc \
    "select count(*) from information_schema.tables where table_schema = current_schema()"
done
docker compose exec -T postgres psql -U n8n -d postgres -qc "DROP DATABASE restoretest;"
```

For a dump from the bucket, decrypt it first as above. A restore only produces
a working instance under the `N8N_ENCRYPTION_KEY` the dump was taken with.
Under any other key the credentials are in the database but unreadable.

## Upgrading n8n

The version this box runs is the `image:` line of
[`docker-compose.yml`](docker-compose.yml), pinned to an `x.y.z` release. That
line is also what `automation/test/n8n-import-check.sh` boots in CI, so a bump
changes the build the exports are checked against in the same commit.

> [!CAUTION]
> Merging a new tag upgrades production within ten minutes. The deploy timer
> sees the changed image, runs `backup.sh`, and recreates n8n on the new
> version. The first boot of a new tag runs n8n's database migrations. To undo
> them, you restore the dump from before the upgrade and lose everything written
> since. Merge a tag bump when someone can watch the first boot.

Change the tag in a pull request, the way every other change to this repository
goes. CI then imports all the exports into the new build, which is what catches
a node type or a `typeVersion` the new version dropped.

After the merge, the next deploy run backs up the database and stops if the
backup fails, then recreates n8n. Watch it happen:

```bash
journalctl -u ose-deploy.service -f
```

The run prints the running and the configured image, then the backup, then
`Recreated` for the n8n container. The first boot takes noticeably longer than
a normal start. Without the deploy timer, run the same thing by hand with
`~/community/automation/infra/deploy.sh`.

An upgrade can also move the path the form page asks for. Caddy serves
`/static/og_image.png` because that is the path n8n's form template requests,
and a new version that renames it brings the n8n card back without any error.
Read the tag after a bump, and update the Caddyfile if the path changed:

```bash
curl -s https://apply.opensourceeurope.org/ | grep og:image
```

The table in [Which backup to restore, when](#which-backup-to-restore-when)
treats a bad upgrade as a restore from the dump the deploy took before it.

### Knowing when an upgrade is due

Nobody has to remember to look. `ose-n8n-advisories.timer` runs
[`check-n8n-version.py`](check-n8n-version.py) on this box every morning. It
compares the n8n tag in `docker-compose.yml` with n8n's published security
advisories. When an advisory with a fix affects that version, the unit fails,
and `ose-alert@` posts the report to the alerts channel, the same way a failed
deploy or backup does: once, with the worst advisories and the stable release
to move to. It posts again as recovered once a merged upgrade clears it.

The check runs here rather than in GitHub Actions so that it reuses the alert
path and the Slack token the box already has, and no Slack token sits in a
GitHub secret. It runs outside n8n so that it still reports when n8n is the
thing in trouble. The deploy installs and enables the units from git like any
other.

To run the same check by hand, on the box or anywhere with a checkout:

```bash
python3 automation/infra/check-n8n-version.py
```

Upgrade to the newest release that n8n does not mark as a prerelease. n8n
tags its newest minor as a prerelease for about a week, and `2.44.x` next to a
stable `2.43.x` is that. The check names the right one.

How fast to act depends on the advisory:

| Advisory | Upgrade within |
|---|---|
| Critical, or high and reachable without logging in | a day |
| Any other high | a week |
| Medium or low | the next routine bump, at least monthly |

An advisory with no patched version, such as the one about the Execute
Command node, never fails the check. No upgrade fixes it. That
node is excluded by n8n's default `NODES_EXCLUDE`, so keep it excluded.

Also watch the source. On `github.com/n8n-io/n8n`, choose **Watch**,
**Custom**, **Security alerts**, so an advisory reaches you the day it is
published rather than the next morning.

### The other two images

Postgres and Caddy are pinned to exact versions too. A floating tag such as
`caddy:2-alpine` is never refreshed here, because the deploy only acts on a tag
that changed in git, so it would stay at whatever was pulled on install day.
Dependabot opens a pull request for each new version, configured in
`.github/dependabot.yml`. Merging it recreates that container within ten
minutes. A Postgres major version is excluded, because moving from 16 to 17
needs a dump and restore, not a new tag.

### Keeping the box patched

The box installs security updates daily, from Debian and from Docker's
repository. Debian's default covers only Debian, so without the second origin
the Docker engine, containerd and runc never update, and they are what keeps a
container inside its box. When an update needs a reboot, the box reboots at
04:30, after the backup, and every container starts again by itself.

The settings are the files in [`apt/`](apt/). The deploy installs them into
`/etc/apt/apt.conf.d/` whenever they change in git, and installs
`unattended-upgrades` first if it is missing, the same way it installs the
systemd units. `bootstrap.sh` installs them on a new box. Nobody configures
this by hand.

To see what it installed and whether a reboot is waiting:

```bash
sudo tail -n 30 /var/log/unattended-upgrades/unattended-upgrades.log
[ -f /var/run/reboot-required ] && cat /var/run/reboot-required.pkgs
```

## Alerts when a workflow fails

`ops — alert Slack when any apply workflow fails` posts to the reviewers'
channel whenever one of the six `apply` workflows stops on an unhandled error.
Without it a failure is silent. The execution turns red in a list nobody
watches, and the pipeline looks healthy from the outside while it does nothing.

The handler has three nodes. An Error Trigger receives the failure, a Code
node builds the message, and a Slack node posts it. The alert names the
workflow that failed, the node inside it that threw and the error text, then
links to both the failed execution and the editor.

Slack messages are internal and carry no applicant address, so the handler
ignores `DRY_RUN` and posts during a dry run as well. It posts to
`SLACK_CHANNEL`, the same channel the application threads use.

### How a workflow is wired to it

Each workflow names the handler in its own **Error Workflow** setting, which
the exports carry as `settings.errorWorkflow`. To read back what the instance
holds for one workflow, run:

```bash
curl -s -H "X-N8N-API-KEY: $N8N_API_KEY" \
  "$N8N_API_URL/api/v1/workflows/{WORKFLOW_ID}" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['settings'])"
```

A new workflow does not inherit the setting. Set it when you add one, or that
workflow fails silently.

The handler itself has no **Error Workflow**. If it pointed at itself, a Slack
outage would make it fail while handling its own failure.

The handler stays inactive and still works. n8n calls a workflow that starts
with an Error Trigger directly, so it needs no activation of its own. What it
does need is for the failing workflow to be active, because only an automatic
run reaches it.

### What it does not catch

The handler fires on an unhandled error. It stays quiet when a node returns a
wrong answer, and it stays quiet when a node handles its own error and
continues. A Code node that returns an empty list instead of throwing leaves
the run green and sends nothing.

It also stays quiet for a run you started by hand. Testing a workflow from the
editor never produces an alert, however it ends.

It cannot report this box going down. The handler runs inside n8n, so when the
box stops, the thing that reports failures stops with it. That case belongs to
[The external uptime monitor](#the-external-uptime-monitor) below.

An alert therefore means something broke. Silence does not mean everything
works.

## The external uptime monitor

[Phare](https://phare.io), an Estonian uptime service, sends a GET to
`https://apply.opensourceeurope.org` and posts to Slack when it stops
answering.

It covers the one failure the alert handler cannot. The handler runs inside
n8n, so when the box stops, it stops too.

The check and its Slack destination are configured in the Phare account, not
in this repository. Put the account credentials in the shared vault.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ssh root@…` refused, any credential | This image has no root key, and Debian sets `PermitRootLogin prohibit-password` | Log in as the non-root user from the vault |
| `Permission denied (publickey)` with the right key | The key is passphrase-protected and `ssh-agent` is empty, so `BatchMode` cannot sign | `ssh-add ~/.ssh/{KEY}`, then confirm with `ssh-add -l` |
| `banner line 0: Not allowed at this time`, connection reset | Source IP blocked by brute-force protection, usually from polling SSH in a loop | Stop connecting and wait 10 to 30 minutes |
| `REMOTE HOST IDENTIFICATION HAS CHANGED` | The box was reinstalled | `ssh-keygen -R {VPS_IP}`, and only when you know why it changed |
| Certificate not issued | DNS for the hostname does not resolve to this box, or 80/443 blocked | Check `dig`, `sudo ufw status` and `docker compose logs caddy` |
| Caddy 502 for a few seconds after start | n8n is still booting, and Caddy has no healthcheck to wait on | Wait. It clears itself |
| `apply.` returns 404 | No active workflow serves `/form/apply-ose` | Activate `apply 4 — application form` |
| n8n log mentions SQLite | The `DB_TYPE` block is not taking effect | Fix it before storing anything. Moving data out of SQLite later is painful |
| `.env` has the variable, `docker compose up -d n8n` says `Running`, `printenv` in the container prints nothing | The compose file on the box predates the variable, so compose never passes it. `docker compose config \| grep {VAR}` prints nothing | `git pull --ff-only` in `~/community`, then `docker compose up -d n8n` and expect `Recreated` |
| `deploy.sh` stops with `the checkout is on ...` | The box was left on a feature branch, so the deploy refuses to fast-forward it | `git -C ~/community checkout main`, then run the deploy again |
| `deploy.sh` stops with `the checkout has local changes` | A file in the checkout was edited on the box | Read `git -C ~/community status`, move the change into a pull request, then `git -C ~/community checkout -- {FILE}` |
| `deploy.sh` stops with `variable(s) missing from .env` | A merged compose file names a variable the box's `.env` lacks. Workflow deploys stop too until it is fixed | Add the variable to `.env`. The next run applies the change |
| `deploy.sh` stops with `empty or missing in .../.env: …` | A value the stack needs is empty, often right after `bootstrap.sh all`, which leaves `AI_API_KEY`, `SLACK_CHANNEL`, `SMTP_FROM` and `DRY_RUN_RECIPIENT` for you | Set each named variable in `.env` |
| `deploy.sh` stops with `the Caddyfile does not validate` | A merged Caddyfile has a syntax error | Fix it in a pull request. The running Caddy is untouched |
| `deploy.sh` stops with `the n8n API at … did not answer after the restart` | n8n took longer than two minutes to boot, or failed to start | Read `docker compose logs --tail=50 n8n`. The infra is already applied, so the next run only syncs the workflows |
| A backup prints `backup.log is not writable` | The old backup unit left a log owned by root | `sudo chown debian: ~/backups/backup.log`, or run `bootstrap.sh units`, which fixes it |
| Backup log ends with `upload ... failed` | The upload key, the bucket name or its preferred project is wrong in `backup.env` | Check the settings in `~/.config/ose-backup/backup.env`. `AccessDenied` means the key's preferred project is not `ose-n8n-backups` |
| The same workflow reports `updated` on every deploy run | It is being edited in the n8n UI, and each run puts the exported version back over the edit | Export the instance's version with `automation/scripts/export-workflows.py` and open a pull request for it |
| Disk filling | Execution history unpruned | Check `EXECUTIONS_DATA_PRUNE=true` and `EXECUTIONS_DATA_MAX_AGE` |
| A deploy or backup failed and no Slack alert arrived | `~/.slack-bot-token` is missing or readable by others, or the bot is not in `SLACK_CHANNEL` | Read `journalctl -u ose-alert@{UNIT}.service` |
| Credentials all broken after a restore | `N8N_ENCRYPTION_KEY` differs from the one in use when the dump was taken | Restore the original key. There is no recovery without it |
| Every `ovhcloud` command returns 403 | The consumer key in `~/.ovh.conf` was revoked or expired | `ovhcloud login` |
| Stray `.*.swp` in `automation/infra/`, or an editor process nobody remembers | A dropped `ssh -t` session left an editor open on `.env`. The swap file holds a copy of the secrets, and a stale editor that saves later overwrites a rotated key with the old one | `pgrep -a 'vim|nano'`, kill the orphan (editors do not save on a plain kill), delete the swap file, then check `.env` by length |
| Applicant emails rejected or spam-filed | Mail sent from this box, while the domain's SPF allows Proton only | Send through the relay in [The SMTP credential](#the-smtp-credential) |
| A workflow failed and no Slack alert arrived | The workflow has no **Error Workflow** set, or a node handled the error and let the run finish green | Read back `settings.errorWorkflow`. For a handled error, make the node throw instead |
| Workflow fails with `access to env vars denied` | The running container predates `N8N_BLOCK_ENV_ACCESS_IN_NODE: "false"` in `docker-compose.yml`, so n8n blocks the `$env` expressions the workflows are built on | `git pull` in `~/community`, then `docker compose up -d n8n` and expect `Recreated` |

## Notes

- `.env` stays out of git. `automation/.env.example` lists the application
  variables, and [the variables table](#variables-this-compose-file-needs)
  lists the infra ones.
- Caddy issues and renews the certificates for `N8N_HOST` and `APPLY_HOST`
  itself, which works once DNS for each name resolves to this box. Caddy sees
  the two names only because the `caddy` service passes them in its own
  `environment:` block. Compose's `${VAR}` interpolation of the YAML does not
  put a variable inside a container by itself.
