data "ovh_me" "account" {}

locals {
  n8n_host   = "${var.n8n_subdomain}.${var.dns_zone}"
  apply_host = "${var.apply_subdomain}.${var.dns_zone}"
}

# Ordering bills the account's default payment method, and destroying this
# resource terminates the VPS and everything on its disk.
resource "ovh_vps" "box" {
  display_name   = var.display_name
  ovh_subsidiary = data.ovh_me.account.ovh_subsidiary
  image_id       = var.image_id
  public_ssh_key = var.ssh_public_key

  # The key is the only way in. No password is set or emailed.
  do_not_send_password = true

  plan = [{
    duration     = "P1M"
    plan_code    = var.plan_code
    pricing_mode = "default"
    configuration = [
      { label = "vps_datacenter", value = var.datacenter },
      { label = "vps_os", value = var.os },
    ]
  }]
}

# The resource exports no address, so it is read back by service name.
data "ovh_vps" "box" {
  service_name = ovh_vps.box.id
}

locals {
  ipv4 = one([
    for ip in data.ovh_vps.box.ips : split("/", ip)[0]
    if can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+(/32)?$", ip))
  ])
}

resource "ovh_domain_zone_record" "n8n" {
  zone      = var.dns_zone
  subdomain = var.n8n_subdomain
  fieldtype = "A"
  ttl       = 300
  target    = local.ipv4
}

resource "ovh_domain_zone_record" "apply" {
  zone      = var.dns_zone
  subdomain = var.apply_subdomain
  fieldtype = "A"
  ttl       = 300
  target    = local.ipv4
}

# bootstrap.sh clones the repository itself, so the script is uploaded from
# this checkout rather than run from a clone that does not exist yet.
resource "terraform_data" "bootstrap" {
  triggers_replace = [local.ipv4]

  depends_on = [ovh_domain_zone_record.n8n, ovh_domain_zone_record.apply]

  connection {
    type  = "ssh"
    host  = local.ipv4
    user  = "debian"
    agent = true
  }

  provisioner "file" {
    source      = "${path.module}/../../bootstrap.sh"
    destination = "/tmp/bootstrap.sh"
  }

  provisioner "remote-exec" {
    inline = [
      "bash /tmp/bootstrap.sh all --n8n-host '${local.n8n_host}' --apply-host '${local.apply_host}' --repo-url '${var.repo_url}' --branch '${var.branch}'",
    ]
  }
}
