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
