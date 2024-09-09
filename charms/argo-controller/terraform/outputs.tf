output "app_name" {
  value = juju_application.argo_controller.name
}

output "provides" {
  value = [
    "metrics-endpoint",
    "grafana-dashboard"
  ]
}

output "requires" {
  value = [
    "object-storage",
    "logging"
  ]
}
