variable "project_name" {
  description = "Scaleway project that holds only the backup bucket. IAM policies are scoped to a project, so the upload key reaches nothing else."
  type        = string
  default     = "ose-n8n-backups"
}

variable "bucket_name" {
  description = "Bucket for the encrypted dumps. Bucket names are global across Scaleway, so a copy of this setup has to pick its own."
  type        = string
  default     = "ose-n8n-backups"
}

variable "region" {
  description = "Scaleway region of the bucket. Pick one away from the VPS, so one incident cannot take both."
  type        = string
  default     = "pl-waw"
}

variable "retention_days" {
  description = "Days a dump is kept in the bucket, and days an overwritten version is kept after that."
  type        = number
  default     = 90
}
