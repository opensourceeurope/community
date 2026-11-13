variable "ssh_public_key" {
  description = "Public key installed for the debian user. The units in automation/infra/systemd run as debian, so this is the only login."
  type        = string
}

variable "project_id" {
  description = "Scaleway project the box is billed to. Null uses the default project of your scw profile."
  type        = string
  default     = null
}

variable "zone" {
  description = "Scaleway zone of the box."
  type        = string
  default     = "pl-waw-1"
}

variable "instance_type" {
  description = "Scaleway instance type. DEV1-S has 2 GB of RAM, which the stack fits in with the swap file bootstrap.sh adds."
  type        = string
  default     = "DEV1-S"
}

variable "disk_gb" {
  description = "Size of the local SSD root volume in GB. DEV1-S allows at most 20, and a local volume cannot grow later."
  type        = number
  default     = 20
}

variable "name" {
  description = "Name of the instance and the prefix of its security group, shown in the Scaleway console."
  type        = string
  default     = "ose-n8n-automation"
}

variable "dns_zone" {
  description = "Scaleway DNS zone the two hostnames live in, for example example.org. Null creates no records, for a zone hosted elsewhere."
  type        = string
  default     = null
}

variable "n8n_subdomain" {
  description = "Admin hostname inside dns_zone: the n8n editor and the Open Collective webhook. Avoid naming it after n8n, since every certificate is published in Certificate Transparency logs."
  type        = string
  default     = "automation"
}

variable "apply_subdomain" {
  description = "Applicant hostname inside dns_zone, whose bare URL serves the application form."
  type        = string
  default     = "apply"
}

variable "ipv6_address" {
  description = "The box's IPv6 address for the AAAA records, read on the box after the first apply with: ip -6 addr show scope global. Null creates no AAAA records."
  type        = string
  default     = null
}

variable "run_bootstrap" {
  description = "Run bootstrap.sh all on the new box over SSH, for a fresh install. Leave false for a move, where .env must be copied in before n8n first starts."
  type        = bool
  default     = false
}

variable "repo_url" {
  description = "Repository the box clones and deploys from."
  type        = string
  default     = "https://github.com/opensourceeurope/community.git"
}

variable "branch" {
  description = "Branch the box deploys."
  type        = string
  default     = "main"
}
