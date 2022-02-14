#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from pathlib import Path

from ops.charm import CharmBase
from ops.main import main
from ops.pebble import Layer
from ops.model import ActiveStatus, MaintenanceStatus, WaitingStatus, BlockedStatus
from lightkube import ApiError, Client, codecs
from lightkube.types import PatchType
from lightkube.resources.apps_v1 import StatefulSet

from charms.observability_libs.v0.kubernetes_service_patch import KubernetesServicePatch


class CheckFailed(Exception):
    """ Raise this exception if one of the checks in main fails. """

    def __init__(self, msg, status_type=None):
        super().__init__()

        self.msg = msg
        self.status_type = status_type
        self.status = status_type(msg)


class ArgoServerOperatorCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)

        self.log = logging.getLogger(__name__)

        self._name = self.model.app.name
        self._namespace = self.model.name
        self._container = self.unit.get_container(self._name)
        self._src_dir = Path(__file__).parent
        self._context = {"namespace": self._namespace, "app_name": self._name}
        self._resource_files = {
            "auth": "auth_manifests.yaml"
        }
        # Creates a service to expose argo-server dashboard port
        self._service_patcher = KubernetesServicePatch(self, [("web", self.config["port"], 2746)])

        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(
            self.on.argo_server_pebble_ready,
            self._argo_server_pebble_ready
        )

        # NOTE previous podspec had these events mapped to the podspec declaration
        # self.framework.observe(self.on.leader_elected, self._on_config_changed)
        # self.framework.observe(self.on.upgrade_charm, self._on_config_changed)

    def _argo_server_layer(self) -> Layer:
        """Returns a Pebble configuration layer for Argo Server"""
        layer_config = {
            "summary": "argo server layer",
            "description": "pebble config layer for argo server",
            "services": {
                "argo-server": {
                    "override": "replace",
                    "summary": "argo server dashboard",
                    "command": "argo server",
                    "startup": "enabled"
                }
            },
        }
        return Layer(layer_config)

    def _create_resource(self, resource_type: str, context: dict = None) -> None:
        """Creates Kubernetes resources"""
        client = Client()
        with open(Path(self._src_dir) / self._resource_files[resource_type]) as f:
            for obj in codecs.load_all_yaml(f, context=context):
                client.create(obj)

    def _patch_resource(self, resource_type: str, context: dict = None) -> None:
        """Patches Kubernetes resources"""
        client = Client()
        with open(Path(self._src_dir) / self._resource_files[resource_type]) as f:
            for obj in codecs.load_all_yaml(f, context=context):
                client.patch(
                    type(obj), obj.metadata.name, obj, patch_type=PatchType.MERGE
                )

    def _patch_security_context(self):
        """Patch the security context
        TODO Not working at the moment
        """
        client = Client()
        pod_spec = client.get(StatefulSet, name=self._name, namespace=self._namespace)

        # NOTE This is how the runAsNonRoot is specified, but clashes with other containers
        # on the pod like the init-container
        pod_spec.spec.template.spec.securityContext.runAsNonRoot = True

        # NOTE Applying the same parameter to the argo-server container also generates an error
        # pod_spec.spec.template.spec.containers[1].securityContext.runAsNonRoot = True
        client.patch(StatefulSet, self._name, pod_spec, patch_type=PatchType.MERGE)

    def _update_layer(self) -> None:
        """Update Pebble layer if changed"""
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

    def _argo_server_pebble_ready(self, event):
        """Handle the pebble-ready event"""
        # Update Pebble configuration layer
        self._update_layer()

    def _on_install(self, _):
        """Handle the intall-event"""
        try:
            self._check_leader()
        except CheckFailed as check_failed:
            self.model.unit.status = check_failed.status
            return

        # Update Pebble configuration layer
        self._update_layer()

        # Create Kubernetes resources
        try:
            self.unit.status = MaintenanceStatus("Creating auth resources")
            self._create_resource(resource_type="auth", context=self._context)
            self.log.info("Created Kubernetes resources")
        except ApiError as e:
            self.log.error(e)
            self.unit.status = BlockedStatus(
                f"Creating resources failed with code {str(e.status.code)}."
            )
        else:
            self.unit.status = ActiveStatus()

    def _on_config_changed(self, event):
        """Handle the config-changed event"""
        try:
            self._check_leader()
        except CheckFailed as check_failed:
            self.model.unit.status = check_failed.status
            return

        # Update Pebble configuration layer
        self._update_layer()

        # Patch Kubernetes resources
        try:
            self.unit.status = MaintenanceStatus("Patching auth resources")
            self._patch_resource(resource_type="auth", context=self._context)
            self.log.info("Patched Kubernetes resources")

            # self._patch_security_context()
            # self.log.info("Patched Kubernetes securityContext")

        except ApiError as e:
            self.log.error(e)
            self.unit.status = BlockedStatus(
                f"Patching resources failed with code {str(e.status.code)}."
            )
        else:
            self.unit.status = ActiveStatus()

    def _check_leader(self):
        if not self.unit.is_leader():
            # We can't do anything useful when not the leader, so do nothing.
            raise CheckFailed("Waiting for leadership", WaitingStatus)

############ PREVIOUS PODSPEC CONFIG ############
#     try:
#         self._check_leader()
#         # image_details = self._check_image_details()

#     except CheckFailed as check_failed:
#         self.model.unit.status = check_failed.status
#         return

#     self.model.unit.status = MaintenanceStatus("Setting pod spec")

#     self.model.pod.set_spec(
#         {
#             "version": 3,
#             "serviceAccount": {
#                 "roles": [
#                     {
#                         "global": True,
#                         "rules": [
#                             {
#                                 "apiGroups": [""],
#                                 "resources": ["configmaps"],
#                                 "verbs": ["get", "watch", "list"],
#                             },
#                             {
#                                 "apiGroups": [""],
#                                 "resources": ["secrets"],
#                                 "verbs": ["get", "create"],
#                             },
#                             {
#                                 "apiGroups": [""],
#                                 "resources": ["pods", "pods/exec", "pods/log"],
#                                 "verbs": ["get", "list", "watch", "delete"],
#                             },
#                             {
#                                 "apiGroups": [""],
#                                 "resources": ["events"],
#                                 "verbs": ["watch", "create", "patch"],
#                             },
#                             {
#                                 "apiGroups": [""],
#                                 "resources": ["serviceaccounts"],
#                                 "verbs": ["get", "list"],
#                             },
#                             {
#                                 "apiGroups": ["argoproj.io"],
#                                 "resources": [
#                                     "eventsources",
#                                     "sensors",
#                                     "workflows",
#                                     "workfloweventbindings",
#                                     "workflowtemplates",
#                                     "cronworkflows",
#                                     "clusterworkflowtemplates",
#                                 ],
#                                 "verbs": [
#                                     "create",
#                                     "get",
#                                     "list",
#                                     "watch",
#                                     "update",
#                                     "patch",
#                                     "delete",
#                                 ],
#                             },
#                         ],
#                     },
#                 ],
#             },
#             "containers": [
#                 {
#                     "name": self.model.app.name,
#                     "imageDetails": image_details,
#                     "imagePullPolicy": "Always",
#                     "args": ["server"],
#                     "ports": [
#                         {"name": "web", "containerPort": self.model.config["port"]}
#                     ],
#                     "kubernetes": {
#                         "readinessProbe": {
#                             "httpGet": {
#                                 "port": self.model.config["port"],
#                                 "scheme": "HTTPS",
#                                 "path": "/",
#                             },
#                             "initialDelaySeconds": 10,
#                             "periodSeconds": 20,
#                         },
#                         "securityContext": {
#                             "runAsNonRoot": True,
#                             "capabilities": {"drop": ["ALL"]},
#                         },
#                     },
#                     "volumeConfig": [
#                         {
#                             "name": "tmp",
#                             "mountPath": "/tmp",
#                             "emptyDir": {"medium": "Memory"},
#                         }
#                     ],
#                 },
#             ],
#         }
#     )
#################################################


if __name__ == "__main__":
    main(ArgoServerOperatorCharm)
