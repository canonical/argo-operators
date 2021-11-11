#!/usr/bin/env python3

import logging
from base64 import b64encode
from glob import glob
from pathlib import Path

import yaml
from ops.charm import CharmBase
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus

from oci_image import OCIImageResource, OCIImageResourceError
from serialized_data_interface import (
    NoCompatibleVersions,
    NoVersionsListed,
    get_interfaces,
)


class ArgoControllerCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        if not self.unit.is_leader():
            # We can't do anything useful when not the leader, so do nothing.
            self.model.unit.status = WaitingStatus("Waiting for leadership")
            return
        self.log = logging.getLogger(__name__)

        try:
            self.interfaces = get_interfaces(self)
        except NoVersionsListed as err:
            self.model.unit.status = WaitingStatus(str(err))
            return
        except NoCompatibleVersions as err:
            self.model.unit.status = BlockedStatus(str(err))
            return

        self.image = OCIImageResource(self, "oci-image")
        for event in [
            self.on.install,
            self.on.leader_elected,
            self.on.upgrade_charm,
            self.on.config_changed,
            self.on["object-storage"].relation_changed,
        ]:
            self.framework.observe(event, self.main)

    def main(self, event):
        try:
            image_details = self.image.fetch()
        except OCIImageResourceError as e:
            self.model.unit.status = e.status
            self.log.info(e)
            return

        self.model.unit.status = MaintenanceStatus("Setting pod spec")

        self.log.info("RELATIONS: {}".format(self.model.relations["object-storage"]))

        if not ((os := self.interfaces["object-storage"]) and os.get_data()):
            self.model.unit.status = BlockedStatus(
                "Waiting for object-storage relation data"
            )
            return
        os = list(os.get_data().values())[0]

        # Sync the argoproj/argoexec image to the same version
        version = image_details["imagePath"].split(":")[-1]
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


if __name__ == "__main__":
    main(ArgoControllerCharm)
