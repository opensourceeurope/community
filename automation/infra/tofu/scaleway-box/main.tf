# Every resource here is billed by the hour from the moment it exists, the
# address and the disk included, until it is deleted.

resource "scaleway_instance_ip" "v4" {
  project_id = var.project_id
  type       = "routed_ipv4"
}

resource "scaleway_instance_ip" "v6" {
  project_id = var.project_id
  type       = "routed_ipv6"
}

locals {
  open_ports = [22, 80, 443]
  ip_ranges  = ["0.0.0.0/0", "::/0"]
}

# ufw on the box allows the same ports. This drops everything else before it
# reaches the box, so a container that publishes a port by mistake stays
# unreachable.
resource "scaleway_instance_security_group" "box" {
  project_id              = var.project_id
  name                    = "${var.name}-web-and-ssh"
  stateful                = true
  inbound_default_policy  = "drop"
  outbound_default_policy = "accept"

  # Scaleway blocks outbound SMTP (25, 465, 587) unless this is off, and n8n
  # sends every applicant email through the relay on 587. Scaleway also keeps
  # the ports blocked for an account it has not cleared to send mail, so check
  # by effect, as the move runbook says.
  enable_default_security = false

  dynamic "inbound_rule" {
    for_each = setproduct(local.open_ports, local.ip_ranges)
    content {
      action   = "accept"
      protocol = "TCP"
      port     = inbound_rule.value[0]
      ip_range = inbound_rule.value[1]
    }
  }

  # Path MTU discovery, and IPv6 neighbour discovery, need ICMP.
  dynamic "inbound_rule" {
    for_each = local.ip_ranges
    content {
      action   = "accept"
      protocol = "ICMP"
      ip_range = inbound_rule.value
    }
  }
}

resource "scaleway_instance_server" "box" {
  project_id        = var.project_id
  name              = var.name
  type              = var.instance_type
  image             = "debian_trixie"
  ip_ids            = [scaleway_instance_ip.v4.id, scaleway_instance_ip.v6.id]
  security_group_id = scaleway_instance_security_group.box.id

  # Scaleway refuses to delete a protected instance, so neither a stray
  # `tofu destroy` nor a console click takes the box and its disk with it.
  protected = true

  root_volume {
    volume_type           = "l_ssd"
    size_in_gb            = var.disk_gb
    delete_on_termination = true
  }

  # Scaleway's Debian image logs in as root. The units run as debian from
  # /home/debian/community, so cloud-init creates that user.
  user_data = {
    cloud-init = templatefile("${path.module}/cloud-init.yaml.tftpl", {
      ssh_public_key = var.ssh_public_key
    })
  }

  tags = ["ose", "n8n"]
}

locals {
  n8n_host   = var.dns_zone == null ? null : "${var.n8n_subdomain}.${var.dns_zone}"
  apply_host = var.dns_zone == null ? null : "${var.apply_subdomain}.${var.dns_zone}"
  subdomains = var.dns_zone == null ? toset([]) : toset([var.n8n_subdomain, var.apply_subdomain])
}

resource "scaleway_domain_record" "a" {
  for_each = local.subdomains
  dns_zone = var.dns_zone
  name     = each.value
  type     = "A"
  data     = scaleway_instance_ip.v4.address
  ttl      = 300
}

# The box takes its IPv6 address from the routed prefix by itself, so the
# address is known only once it runs. Read it there and set ipv6_address.
resource "scaleway_domain_record" "aaaa" {
  for_each = var.ipv6_address == null ? toset([]) : local.subdomains
  dns_zone = var.dns_zone
  name     = each.value
  type     = "AAAA"
  data     = var.ipv6_address
  ttl      = 300
}

# A fresh install only. bootstrap.sh clones the repository itself, so the
# script is uploaded from this checkout rather than run from a clone that does
# not exist yet. Caddy gets its certificates once the A records resolve.
resource "terraform_data" "bootstrap" {
  count            = var.run_bootstrap ? 1 : 0
  triggers_replace = [scaleway_instance_server.box.id]

  depends_on = [scaleway_domain_record.a]

  lifecycle {
    precondition {
      condition     = var.dns_zone != null
      error_message = "run_bootstrap needs dns_zone, because bootstrap.sh writes both hostnames into .env."
    }
  }

  connection {
    type  = "ssh"
    host  = scaleway_instance_ip.v4.address
    user  = "debian"
    agent = true
  }

  provisioner "remote-exec" {
    # Exit code 2 means cloud-init finished with a warning, which is fine.
    inline = ["cloud-init status --wait >/dev/null || [ $? -eq 2 ]"]
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
