"""Charms dependencies for tests."""

from charmed_kubeflow_chisme.testing import CharmSpec

MINIO = CharmSpec(
    charm="minio",
    channel="latest/edge",
    trust=False,
    config={
        "access-key": "minio",
        "secret-key": "minio-secret-key",
    },
)
