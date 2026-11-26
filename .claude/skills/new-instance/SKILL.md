---
name: new-instance
description: Use when someone wants their own copy of this n8n application automation (Postgres, n8n, and Caddy on a Scaleway instance), or wants to rebuild the box from nothing. It walks the operator through the setup in order, with the cost, secret, and access gates this repository requires.
---

# A new instance of the n8n application automation

The whole setup lives in this repository: the instance in
`automation/infra/tofu/scaleway-box/`, the backup bucket in
`automation/infra/tofu/backup-storage/`, the machine in
`automation/infra/bootstrap.sh`, the stack in
`automation/infra/docker-compose.yml`, and the workflows in `automation/n8n/`.
`automation/infra/README.md` is the runbook. This skill gives the order and
the gates, and the runbook gives the commands. Read AGENTS.md first.

## Decide which case it is

| Case | Path |
|---|---|
| A copy of this setup under someone else's names, with no data | "Fresh install" in this skill |
| The OSE box is lost and a dump exists | "Fresh install" with `run_bootstrap` off, then the last row of "Which backup to restore, when" in the runbook |

## Gates that hold in every case

Stop at each of the following gates:

- Before any `tofu apply`, name each resource the plan creates and its price
  per hour and per month. Get the operator's yes to exactly that. Read prices
  from Scaleway's public catalog,
  `https://api.scaleway.com/product-catalog/v2alpha1/public-catalog/products?zone=pl-waw-1&page_size=500`.
  Prices there are per hour, and the monthly price is the hourly price times
  730. The `scw` CLI has no monthly price, no IP price, and no volume price. A
  choice of direction is not a yes.
- Have the operator confirm, before the apply, that the Scaleway Organization
  has passed identity verification. Scaleway blocks outbound SMTP until then,
  and n8n sends every email through the relay on port 587. Without the
  verification, the apply stops at the security group after the addresses
  already bill.
- The operator runs every command on the box. Never log in with `ssh` or
  `scp` yourself. Write one-shot `ssh debian@{HOST} '...'` commands for the
  operator's laptop, never a block to paste into an open session and never one
  ending in `exit`. Say what output to expect, and wait for it.
- Secrets never pass through you. The operator types each one into the
  file on the machine that reads it. Check it by its size or by effect, never by printing it.
- Never run two n8n instances on the same data. Both would run every sweep and send
  every email twice. When the lost OSE box is rebuilt from a dump, the old
  one must be gone first.
- Nothing is activated without asking, and `DRY_RUN` stays `true` until the
  operator decides to go live. Then follow "Going live" in the runbook first,
  because rows handled in a dry run never mailed their applicant.
- Applicant data stays in the operator's terminal. Queries you write for the
  operator print counts, stages, and slugs. When they need addresses, ask them not
  to paste the output back.

## Fresh install

0. Check every tool before anything else. The tools are those in "Tools on
   your machine" in the runbook, plus Node.js and `jq` from "What you need on
   your machine" in `automation/README.md`. Run `tofu version`, `scw version`,
   `age --version`, `docker info`, `ssh-add -l`, `gh auth status`,
   `shellcheck --version`, `git --version`, `python3 --version`,
   `node --version`, and `jq --version`. Name each one that fails with its
   install command from those tables, and stop until the operator has
   installed it. A tool found missing later costs a half-built
   box that already bills.
1. Set up the accounts in the table in "Accounts you need first" in the
   runbook:
   - Scaleway in a verified Organization
   - Open Collective host admin
   - A Slack app
   - An SMTP relay
   - A shared password vault
   - An uptime service

   The workflows
   are written for the OSE host on Open Collective, so another host means
   changing the host slug in the workflows that read it.
2. Create the backup bucket with `tofu/backup-storage/`, after the
   operator's yes to its price. Bucket names are global, so pick new ones.
3. Build the box with `tofu/scaleway-box/`, setting `dns_zone`,
   `run_bootstrap = true`, and the operator's own `repo_url`. The `repo_url`
   must be a public repository, because the box clones and fetches it with no
   credentials. Show the plan, name the prices, and wait for the yes. Then the operator runs `tofu apply` in their
   own terminal with their SSH key loaded, because the bootstrap logs into the
   box. The default is a `DEV1-S`, sized from measured use. Larger is fine but
   not needed. For a DNS zone outside Scaleway, leave `dns_zone` and
   `run_bootstrap` unset, create the records by hand from the `ipv4` output,
   and have the operator run `bootstrap.sh` as "Install the box" in the
   runbook describes.
4. Add the AAAA records. The operator reads the box's IPv6 address with
   `ip -6 addr show scope global`. Set `ipv6_address` and apply again. That
   adds only the records, which cost nothing.
5. Follow the numbered steps of "After the script" in the runbook, in that
   order. Its step 1 comes first: the encryption key goes into
   the vault before anything else. A copy needs its own editor gate password
   and hash, from step 2, or nobody reaches its editor.
   A copy also sets `ROOT_REDIRECT_HOST` in `.env` to its own bare domain and
   points the redirect in the Caddyfile at its own website. Otherwise its
   Caddy keeps asking for a certificate for opensourceeurope.org.
6. Verify the install as "Verify the install" in the runbook describes. It
   ends with the relay check on port 587, which must answer `open`.

## What a copy changes

The repository is written for OSE, and a copy keeps OSE's values until it
changes them. Infra files mark each one with a `COPYING:` comment, so
`git grep -n 'COPYING:'` lists them: the repository URL, instance and bucket
names, the bare domain's redirect, the applicant hostname, and the editor gate
hash. Go through each one with the operator.

Some places cannot carry a comment. Change them too, through the
`n8n-skills:n8n` skill for anything in `automation/n8n/`:

- The Open Collective host slug `europe`, in **Hosts to sync** and in
  `ORG_BY_HOST` in **Extract new applications**, in both apply 1a and apply 1b.
- The organization's name in the `ORG_NAMES` map and in fixed wording such as
  "OSE's admins", in the render nodes that build Slack messages, dialogs and
  emails. `grep -lE "ORG_NAMES|Open Source Europe|OSE's" automation/n8n/*.json`
  lists the workflows to walk.
- The emails in `automation/emails/` and the render nodes that embed them,
  changed together, which check 10 enforces. They name OSE and link its
  documentation.
- The form in `automation/n8n/form-ose.json` (apply 4): its wording, its
  wordmark, and the links on its pages.
- The model prompts in `automation/prompts/`, which describe what OSE hosts
  and when it suggests Open Collective Europe (OCE) instead.
- The link preview image, `automation/infra/public/static/og_image.png`.

## Done means

The install is done when all of the following are true:

- `https://{ADMIN_HOSTNAME}/healthz` answers `200`, and the applicant hostname
  serves the form once apply 4 is active.
- A manual `ose-backup.service` run ends with `ok uploaded`.
- `ose-alert@test.service` posts to Slack.
- The encryption key and the backup decryption key are in the vault.
