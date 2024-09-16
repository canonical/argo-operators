output "app_name" {
  value = juju_application.argo_controller.name
}

output "provides" {
  value = {
    metrics_endpoint  = "metrics-endpoint",
    grafana_dashboard = "grafana-dashboard"
  }
}

output "requires" {
  value = {
    object_storage = "object-storage",
    logging        = "logging"
  }
}
