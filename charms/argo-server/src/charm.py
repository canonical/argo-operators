#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

from oci_image import OCIImageResource, OCIImageResourceError
from ops.charm import CharmBase
from ops.main import main
from ops.model import ActiveStatus, MaintenanceStatus, WaitingStatus


class CheckFailed(Exception):
    """Raise this exception if one of the checks in main fails."""

    def __init__(self, msg, status_type=None):
        super().__init__()

        self.msg = msg
        self.status_type = status_type
        self.status = status_type(msg)


class Operator(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)

        self.log = logging.getLogger(__name__)
        self.image = OCIImageResource(self, "oci-image")

        for event in [
            self.on.install,
            self.on.leader_elected,
            self.on.upgrade_charm,
            self.on.config_changed,
        ]:
            self.framework.observe(event, self.main)

    def main(self, event):
        try:
            self._check_leader()

            image_details = self._check_image_details()

        except CheckFailed as check_failed:
            self.model.unit.status = check_failed.status
            return

        self.model.unit.status = MaintenanceStatus("Setting pod spec")

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
                                    "resources": ["configmaps"],
                                    "verbs": ["get", "watch", "list"],
                                },
                                {
                                    "apiGroups": [""],
                                    "resources": ["secrets"],
                                    "verbs": ["get", "create"],
                                },
                                {
                                    "apiGroups": [""],
                                    "resources": ["pods", "pods/exec", "pods/log"],
                                    "verbs": ["get", "list", "watch", "delete"],
                                },
                                {
                                    "apiGroups": [""],
                                    "resources": ["events"],
                                    "verbs": ["watch", "create", "patch"],
                                },
                                {
                                    "apiGroups": [""],
                                    "resources": ["serviceaccounts"],
                                    "verbs": ["get", "list", "watch"],
                                },
                                {
                                    "apiGroups": ["argoproj.io"],
                                    "resources": [
                                        "eventsources",
                                        "sensors",
                                        "workflows",
                                        "workfloweventbindings",
                                        "workflowtemplates",
                                        "cronworkflows",
                                        "clusterworkflowtemplates",
                                    ],
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
                            ],
                        },
                    ],
                },
                "containers": [
                    {
                        "name": self.model.app.name,
                        "imageDetails": image_details,
                        "imagePullPolicy": "Always",
                        "args": ["server"],
                        "ports": [{"name": "web", "containerPort": self.model.config["port"]}],
                        "kubernetes": {
                            "readinessProbe": {
                                "httpGet": {
                                    "port": self.model.config["port"],
                                    "scheme": "HTTPS",
                                    "path": "/",
                                },
                                "initialDelaySeconds": 10,
                                "periodSeconds": 20,
                            },
                            "securityContext": {
                                "runAsNonRoot": True,
                                "capabilities": {"drop": ["ALL"]},
                            },
                        },
                        "volumeConfig": [
                            {
                                "name": "tmp",
                                "mountPath": "/tmp",
                                "emptyDir": {"medium": "Memory"},
                            }
                        ],
                    },
                ],
            }
        )

        self.model.unit.status = ActiveStatus()

    def _check_leader(self):
        if not self.unit.is_leader():
            # We can't do anything useful when not the leader, so do nothing.
            raise CheckFailed("Waiting for leadership", WaitingStatus)

    def _check_image_details(self):
        try:
            image_details = self.image.fetch()
        except OCIImageResourceError as e:
            raise CheckFailed(f"{e.status.message}", e.status_type)
        return image_details


if __name__ == "__main__":
    main(Operator)
