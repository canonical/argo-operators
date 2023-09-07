# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

from charmed_kubeflow_chisme.components.pebble_component import PebbleServiceComponent
from ops.pebble import Layer

logger = logging.getLogger(__name__)

ARGO_CONTROLLER_CONFIGMAP = "argo-workflow-controller-configmap"
EXECUTOR_IMAGE_CONFIG = "executor-image"
LIVENESS_PROBE_PORT = "6060"
METRICS_PORT = "9090"
LIVENESS_PROBE_PATH = "/healthz"
LIVENESS_PROBE_NAME = "argo-controller-up"


class ArgoControllerPebbleService(PebbleServiceComponent):
    def __init__(
        self,
        *args,
        **kwargs,
    ):
        """Pebble service container component in order to configure Pebble layer"""
        super().__init__(*args, **kwargs)
        self.environment = {
            "ARGO_NAMESPACE": self.model.name,
            "LEADER_ELECTION_IDENTITY": self.model.app.name,
        }

    def get_layer(self) -> Layer:
        """Defines and returns Pebble layer configuration

        This method is required for subclassing PebbleServiceContainer
        """
        logger.info("PebbleServiceComponent.get_layer executing")
        return Layer(
            {
                "summary": "argo-controller layer",
                "description": "Pebble config layer for argo-controller",
                "services": {
                    self.service_name: {
                        "override": "replace",
                        "summary": "Entry point for kfp-viewer image",
                        "command": (
                            "workflow-controller "
                            "--configmap "
                            f"{ARGO_CONTROLLER_CONFIGMAP} "
                            "--executor-image "
                            "{self.model.config[EXECUTOR_IMAGE_CONFIG_NAME]} "
                            "--namespaced"
                        ),
                        "startup": "enabled",
                        "environment": self.environment,
                        "on-check-failure": {LIVENESS_PROBE_NAME: "restart"},
                    }
                },
                "checks": {
                    LIVENESS_PROBE_NAME: {
                        "override": "replace",
                        "period": "30s",
                        "timeout": "20s",
                        "threshold": 3,
                        "http": {
                            "url": f"http://localhost:{LIVENESS_PROBE_PORT}{LIVENESS_PROBE_PATH}"
                        },
                    }
                },
            }
        )
