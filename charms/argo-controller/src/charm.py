#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm for the Argo Workflow Controller.

https://github.com/canonical/argo-operators
"""

import logging
from base64 import b64encode
from urllib.parse import urlparse

import lightkube
from charmed_kubeflow_chisme.components import (
    RelationCountGateComponent,
    S3RequirerComponent,
    SdiRelationDataReceiverComponent,
)
from charmed_kubeflow_chisme.components.charm_reconciler import CharmReconciler
from charmed_kubeflow_chisme.components.kubernetes_component import KubernetesComponent
from charmed_kubeflow_chisme.components.leadership_gate_component import LeadershipGateComponent
from charmed_kubeflow_chisme.kubernetes import create_charm_default_labels
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v1.loki_push_api import LogForwarder
from charms.observability_libs.v1.kubernetes_service_patch import KubernetesServicePatch
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from lightkube.models.core_v1 import ServicePort
from lightkube.resources.apiextensions_v1 import CustomResourceDefinition
from lightkube.resources.core_v1 import ConfigMap, Secret
from ops import main
from ops.charm import CharmBase

from components.pebble_component import (
    ARGO_CONTROLLER_CONFIGMAP,
    ARGO_KEYFORMAT,
    METRICS_PORT,
    ArgoControllerPebbleService,
)

logger = logging.getLogger(__name__)

K8S_RESOURCE_FILES = [
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

        self.s3_relations_conflict_detector = self.charm_reconciler.add(
            component=RelationCountGateComponent(
                charm=self,
                name="s3-relations-conflict-detector",
                relation_names=["object-storage", "s3-credentials"],
            ),
            depends_on=[self.leadership_gate],
        )

        self.s3_relation = self.charm_reconciler.add(
            component=S3RequirerComponent(
                charm=self,
                name="relation:s3_credentials",
                relation_name="s3-credentials",
                is_optional=True,
                required_relation_fields=frozenset({"access-key", "secret-key", "endpoint"}),
            ),
            depends_on=[self.leadership_gate, self.s3_relations_conflict_detector],
        )

        self.object_storage_relation = self.charm_reconciler.add(
            component=SdiRelationDataReceiverComponent(
                charm=self,
                name="relation:object_storage",
                relation_name="object-storage",
                # Make this relation optional, since a relation with s3-credentials is
                # also sufficient
                minimum_related_applications=0,
            ),
            depends_on=[self.leadership_gate, self.s3_relations_conflict_detector],
        )

        self.kubernetes_resources = self.charm_reconciler.add(
            component=KubernetesComponent(
                charm=self,
                name="kubernetes:crds-cm-and-secrets",
                resource_templates=K8S_RESOURCE_FILES,
                krh_resource_types={
                    ConfigMap,
                    CustomResourceDefinition,
                    Secret,
                },
                krh_labels=create_charm_default_labels(
                    self.app.name,
                    self.model.name,
                    scope="crds-cm-and-secrets",
                ),
                context_callable=self._context_callable,
                lightkube_client=lightkube.Client(),
            ),
            depends_on=[
                self.leadership_gate,
                self.s3_relations_conflict_detector,
                self.object_storage_relation,
                self.s3_relation,
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
                self.s3_relations_conflict_detector,
            ],
        )

        self.charm_reconciler.install_default_event_handlers()
        self._logging = LogForwarder(charm=self)

    @property
    def active_storage_component(self):
        """Returns the active storage component (S3 or object storage)."""
        if self.model.get_relation("s3-credentials"):
            return self.s3_relation.component
        return self.object_storage_relation.component

    @property
    def _context_callable(self):
        def context():
            active = self.active_storage_component
            data = active.get_data()
            if isinstance(active, S3RequirerComponent):
                # get_data() returns a list, only one S3 relation is expected,
                # so take the first entry
                data = data[0]
                # Strip any URL scheme (e.g. "http://") since argo's S3 client expects
                # just the host[:port], not a full URL
                parsed = urlparse(data["endpoint"])
                endpoint = parsed.netloc if parsed.netloc else parsed.path
                s3_region = data.get("region")
            else:
                # When minimum_related_applications != maximum_related_applications,
                # SdiRelationDataReceiverComponent.get_data() returns a list of dicts
                # rather than a single dict. Extract the first entry.
                if isinstance(data, list):
                    data = data[0]
                endpoint = f"{data['service']}.{data['namespace']}:{data['port']}"
                s3_region = None
            return {
                "app_name": self.app.name,
                "namespace": self.model.name,
                "access_key": b64encode(data["access-key"].encode("utf-8")).decode("utf-8"),
                "secret_key": b64encode(data["secret-key"].encode("utf-8")).decode("utf-8"),
                "mlpipeline_minio_artifact_secret": "mlpipeline-minio-artifact",
                "argo_controller_configmap": ARGO_CONTROLLER_CONFIGMAP,
                "s3_bucket": data.get("bucket", self.model.config["bucket"]),
                "s3_minio_endpoint": endpoint,
                "s3_region": s3_region,
                "kubelet_insecure": self.model.config["kubelet-insecure"],
                "key_format": ARGO_KEYFORMAT,
            }

        return context


if __name__ == "__main__":
    main(ArgoControllerOperator)
