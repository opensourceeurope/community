output "ipv4" {
  description = "Address for the A records. Log in with: ssh debian@<ipv4>"
  value       = scaleway_instance_ip.v4.address
}

output "ipv6_prefix" {
  description = "The box's routed IPv6 prefix. Read the address for the AAAA records on the box with: ip -6 addr show scope global"
  value       = scaleway_instance_ip.v6.prefix
}

output "server_id" {
  description = "Scaleway id of the instance"
  value       = scaleway_instance_server.box.id
}

output "admin_url" {
  value = local.n8n_host == null ? null : "https://${local.n8n_host}"
}

output "form_url" {
  value = local.apply_host == null ? null : "https://${local.apply_host}"
}
