"""Charms dependencies for tests."""

from charmed_kubeflow_chisme.testing import CharmSpec

# Previous stable track, used as the starting point of the upgrade test.
# Bump it whenever a new Argo Workflows track is released.
ARGO_CONTROLLER_PREVIOUS = CharmSpec(
    charm="argo-controller",
    channel="3.7/stable",
    trust=True,
)

MINIO = CharmSpec(
    charm="minio",
    channel="latest/edge",
    trust=False,
    config={
        "access-key": "minio",
        "secret-key": "minio-secret-key",
    },
)

S3_INTEGRATOR = CharmSpec(
    charm="s3-integrator",
    channel="2/stable",
    trust=False,
)
