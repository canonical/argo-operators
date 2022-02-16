#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""Argo Server charm for Kubernetes."""

import logging
from pathlib import Path

from charms.observability_libs.v0.kubernetes_service_patch import KubernetesServicePatch
from lightkube import ApiError, Client, codecs
from lightkube.resources.rbac_authorization_v1 import ClusterRole, ClusterRoleBinding
from lightkube.types import PatchType
from ops.charm import CharmBase
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus
from ops.pebble import Layer


class ArgoServerOperatorCharm(CharmBase):
    """Argo Server charm."""

    def __init__(self, *args):
        super().__init__(*args)

        self.log = logging.getLogger(__name__)

        self._name = self.model.app.name
        self._namespace = self.model.name
        self._container = self.unit.get_container(self._name)
        self._src_dir = Path(__file__).parent
        self._context = {"namespace": self._namespace, "app_name": self._name}
        self._resource_files = {"auth": "auth_manifests.yaml"}

        # Creates a service to expose argo-server dashboard port
        self._service_patcher = KubernetesServicePatch(self, [("web", self.config["port"], 2746)])

        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.leader_elected, self._on_config_changed)
        self.framework.observe(self.on.upgrade_charm, self._on_config_changed)
        self.framework.observe(self.on.argo_server_pebble_ready, self._argo_server_pebble_ready)
        self.framework.observe(self.on.remove, self._on_remove)

    def _on_install(self, _):
        """Handle the intall-event."""
        if not self.unit.is_leader():
            self.unit.status = WaitingStatus("Waiting for leadership")
            return

        # Update Pebble configuration layer
        self._update_layer()

        # Create Kubernetes resources
        try:
            self.unit.status = MaintenanceStatus("Creating auth resources")
            self._create_resource(resource_type="auth", context=self._context)
            self.log.info("Created Kubernetes resources")
        except ApiError as e:
            self.log.error("On install, creating resources failed: {}".format(e))
            self.unit.status = BlockedStatus(
                f"Creating resources failed with code {str(e.status.code)}."
            )
        else:
            self.unit.status = ActiveStatus()

    def _on_config_changed(self, _):
        """Handle the config-changed event."""
        if not self.unit.is_leader():
            self.unit.status = WaitingStatus("Waiting for leadership")
            return

        # Update Pebble configuration layer
        self._update_layer()

        # Patch Kubernetes resources
        try:
            self.unit.status = MaintenanceStatus("Patching auth resources")
            self._patch_resource(resource_type="auth", context=self._context)
            self.log.info("Patched Kubernetes resources")

        except ApiError as e:
            self.log.error(e)
            self.unit.status = BlockedStatus(
                f"Patching resources failed with code {str(e.status.code)}."
            )
        else:
            self.unit.status = ActiveStatus()

    def _argo_server_pebble_ready(self, event):
        """Handle the pebble-ready event."""
        # Update Pebble configuration layer
        self._update_layer()

    def _on_remove(self, _):
        """Handle the remove event."""
        try:
            self.unit.status = MaintenanceStatus("Deleting auth resources")
            # Destroy the created resources
            self._delete_resources()
        except ApiError as e:
            self.log.error("On remove, deleting resources failed: {}".format(e))
            self.unit.status = BlockedStatus(
                f"Deleting resources failed with code {str(e.status.code)}."
            )
        else:
            self.unit.status = ActiveStatus()

    def _update_layer(self) -> None:
        """Update Pebble layer if changed."""
        if not self._container.can_connect():
            self.unit.status = WaitingStatus("Waiting for Pebble in workload container")
            return

        # Get current config
        current_layer = self._container.get_plan()
        # Create the config layer
        new_layer = self._argo_server_layer()

        # Update the Pebble configuration layer
        if current_layer.services != new_layer.services:
            self._container.add_layer("argo-server", new_layer, combine=True)
            self.log.info("Added updated layer 'argo-server' to Pebble plan")
            self._container.restart("argo-server")
            self.log.info("Restarted argo-server service")

        self.unit.status = ActiveStatus()

    def _argo_server_layer(self) -> Layer:
        """Return a Pebble configuration layer for Argo Server."""
        layer_config = {
            "summary": "argo server layer",
            "description": "pebble config layer for argo server",
            "services": {
                "argo-server": {
                    "override": "replace",
                    "summary": "argo server dashboard",
                    "command": "argo server --auth-mode {}".format(self.config["auth-mode"]),
                    "startup": "enabled",
                }
            },
        }
        return Layer(layer_config)

    def _create_resource(self, resource_type: str, context: dict = None) -> None:
        """Create Kubernetes resources."""
        client = Client()
        with open(Path(self._src_dir) / self._resource_files[resource_type]) as f:
            for obj in codecs.load_all_yaml(f, context=context):
                client.create(obj)

    def _patch_resource(self, resource_type: str, context: dict = None) -> None:
        """Patch Kubernetes resources."""
        client = Client()
        with open(Path(self._src_dir) / self._resource_files[resource_type]) as f:
            for obj in codecs.load_all_yaml(f, context=context):
                client.patch(type(obj), obj.metadata.name, obj, patch_type=PatchType.MERGE)

    def _delete_resources(self) -> None:
        """Delete kubernetes resources."""
        client = Client()
        self.log.info("Deleting roles from model")
        client.delete(ClusterRoleBinding, name=f"{self._name}-binding", namespace=self._namespace)
        client.delete(ClusterRole, name=f"{self._name}-cluster-role", namespace=self._namespace)


if __name__ == "__main__":
    main(ArgoServerOperatorCharm)
