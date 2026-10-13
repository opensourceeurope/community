terraform {
  required_version = ">= 1.8"

  required_providers {
    ovh = {
      source  = "ovh/ovh"
      version = "~> 2.22"
    }
  }
}

# Credentials come from ~/.ovh.conf or the OVH_* environment variables, the
# same place the ovhcloud CLI reads them, never from a variable in this module.
provider "ovh" {}
