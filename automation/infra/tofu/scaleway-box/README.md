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

The module does not run `bootstrap.sh` and does not touch DNS. A move needs
`.env` copied in before the first start, as
[Moving to a new box](../../README.md#moving-to-a-new-box) describes, and the
DNS zone is managed outside this repository.

## Before you start

You need:

- OpenTofu 1.8 or later.
- A Scaleway API key in `~/.config/scw/config.yaml` or the `SCW_*` environment
  variables. Its default project is the one billed, unless you set
  `project_id`. `scw config get default-project-id` prints it.
- The private half of `ssh_public_key` loaded in your SSH agent. Check with
  `ssh-add -l`.

## Settings

Write the values in a `terraform.tfvars` file next to the module. Git does not
ignore it, so it must hold no secret, and none of these variables is one.

```hcl
ssh_public_key = "ssh-ed25519 AAAA... you@laptop"
```

| Variable | Default | What it is |
|---|---|---|
| `ssh_public_key` | none | Public key for the `debian` user |
| `project_id` | the default project of your `scw` profile | Scaleway project the box is billed to |
| `zone` | `pl-waw-1` | Scaleway zone, next to the backup bucket in `pl-waw` |
| `instance_type` | `DEV1-S` | Instance type |
| `disk_gb` | `20` | Local SSD size in GB. `DEV1-S` allows at most 20, and a local volume cannot grow later |
| `name` | `ose-n8n-automation` | Name shown in the Scaleway console |

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
the instance, and change or destroy nothing. The following command creates them:

```bash
tofu apply
```

`ipv4` is the address for the `A` records. `ipv6_prefix` is the routed prefix.
Read the address for the `AAAA` records on the box:

```bash
ip -6 addr show scope global
```

Cloud-init needs a minute or two after the apply. Then log in with
`ssh debian@{NEW_IP}`.

The state file stays in this directory and git ignores it. Keep it in your
main checkout, not in a worktree, because removing the worktree deletes it.

## Take it down

The instance is protected, so Scaleway refuses to delete it and
`tofu destroy` fails. To take it down on purpose, set `protected = false` in
`main.tf` and apply that first.

> [!CAUTION]
> Deleting the instance deletes its disk: the database, `.env` with
> `N8N_ENCRYPTION_KEY`, and the local backups. Confirm that the vault holds the
> encryption key and that a recent dump exists off the box first.
