# Scaleway box: an instance for the stack

This OpenTofu module creates a Scaleway instance that
[`bootstrap.sh`](../../bootstrap.sh) can turn into the box running the OSE
application automation. It creates:

- A `DEV1-S` instance (2 vCPU, 2 GB) in `pl-waw-1`, on Debian 13, with a 20 GB
  local SSD.
- One routed IPv4 address and one routed IPv6 prefix.
- A security group that drops every inbound connection but SSH, HTTP, HTTPS
  and ICMP, over IPv4 and IPv6. It lets outbound SMTP through, which Scaleway
  blocks by default, because n8n sends mail through the relay on port 587.

Cloud-init creates the `debian` user with passwordless sudo and your SSH key,
because Scaleway's image logs in as `root` and the units in
[`systemd/`](../../systemd/) run as `debian`. The stack fits into 2 GB because
`bootstrap.sh` adds a swap file for n8n's short peaks.

It has two uses:

- **A fresh install**, a copy of this setup under your own names. With
  `dns_zone` set it also creates the `A` records in your Scaleway DNS zone,
  and with `run_bootstrap = true` it runs `bootstrap.sh all` over SSH. That
  leaves a running stack on an empty database, and the steps in
  [After the script](../../README.md#after-the-script) to finish.
- **A move** of an existing stack. Leave `run_bootstrap` off, because `.env`
  must be copied in before n8n first starts, as
  [Moving to a new box](../../README.md#moving-to-a-new-box) describes.

## Before you start

You need:

- OpenTofu 1.8 or later.
- A Scaleway Organization that has passed identity verification. Scaleway
  blocks outbound SMTP for an unverified one, and the apply then stops at the
  security group, after the addresses already exist.
- A Scaleway API key in `~/.config/scw/config.yaml` or the `SCW_*` environment
  variables. Its default project is the one billed, unless you set
  `project_id`. `scw config get default-project-id` prints it.
- The private half of `ssh_public_key` loaded in your SSH agent. Check with
  `ssh-add -l`.

## Settings

Write the values in a `terraform.tfvars` file next to the module. Git does not
ignore it, so it must hold no secret, and none of these variables is one.

A fresh install under your own names:

```hcl
ssh_public_key = "ssh-ed25519 AAAA... you@laptop"
dns_zone       = "example.org"
run_bootstrap  = true
repo_url       = "https://github.com/{OWNER}/{REPOSITORY}.git"
```

The DNS zone must be hosted in Scaleway Domains and DNS. For a zone elsewhere,
leave `dns_zone` unset, create the records by hand from the `ipv4` output, and
run `bootstrap.sh` yourself as
[Install the box](../../README.md#install-the-box) describes.

| Variable | Default | What it is |
|---|---|---|
| `ssh_public_key` | none | Public key for the `debian` user |
| `project_id` | the default project of your `scw` profile | Scaleway project the box is billed to |
| `zone` | `pl-waw-1` | Scaleway zone, next to the backup bucket in `pl-waw` |
| `instance_type` | `DEV1-S` | Instance type |
| `disk_gb` | `20` | Local SSD size in GB. `DEV1-S` allows at most 20, and a local volume cannot grow later |
| `name` | `ose-n8n-automation` | Name shown in the Scaleway console |
| `dns_zone` | none | Scaleway DNS zone of the two hostnames. Unset creates no records |
| `n8n_subdomain` | `automation` | Admin hostname: the n8n editor and the Open Collective webhook. Avoid naming it after n8n, because every certificate is published in Certificate Transparency logs |
| `apply_subdomain` | `apply` | Applicant hostname, whose bare URL serves the form |
| `ipv6_address` | none | The box's IPv6 address for the `AAAA` records. It is known only once the box runs, so set it after the first apply and apply again |
| `run_bootstrap` | `false` | Run `bootstrap.sh all` over SSH after creating the box. Needs `dns_zone` |
| `repo_url` | the OSE repository | Repository the box clones and deploys from |
| `branch` | `main` | Branch the box deploys |

Scaleway lets only a verified Organization (identity verification, KYC)
unblock SMTP. Until then the apply stops at the security group with `your
account is not trusted, you cannot disable default security rules`, after the
addresses already exist. Verification is per Organization, so an older
account of yours elsewhere does not count. Verify the Organization that owns
the project first.

## Create it

The following commands download the provider and show what the apply would
create:

```bash
cd automation/infra/tofu/scaleway-box
tofu init
tofu plan
```

> [!CAUTION]
> `tofu apply` creates billed resources. Scaleway charges the instance, the
> disk and the IPv4 address by the hour until they are deleted. Read the plan
> before you confirm it.

The plan must add the IPv4 address, the IPv6 prefix, the security group and
the instance, plus the records and the bootstrap when you set them, and change
or destroy nothing. The following command creates them. Run it yourself, in your own terminal,
with your SSH key loaded (`ssh-add -l` lists it). With `run_bootstrap = true`
it logs into the new box over SSH, which an agent never does:

```bash
tofu apply
```

The bootstrap uploads `bootstrap.sh` from your checkout, and the box then
clones `repo_url` at `branch`. Apply from a checkout of that same branch, so
both are the same version.

`ipv4` is the address for the `A` records. `ipv6_prefix` is the routed prefix.
Read the address for the `AAAA` records on the box:

```bash
ip -6 addr show scope global
```

Cloud-init needs a minute or two after the apply. Then log in with
`ssh debian@{NEW_IP}`.

The state file stays in this directory and git ignores it. Keep it outside
any worktree, because removing the worktree deletes it, and keep a copy, for
example in `~/.local/state/`. Without it OpenTofu no longer knows the box
belongs to this module.

## Take it down

The instance is protected, so Scaleway refuses to delete it and
`tofu destroy` fails. To take it down on purpose, set `protected = false` in
`main.tf` and apply that first.

> [!CAUTION]
> Deleting the instance deletes its disk: the database, `.env` with
> `N8N_ENCRYPTION_KEY`, and the local backups. Confirm that the vault holds the
> encryption key and that a recent dump exists off the box first.
