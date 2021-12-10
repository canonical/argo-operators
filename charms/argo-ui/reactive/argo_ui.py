import os

from charmhelpers.core import hookenv
from charms import layer
from charms.reactive import (
    clear_flag,
    endpoint_from_name,
    hook,
    set_flag,
    when,
    when_any,
    when_not,
)


@hook("upgrade-charm")
def upgrade_charm():
    clear_flag("charm.started")


@when("charm.started")
def charm_ready():
    layer.status.active("")


@when_any("layer.docker-resource.oci-image.changed", "config.changed")
def update_image():
    clear_flag("charm.started")


@when("endpoint.service-mesh.joined")
def configure_mesh():
    endpoint_from_name("service-mesh").add_route(
        prefix="/argo/",
        rewrite="/",
        service=hookenv.service_name(),
        port=hookenv.config("port"),
    )


@when("layer.docker-resource.oci-image.available")
@when_not("charm.started")
def start_charm():
    if not hookenv.is_leader():
        hookenv.log("This unit is not a leader.")
        return False

    layer.status.maintenance("configuring container")

    image_info = layer.docker_resource.get_info("oci-image")
    port = hookenv.config("port")

    layer.caas_base.pod_spec_set(
        {
            "version": 2,
            "serviceAccount": {
                "global": True,
                "rules": [
                    {
                        "apiGroups": [""],
                        "resources": ["pods", "pods/exec", "pods/log"],
                        "verbs": ["get", "list", "watch"],
                    },
                    {"apiGroups": [""], "resources": ["secrets"], "verbs": ["get"]},
                    {
                        "apiGroups": ["argoproj.io"],
                        "resources": ["workflows", "workflows/finalizers"],
                        "verbs": ["get", "list", "watch"],
                    },
                ],
            },
            "containers": [
                {
                    "name": "argo-ui",
                    "imageDetails": {
                        "imagePath": image_info.registry_path,
                        "username": image_info.username,
                        "password": image_info.password,
                    },
                    "config": {
                        "ARGO_NAMESPACE": os.environ["JUJU_MODEL_NAME"],
                        "IN_CLUSTER": "true",
                        "BASE_HREF": "/argo/",
                    },
                    "ports": [{"name": "http-ui", "containerPort": port}],
                    "kubernetes": {
                        "readinessProbe": {"httpGet": {"path": "/", "port": port}}
                    },
                }
            ],
        }
    )

    layer.status.maintenance("creating container")
    set_flag("charm.started")
