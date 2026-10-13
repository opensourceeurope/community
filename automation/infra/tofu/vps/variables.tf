variable "dns_zone" {
  description = "DNS zone hosted at OVH that the two hostnames live in, for example example.org."
  type        = string
}

variable "n8n_subdomain" {
  description = "Admin hostname inside dns_zone: the n8n editor and the Open Collective webhook. Avoid naming it after n8n, since every certificate is published in Certificate Transparency logs."
  type        = string
}

variable "apply_subdomain" {
  description = "Applicant-facing hostname inside dns_zone, whose bare URL serves the application form."
  type        = string
}

variable "ssh_public_key" {
  description = "Public key installed for the debian user. Its private half must be loaded in your SSH agent, because the bootstrap connects with it."
  type        = string
}

variable "image_id" {
  description = "OVH image id of Debian 13. OVH requires it whenever a public key is installed at order time. See README.md for how to look it up."
  type        = string
}

variable "plan_code" {
  description = "OVH VPS plan code from the public catalog."
  type        = string
  default     = "vps-2027-model1"
}

variable "datacenter" {
  description = "vps_datacenter value from the public catalog."
  type        = string
  default     = "SBG"
}

variable "os" {
  description = "vps_os value from the public catalog. bootstrap.sh is written for Debian 13."
  type        = string
  default     = "Debian 13"
}

variable "display_name" {
  description = "Name shown for the VPS in the OVH control panel."
  type        = string
  default     = "n8n application automation"
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
