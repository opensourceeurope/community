# VPS: a new box from nothing

This OpenTofu module orders an OVH VPS, points two DNS records at it, and runs
[`bootstrap.sh`](../../bootstrap.sh) on it. It describes how to build a box
like the one that runs the OSE application automation. It is not applied to
that box, and no state file points at it.

What `tofu apply` does, in order:

1. Orders a VPS through the OVH order workflow, with Debian 13 and your public
   SSH key for the `debian` user. No password is set or emailed.
2. Reads the new VPS back to learn its IPv4 address. The VPS resource itself
   exports no address.
3. Creates an `A` record for each of the two hostnames in your OVH DNS zone.
4. Uploads `bootstrap.sh` over SSH and runs it with `all` and both hostnames.
   The bootstrap clones the repository, writes `.env` with secrets generated on
   the box, starts the stack and installs the timers. It ends by printing the
   steps that only a person can do.

## Before you start

You need:

- OpenTofu 1.8 or later.
- OVH API credentials in `~/.ovh.conf` or the `OVH_*` environment variables.
  The `ovhcloud` CLI reads the same file, and `ovhcloud login` renews it. The
  credentials must allow ordering, and the account needs a default payment
  method. OVH no longer accepts `BANK_ACCOUNT` for that.
- A DNS zone hosted at OVH for the two hostnames.
- The private half of `ssh_public_key` loaded in your SSH agent, because the
  bootstrap connects with it. Check with `ssh-add -l`.
- The OVH image id of Debian 13, as the next section describes.

### The image id

OVH requires an image id whenever it installs an SSH key at order time, and it
lists image ids per VPS. Any VPS on your account answers. The following
command, run against an existing VPS, prints the id of Debian 13:

```bash
ovhcloud vps image list {SERVICE_NAME}
```

Without a VPS to ask, use the API console at
`https://eu.api.ovh.com/console/` and call
`GET /vps/{serviceName}/images/available`, then
`GET /vps/{serviceName}/images/available/{id}` for the name of each.

## Settings

Write the values in a `terraform.tfvars` file next to the module. Git ignores
no part of it, so it must hold no secret, and none of these variables is one.
The following example builds a box like the OSE one under other names:

```hcl
dns_zone        = "example.org"
n8n_subdomain   = "automation"
apply_subdomain = "apply"
ssh_public_key  = "ssh-ed25519 AAAA... you@laptop"
image_id        = "{DEBIAN_13_IMAGE_ID}"
repo_url        = "https://github.com/{OWNER}/{REPOSITORY}.git"
```

| Variable | Default | What it is |
|---|---|---|
| `dns_zone` | none | OVH DNS zone the two hostnames live in |
| `n8n_subdomain` | none | Admin hostname: the n8n editor and the Open Collective webhook. Avoid naming it after n8n, because every certificate is published in Certificate Transparency logs |
| `apply_subdomain` | none | Applicant hostname, whose bare URL serves the form |
| `ssh_public_key` | none | Public key for the `debian` user |
| `image_id` | none | OVH image id of Debian 13 |
| `plan_code` | `vps-2027-model1` | OVH plan code. This one is VPS-1 2027, the size the OSE box runs |
| `datacenter` | `SBG` | OVH datacenter, from the public VPS catalog |
| `os` | `Debian 13` | Operating system. `bootstrap.sh` is written for Debian 13 |
| `display_name` | `n8n application automation` | Name shown in the OVH control panel |
| `repo_url` | the OSE repository | Repository the box clones and deploys from |
| `branch` | `main` | Branch the box deploys |

The OVH public catalog lists every valid plan code, datacenter and operating
system at `https://eu.api.ovh.com/v1/order/catalog/public/vps?ovhSubsidiary=FR`.

## Build it

The following commands download the provider and show what the apply would do:

```bash
cd automation/infra/tofu/vps
tofu init
tofu plan
```

> [!CAUTION]
> `tofu apply` places a real order. OVH bills the account's default payment
> method monthly from that moment. Read the plan before you confirm it.

The following command orders the box and bootstraps it:

```bash
tofu apply
```

The bootstrap output scrolls past at the end of the apply. It ends with the
manual steps, which are also in
[First install](../../README.md#first-install-from-a-fresh-vps).

The state file stays in this directory and git ignores it. Keep it while the
box exists. Without it, OpenTofu no longer knows the box belongs to this module.

## Take it down

> [!CAUTION]
> `tofu destroy` terminates the VPS through OVH's termination workflow, and
> everything on its disk goes with it: the database, `.env` and the
> `N8N_ENCRYPTION_KEY` in it, and the local backups. Confirm that the vault
> holds the encryption key and that a recent dump exists off the box before
> you run it. It also deletes both DNS records.

The following command removes the box and its records:

```bash
tofu destroy
```
