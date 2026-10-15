# Backup storage: the off-box bucket

This OpenTofu module creates where `backup.sh` sends its encrypted dumps. It
creates four things in an existing Scaleway Organization:

- A project, `ose-n8n-backups`, that holds only the bucket. Scaleway scopes an
  IAM policy to a project, not to a bucket, so a key limited to this project
  reaches no other bucket in the Organization.
- A bucket in `pl-waw` with versioning on, and a lifecycle rule. The rule
  deletes a dump after 90 days and a replaced version 90 days after that, and
  it drops an unfinished upload after one day.
- An IAM application, `ose-n8n-backups-uploader`, that the VPS uploads as.
- A policy that gives the application `ObjectStorageObjectsWrite` on the
  project and nothing else. It can create objects and cannot delete them.

The module creates no API key. You create the key in the console and type it
straight into the file on the box, so the secret never lands in the state file
and never reaches a terminal.

## Before you start

You need OpenTofu 1.8 or later and a Scaleway API key for your own account. The
provider reads the key from `~/.config/scw/config.yaml` or from the `SCW_*`
environment variables. A key that has expired fails the apply with
`denied authentication: API key is expired` and creates nothing.

Bucket names are global across Scaleway. To copy this setup, set `bucket_name`
and `project_name` to names of your own, in a `terraform.tfvars` file next to
the module or with `-var`.

## Create it

The following commands download the provider and show what the apply would
create:

```bash
cd automation/infra/tofu/backup-storage
tofu init
tofu plan
```

The plan must show four resources to add and nothing to change or destroy. The
following command creates them:

```bash
tofu apply
```

The state file stays in this directory and git ignores it. It holds no secret,
but keep it: it is what lets a later `tofu plan` compare the bucket with this
module. Keep it in your main checkout, not in a worktree, because removing the
worktree deletes it. A `tofu plan` that reports no changes proves a moved copy
still matches the bucket.

An expired key fails with `denied authentication: API key is expired`. The
`scw` CLI cannot renew it, because creating a key needs a working one. Generate
a new key in the console under **IAM**, **API keys**, then run `scw init` and
answer `Y` when it asks to override the `default` profile.

## Then

Follow [The copy off the box](../../README.md#the-copy-off-the-box) to create
the upload key, the age key pair and the settings file on the box.

## Variables

| Variable | Default | What it is |
|---|---|---|
| `project_name` | `ose-n8n-backups` | Scaleway project that holds only the bucket |
| `bucket_name` | `ose-n8n-backups` | Bucket for the encrypted dumps. Pick your own when you copy this |
| `region` | `pl-waw` | Region of the bucket. Pick one away from the VPS |
| `retention_days` | `90` | Days a dump is kept, and days a replaced version is kept after that |
