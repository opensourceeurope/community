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
  # /home/debian/community, so cloud-init creates that user the way OVH's
  # image does.
  user_data = {
    cloud-init = templatefile("${path.module}/cloud-init.yaml.tftpl", {
      ssh_public_key = var.ssh_public_key
    })
  }

  tags = ["ose", "n8n"]
}
