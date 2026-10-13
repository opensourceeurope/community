output "bucket" {
  description = "BACKUP_S3_BUCKET for ~/.config/ose-backup/backup.env on the box"
  value       = scaleway_object_bucket.backups.name
}

output "region" {
  description = "BACKUP_S3_REGION for ~/.config/ose-backup/backup.env on the box"
  value       = var.region
}

output "project_id" {
  description = "The project to choose when creating the upload key in the console"
  value       = scaleway_account_project.backups.id
}

output "uploader_application" {
  description = "Create the upload key for this IAM application in the Scaleway console"
  value       = scaleway_iam_application.uploader.name
}
