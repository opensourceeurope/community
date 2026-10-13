output "ipv4" {
  description = "Address of the new box. Log in with: ssh debian@<ipv4>"
  value       = local.ipv4
}

output "service_name" {
  description = "OVH service name of the VPS"
  value       = ovh_vps.box.id
}

output "admin_url" {
  value = "https://${local.n8n_host}"
}

output "form_url" {
  value = "https://${local.apply_host}"
}

output "next" {
  description = "The steps no tool can do for you"
  value       = "bootstrap.sh printed the remaining manual steps at the end of the apply output. They are also in automation/infra/README.md, under 'First install'."
}
