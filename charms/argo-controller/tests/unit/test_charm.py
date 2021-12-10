# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import pytest

from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.testing import Harness
import yaml

from charm import ArgoControllerCharm


@pytest.fixture
def harness():
    return Harness(ArgoControllerCharm)


def test_not_leader(harness):
    harness.begin_with_initial_hooks()
    assert isinstance(harness.charm.model.unit.status, WaitingStatus)


def test_missing_image(harness):
    harness.set_leader(True)
    harness.begin_with_initial_hooks()
    assert isinstance(harness.charm.model.unit.status, BlockedStatus)


def test_main_no_relation(harness):
    harness.set_leader(True)
    harness.add_oci_resource(
        "oci-image",
        {
            "registrypath": "argoproj/workflow-controller:v3.0.1",
            "username": "",
            "password": "",
        },
    )
    harness.begin_with_initial_hooks()
    pod_spec = harness.get_pod_spec()

    # confirm that we can serialize the pod spec
    yaml.safe_dump(pod_spec)

    assert isinstance(harness.charm.model.unit.status, BlockedStatus)


def test_main_with_relation(harness):
    harness.set_leader(True)
    harness.add_oci_resource(
        "oci-image",
        {
            "registrypath": "argoproj/workflow-controller:v3.0.1",
            "username": "",
            "password": "",
        },
    )
    rel_id = harness.add_relation("object-storage", "minio")
    harness.add_relation_unit(rel_id, "minio/0")
    data = {
        "service": "my-service",
        "port": 4242,
        "access-key": "my-access-key",
        "secret-key": "my-secret-key",
        "secure": True,
        "namespace": "my-namespace",
    }
    harness.update_relation_data(
        rel_id,
        "minio",
        {
            "data": yaml.dump(data),
            "_supported_versions": yaml.dump(["v1"]),
        },
    )
    harness.begin_with_initial_hooks()
    assert isinstance(harness.charm.model.unit.status, ActiveStatus)
