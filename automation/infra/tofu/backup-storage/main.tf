resource "scaleway_account_project" "backups" {
  name        = var.project_name
  description = "Encrypted n8n database dumps, written by the VPS backup timer"
}

resource "scaleway_object_bucket" "backups" {
  name       = var.bucket_name
  project_id = scaleway_account_project.backups.id
  region     = var.region

  # The upload key may write but not delete, and a write over an existing name
  # is a delete by another route. Versioning keeps the version it replaced.
  versioning {
    enabled = true
  }

  lifecycle_rule {
    id                                     = "expire-dumps"
    enabled                                = true
    abort_incomplete_multipart_upload_days = 1

    expiration {
      days = var.retention_days
    }

    noncurrent_version_expiration {
      noncurrent_days = var.retention_days
    }
  }
}

# The identity the VPS uploads as. Its API key is created by hand in the
# Scaleway console, so the secret never lands in this module's state.
resource "scaleway_iam_application" "uploader" {
  name        = "${var.project_name}-uploader"
  description = "Uploads encrypted n8n dumps from the VPS. Write only, no delete"
}

resource "scaleway_iam_policy" "uploader" {
  name           = "${var.project_name}-write-only"
  description    = "Create objects in the backup project, nothing else"
  application_id = scaleway_iam_application.uploader.id

  rule {
    project_ids          = [scaleway_account_project.backups.id]
    permission_set_names = ["ObjectStorageObjectsWrite"]
  }
}
