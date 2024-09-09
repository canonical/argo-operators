resource "juju_application" "argo_controller" {
  name      = var.app_name
  model     = var.model_name
  trust     = true
  resources = var.resources
  charm {
    name     = "argo-controller"
    channel  = var.channel
    revision = var.revision
  }

  units  = 1
  config = var.config
}
