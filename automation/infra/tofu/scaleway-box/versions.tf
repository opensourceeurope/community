terraform {
  required_version = ">= 1.8"

  required_providers {
    scaleway = {
      source  = "scaleway/scaleway"
      version = "~> 2.86"
    }
  }
}

# Credentials, the organization and the default project come from
# ~/.config/scw/config.yaml or the SCW_* environment variables, never from a
# variable in this module.
provider "scaleway" {
  zone = var.zone
}
