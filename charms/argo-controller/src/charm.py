#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from base64 import b64encode
from glob import glob
from pathlib import Path

import yaml
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from ops.charm import CharmBase
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus

from oci_image import OCIImageResource, OCIImageResourceError
from serialized_data_interface import (
    NoCompatibleVersions,
    NoVersionsListed,
    get_interfaces,
)


class CheckFailed(Exception):
    """ Raise this exception if one of the checks in main fails. """

    def __init__(self, msg: str, status_type=None):
        super().__init__()

        self.msg = str(msg)
        self.status_type = status_type
        self.status = status_type(self.msg)


class ArgoControllerCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)

        self.log = logging.getLogger(__name__)

        self.image = OCIImageResource(self, "oci-image")

        self.prometheus_provider = MetricsEndpointProvider(
            charm=self,
            relation_name="monitoring",
            jobs=[
                {
                    "job_name": "argo_controller_metrics",
                    "scrape_interval": self.config["metrics-scrape-interval"],
                    "metrics_path": self.config["metrics-api"],
                    "static_configs": [{"targets": ["*:{}".format(self.config["http-port"])]}],
                }
            ],
        )

        self.dashboard_provider = GrafanaDashboardProvider(self)

        for event in [
            self.on.install,
            self.on.leader_elected,
            self.on.upgrade_charm,
            self.on.config_changed,
            self.on["object-storage"].relation_changed,
            self.on["monitoring"].relation_changed,
            self.on["monitoring"].relation_broken,
            self.on["monitoring"].relation_departed,
        ]:
            self.framework.observe(event, self.main)

    def main(self, event):
        try:
            self._check_leader()

            interfaces = self._get_interfaces()

            image_details = self._check_image_details()

            os = self._check_object_storage(interfaces)

        except CheckFailed as check_failed:
            self.model.unit.status = check_failed.status
            return

        self.model.unit.status = MaintenanceStatus("Setting pod spec")

        # Sync the argoproj/argoexec image to the same version
        metadata = yaml.safe_load(Path("metadata.yaml").read_bytes())
        version = metadata["resources"]["oci-image"]["upstream-source"].split(":")[-1]
        executor_image = f"argoproj/argoexec:{version}"
        self.log.info(f"using executorImage {executor_image}")

        config_map = {
            "containerRuntimeExecutor": self.model.config["executor"],
            "kubeletInsecure": self.model.config["kubelet-insecure"],
            "artifactRepository": {
                "s3": {
                    "bucket": self.model.config["bucket"],
                    "keyFormat": self.model.config["key-format"],
                    "endpoint": f"{os['service']}.{os['namespace']}:{os['port']}",
                    "insecure": not os["secure"],
                    "accessKeySecret": {
                        "name": "mlpipeline-minio-artifact",
                        "key": "accesskey",
                    },
                    "secretKeySecret": {
                        "name": "mlpipeline-minio-artifact",
                        "key": "secretkey",
                    },
                }
            },
        }

        crd_root = "files/crds"
        crds = [yaml.safe_load(Path(f).read_text()) for f in glob(f"{crd_root}/*.yaml")]
        self.model.pod.set_spec(
            {
                "version": 3,
                "serviceAccount": {
                    "roles": [
                        {
                            "global": True,
                            "rules": [
                                {
                                    "apiGroups": [""],
                                    "resources": ["pods", "pods/exec"],
                                    "verbs": [
                                        "create",
                                        "get",
                                        "list",
                                        "watch",
                                        "update",
                                        "patch",
                                        "delete",
                                    ],
                                },
                                {
                                    "apiGroups": [""],
                                    "resources": ["configmaps"],
                                    "verbs": ["get", "watch", "list"],
                                },
                                {
                                    "apiGroups": [""],
                                    "resources": ["persistentvolumeclaims"],
                                    "verbs": ["create", "delete", "get"],
                                },
                                {
                                    "apiGroups": ["argoproj.io"],
                                    "resources": ["workflows", "workflows/finalizers"],
                                    "verbs": [
                                        "get",
                                        "list",
                                        "watch",
                                        "update",
                                        "patch",
                                        "delete",
                                        "create",
                                    ],
                                },
                                {
                                    "apiGroups": ["argoproj.io"],
                                    "resources": [
                                        "workflowtemplates",
                                        "workflowtemplates/finalizers",
                                        "clusterworkflowtemplates",
                                        "clusterworkflowtemplates/finalizers",
                                    ],
                                    "verbs": [
                                        "get",
                                        "list",
                                        "watch",
                                    ],
                                },
                                {
                                    "apiGroups": [""],
                                    "resources": ["serviceaccounts"],
                                    "verbs": ["get", "list"],
                                },
                                {
                                    "apiGroups": ["argoproj.io"],
                                    "resources": [
                                        "cronworkflows",
                                        "cronworkflows/finalizers",
                                    ],
                                    "verbs": [
                                        "get",
                                        "list",
                                        "watch",
                                        "update",
                                        "patch",
                                        "delete",
                                    ],
                                },
                                {
                                    "apiGroups": [""],
                                    "resources": ["events"],
                                    "verbs": ["create", "patch"],
                                },
                                {
                                    "apiGroups": ["policy"],
                                    "resources": ["poddisruptionbudgets"],
                                    "verbs": ["create", "get", "delete"],
                                },
                                {
                                    "apiGroups": ["coordination.k8s.io"],
                                    "resources": ["leases"],
                                    "verbs": ["create", "get", "update"],
                                },
                                {
                                    "apiGroups": [""],
                                    "resources": ["secrets"],
                                    "verbs": ["get"],
                                },
                            ],
                        }
                    ],
                },
                "service": {
                    "updateStrategy": {
                        "type": "RollingUpdate",
                        "rollingUpdate": {"maxUnavailable": 1},
                    },
                },
                "containers": [
                    {
                        "name": self.model.app.name,
                        "imageDetails": image_details,
                        "imagePullPolicy": "Always",
                        "args": [
                            "--configmap",
                            "argo-controller-configmap-config",
                            "--executor-image",
                            executor_image,
                        ],
                        "envConfig": {
                            "ARGO_NAMESPACE": self.model.name,
                            "LEADER_ELECTION_IDENTITY": self.model.app.name,
                        },
                        "volumeConfig": [
                            {
                                "name": "configmap",
                                "mountPath": "/config-map.yaml",
                                "files": [
                                    {
                                        "path": "config",
                                        "content": yaml.dump(config_map),
                                    }
                                ],
                            }
                        ],
                    },
                ],
                "kubernetesResources": {
                    "customResourceDefinitions": [
                        {"name": crd["metadata"]["name"], "spec": crd["spec"]}
                        for crd in crds
                    ],
                    "secrets": [
                        {
                            "name": "mlpipeline-minio-artifact",
                            "type": "Opaque",
                            "data": {
                                "accesskey": b64encode(
                                    os["access-key"].encode("utf-8")
                                ),
                                "secretkey": b64encode(
                                    os["secret-key"].encode("utf-8")
                                ),
                            },
                        }
                    ],
                },
            }
        )

        self.model.unit.status = ActiveStatus()

    def _check_leader(self):
        if not self.unit.is_leader():
            # We can't do anything useful when not the leader, so do nothing.
            raise CheckFailed("Waiting for leadership", WaitingStatus)

    def _get_interfaces(self):
        try:
            interfaces = get_interfaces(self)
        except NoVersionsListed as err:
            raise CheckFailed(err, WaitingStatus)
        except NoCompatibleVersions as err:
            raise CheckFailed(err, BlockedStatus)
        return interfaces

    def _check_object_storage(self, interfaces):
        if not ((os := interfaces["object-storage"]) and os.get_data()):
            raise CheckFailed("Waiting for object-storage relation data", BlockedStatus)

        return list(os.get_data().values())[0]

    def _check_image_details(self):
        try:
            image_details = self.image.fetch()
        except OCIImageResourceError as e:
            raise CheckFailed(f"{e.status.message}", e.status_type)
        return image_details


if __name__ == "__main__":
    main(ArgoControllerCharm)
