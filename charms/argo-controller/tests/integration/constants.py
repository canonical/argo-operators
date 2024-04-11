# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

from pathlib import Path

import yaml

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
CHARM_ROOT = "."
ARGO_CONTROLLER = "argo-controller"
ARGO_CONTROLLER_TRUST = True
MINIO = "minio"
MINIO_CHANNEL = "ckf-1.8/stable"
MINIO_CONFIG = {
    "access-key": "minio",
    "secret-key": "minio-secret-key",
}

PROMETHEUS_K8S = "prometheus-k8s"
PROMETHEUS_K8S_CHANNEL = "1.0/stable"
PROMETHEUS_K8S_TRUST = True
GRAFANA_K8S = "grafana-k8s"
GRAFANA_K8S_CHANNEL = "1.0/stable"
GRAFANA_K8S_TRUST = True
PROMETHEUS_SCRAPE_K8S = "prometheus-scrape-k8s"
PROMETHEUS_SCRAPE_K8S_CHANNEL = "1.0/stable"
PROMETHEUS_SCRAPE_CONFIG = {"scrape_interval": "30s"}
