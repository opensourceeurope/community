---
name: new-instance
description: Use when someone wants their own copy of this n8n application automation (Postgres, n8n and Caddy on a Scaleway instance), wants to rebuild the box from nothing, or wants to move the running stack to a new box. Walks the operator through it in order, with the cost, secret and access gates this repository requires.
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
| A copy of this setup under someone else's names, with no data | "Fresh install" below |
| The OSE box is lost and a dump exists | "Fresh install" with `run_bootstrap` off, then the last row of "Which backup to restore, when" in the runbook |
| The running OSE stack moves to another machine | "Moving to a new box" in the runbook, nothing else |

## Gates that hold in every case

- **Money.** Before any `tofu apply`, name each resource the plan creates and
  its price per hour and per month, and get the operator's yes to exactly
  that. Read prices from Scaleway's public catalog,
  `https://api.scaleway.com/product-catalog/v2alpha1/public-catalog/products?zone=pl-waw-1&page_size=500`.
  Prices there are per hour, and monthly is hourly times 730. The `scw` CLI
  has no monthly price, no IP price and no volume price. A choice of direction
  is not a yes.
- **Verified Organization.** Scaleway blocks outbound SMTP until the
  Organization has passed identity verification, and n8n sends every email
  through the relay on port 587. Have the operator confirm it before the
  apply. Otherwise the apply stops at the security group after the addresses
  already bill.
- **The operator runs every command on the box.** Never log in with `ssh` or
  `scp` yourself. Write one-shot `ssh debian@{HOST} '...'` commands for the
  operator's laptop, never a block to paste into an open session and never one
  ending in `exit`. Say what output to expect, and wait for it.
- **Secrets never pass through you.** The operator types each one into the
  file on the machine that reads it, or copies it box to box with `scp -3`.
  Check a copy by its size or by effect, never by printing it.
- **Never two n8n on the same data.** Both would run every sweep and send
  every email twice. A rehearsal restores into a scratch database only.
- **Nothing is activated without asking**, and `DRY_RUN` stays `true` until
  the operator decides to go live.

## Fresh install

1. **Accounts.** The table in "Accounts you need first" in the runbook:
   Scaleway in a verified Organization, Open Collective host admin, a Slack app,
   an SMTP relay, a shared password vault, an uptime service. The workflows
   are written for the OSE host on Open Collective, so another host means
   changing the host slug in the workflows that read it.
2. **Backup bucket.** `tofu/backup-storage/`, with the operator's yes to its
   price. Bucket names are global, so pick new ones.
3. **The box.** `tofu/scaleway-box/` with `dns_zone`, `run_bootstrap = true`
   and the operator's own `repo_url` if they forked. Show the plan, name the
   prices and wait for the yes. Then the operator runs `tofu apply` in their
   own terminal with their SSH key loaded, because the bootstrap logs into the
   box. The default is a `DEV1-S`, sized from measured use. Larger is fine but
   not needed. For a DNS zone outside Scaleway, leave `dns_zone` and
   `run_bootstrap` unset, create the records by hand from the `ipv4` output,
   and have the operator run `bootstrap.sh` as "Install the box" in the
   runbook describes.
4. **AAAA records.** The operator reads the box's IPv6 address with
   `ip -6 addr show scope global`. Set `ipv6_address` and apply again. That
   adds only the records, which cost nothing.
5. **After the script.** The numbered steps of "After the script" in the
   runbook, in that order. Its step 1 comes first: the encryption key goes into
   the vault before anything else. A copy needs its own editor gate password
   and hash, from step 2, or nobody reaches its editor.
6. **Verify.** "Verify the install" in the runbook. Then the checks of
   "Check it" under "Moving to a new box". Email out on port 587 must answer
   `open`.

## Done means

- `https://{ADMIN_HOSTNAME}/healthz` answers `200`, and the applicant hostname
  serves the form once apply 4 is active.
- A manual `ose-backup.service` run ends with `ok uploaded`.
- `ose-alert@test.service` posts to Slack.
- The encryption key and the backup decryption key are in the vault.
