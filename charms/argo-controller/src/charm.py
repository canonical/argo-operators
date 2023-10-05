#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm for the Argo Workflow Controller.

https://github.com/canonical/argo-operators
"""

import logging
from base64 import b64encode

import lightkube
from charmed_kubeflow_chisme.components import SdiRelationDataReceiverComponent
from charmed_kubeflow_chisme.components.charm_reconciler import CharmReconciler
from charmed_kubeflow_chisme.components.kubernetes_component import KubernetesComponent
from charmed_kubeflow_chisme.components.leadership_gate_component import LeadershipGateComponent
from charmed_kubeflow_chisme.kubernetes import create_charm_default_labels
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.observability_libs.v1.kubernetes_service_patch import KubernetesServicePatch
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from lightkube.models.core_v1 import ServicePort
from lightkube.resources.apiextensions_v1 import CustomResourceDefinition
from lightkube.resources.core_v1 import ConfigMap, Secret, ServiceAccount
from lightkube.resources.rbac_authorization_v1 import (
    ClusterRole,
    ClusterRoleBinding,
    Role,
    RoleBinding,
)
from ops.charm import CharmBase
from ops.main import main

from components.pebble_component import (
    ARGO_CONTROLLER_CONFIGMAP,
    METRICS_PORT,
    ArgoControllerPebbleService,
)

logger = logging.getLogger(__name__)

K8S_RESOURCE_FILES = [
    "src/templates/auth_manifests.yaml.j2",
    "src/templates/crds.yaml",
    "src/templates/minio_configmap.yaml.j2",
    "src/templates/mlpipeline_minio_artifact_secret.yaml.j2",
]
METRICS_PATH = "/metrics"


class ArgoControllerOperator(CharmBase):
    """Charm for the Argo Workflows controller.

    https://github.com/canonical/argo-operators
    """

    def __init__(self, *args):
        super().__init__(*args)

        # patch service ports
        metrics_port = ServicePort(int(METRICS_PORT), name="metrics-port")
        self.service_patcher = KubernetesServicePatch(
            self,
            [metrics_port],
            service_name=self.app.name,
        )

        self.prometheus_provider = MetricsEndpointProvider(
            charm=self,
            relation_name="metrics-endpoint",
            jobs=[
                {
                    "metrics_path": METRICS_PATH,
                    "static_configs": [{"targets": ["*:{}".format(METRICS_PORT)]}],
                }
            ],
        )

        # The provided dashboard template is based on https://grafana.com/grafana/dashboards/13927
        # by user M4t3o
        self.dashboard_provider = GrafanaDashboardProvider(self)

        self.charm_reconciler = CharmReconciler(self)

        self.leadership_gate = self.charm_reconciler.add(
            component=LeadershipGateComponent(
                charm=self,
                name="leadership-gate",
            ),
            depends_on=[],
        )

        self.object_storage_relation = self.charm_reconciler.add(
            component=SdiRelationDataReceiverComponent(
                charm=self,
                name="relation:object_storage",
                relation_name="object-storage",
            ),
            depends_on=[self.leadership_gate],
        )

        self.kubernetes_resources = self.charm_reconciler.add(
            component=KubernetesComponent(
                charm=self,
                name="kubernetes:auth-crds-cm-and-secrets",
                resource_templates=K8S_RESOURCE_FILES,
                krh_resource_types={
                    ClusterRole,
                    ClusterRoleBinding,
                    ConfigMap,
                    CustomResourceDefinition,
                    Role,
                    RoleBinding,
                    Secret,
                    ServiceAccount,
                },
                krh_labels=create_charm_default_labels(
                    self.app.name,
                    self.model.name,
                    scope="auth-crds-cm-and-secrets",
                ),
                context_callable=self._context_callable,
                lightkube_client=lightkube.Client(),
            ),
            depends_on=[
                self.leadership_gate,
                self.object_storage_relation,
            ],
        )

        self.argo_controller_container = self.charm_reconciler.add(
            component=ArgoControllerPebbleService(
                charm=self,
                name="container:argo-controller",
                container_name="argo-controller",
                service_name="argo-controller",
            ),
            depends_on=[
                self.leadership_gate,
                self.kubernetes_resources,
                self.object_storage_relation,
            ],
        )

        self.charm_reconciler.install_default_event_handlers()

    @property
    def _context_callable(self):
        return lambda: {
            "app_name": self.app.name,
            "namespace": self.model.name,
            "access_key": b64encode(
                self.object_storage_relation.component.get_data()["access-key"].encode("utf-8")
            ).decode("utf-8"),
            "secret_key": b64encode(
                self.object_storage_relation.component.get_data()["secret-key"].encode("utf-8")
            ).decode("utf-8"),
            "mlpipeline_minio_artifact_secret": "mlpipeline-minio-artifact",
            "argo_controller_configmap": ARGO_CONTROLLER_CONFIGMAP,
            "s3_bucket": self.model.config["bucket"],
            "s3_minio_endpoint": (
                f"{self.object_storage_relation.component.get_data()['service']}."
                f"{self.object_storage_relation.component.get_data()['namespace']}:"
                f"{self.object_storage_relation.component.get_data()['port']}"
            ),
            "kubelet_insecure": self.model.config["kubelet-insecure"],
            "runtime_executor": self.model.config["executor"],
        }


if __name__ == "__main__":
    main(ArgoControllerOperator)
