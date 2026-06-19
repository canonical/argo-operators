# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from charmed_kubeflow_chisme.testing import add_sdi_relation_to_harness
from ops.model import ActiveStatus, BlockedStatus
from ops.testing import Harness

from charm import ArgoControllerOperator

MOCK_OBJECT_STORAGE_DATA = {
    "access-key": "access-key",
    "secret-key": "secret-key",
    "service": "service",
    "namespace": "namespace",
    "port": 1234,
    "secure": True,
}

MOCK_S3_DATA_BASE = {
    "access-key": "access-key",
    "secret-key": "secret-key",
    "bucket": "mybucket",
    "region": "us-east-1",
}

EXPECTED_ENVIRONMENT = {
    "ARGO_NAMESPACE": "namespace",
    "LEADER_ELECTION_IDENTITY": "argo-controller",
}


@pytest.fixture
def harness() -> Harness:
    harness = Harness(ArgoControllerOperator)
    return harness


@pytest.fixture()
def mocked_lightkube_client(mocker):
    """Mocks the Lightkube Client in charm.py, returning a mock instead."""
    mocked_lightkube_client = MagicMock()
    mocker.patch("charm.lightkube.Client", return_value=mocked_lightkube_client)
    yield mocked_lightkube_client


@pytest.fixture()
def mocked_kubernetes_service_patch(mocker):
    """Mocks the KubernetesServicePatch for the charm."""
    mocked_kubernetes_service_patch = mocker.patch(
        "charm.KubernetesServicePatch", lambda x, y, service_name: None
    )
    yield mocked_kubernetes_service_patch


def test_log_forwarding(
    harness: Harness, mocked_lightkube_client, mocked_kubernetes_service_patch
):
    with patch("charm.LogForwarder") as mock_logging:
        harness.begin()
        mock_logging.assert_called_once_with(charm=harness.charm)


def test_not_leader(harness, mocked_lightkube_client, mocked_kubernetes_service_patch):
    """Test when we are not the leader."""
    harness.begin_with_initial_hooks()
    # Assert that we are not Active, and that the leadership-gate is the cause.
    assert not isinstance(harness.charm.model.unit.status, ActiveStatus)
    assert harness.charm.model.unit.status.message.startswith("[leadership-gate]")


def test_object_storage_relation_with_data(
    harness, mocked_lightkube_client, mocked_kubernetes_service_patch
):
    """Test that if Leadership is Active, the object storage relation operates as expected.

    Note: See test_relation_components.py for an alternative way of unit testing Components without
          mocking the regular charm.
    """
    # Arrange
    harness.begin()

    # Mock:
    # * leadership_gate to be active and executed
    harness.charm.leadership_gate.get_status = MagicMock(return_value=ActiveStatus())

    # Add relation with data.  This should trigger a charm reconciliation due to relation-changed.
    add_sdi_relation_to_harness(harness, "object-storage", data=MOCK_OBJECT_STORAGE_DATA)

    # Assert
    assert isinstance(harness.charm.object_storage_relation.status, ActiveStatus)


def test_object_storage_relation_without_data(
    harness, mocked_lightkube_client, mocked_kubernetes_service_patch
):
    """Test that the object storage relation is Active when a relation exists but has no data.

    With minimum_related_applications=0 the relation is optional, so even an empty relation
    does not block the component.
    """
    # Arrange
    harness.begin()

    # Mock:
    # * leadership_gate to be active and executed
    harness.charm.leadership_gate.get_status = MagicMock(return_value=ActiveStatus())

    # Add relation with data.  This should trigger a charm reconciliation due to relation-changed.
    add_sdi_relation_to_harness(harness, "object-storage", data={})

    # Assert
    assert isinstance(harness.charm.object_storage_relation.status, ActiveStatus)


def test_object_storage_relation_without_relation(
    harness, mocked_lightkube_client, mocked_kubernetes_service_patch
):
    """Test that the object storage relation is Active when no relation is established.

    The object-storage relation has minimum_related_applications=0, making it optional.
    The s3-relations-conflict-detector is mocked Active here to isolate this component.
    """
    # Arrange
    harness.begin()

    # Mock:
    # * leadership_gate to be active and executed
    # * s3_relations_conflict_detector to be active (tested separately)
    harness.charm.leadership_gate.get_status = MagicMock(return_value=ActiveStatus())
    harness.charm.s3_relations_conflict_detector.get_status = MagicMock(
        return_value=ActiveStatus()
    )

    # Act
    harness.charm.on.install.emit()

    # Assert
    assert isinstance(harness.charm.object_storage_relation.status, ActiveStatus)


@pytest.mark.parametrize(
    "add_s3_credentials, add_object_storage, expected_status",
    [
        pytest.param(False, False, BlockedStatus, id="no-relation"),
        pytest.param(True, False, ActiveStatus, id="s3-credentials-only"),
        pytest.param(False, True, ActiveStatus, id="object-storage-only"),
        pytest.param(True, True, BlockedStatus, id="both-relations"),
    ],
)
def test_s3_relations_conflict_detector_status(
    harness,
    mocked_lightkube_client,
    mocked_kubernetes_service_patch,
    add_s3_credentials,
    add_object_storage,
    expected_status,
):
    """Test that s3-relations-conflict-detector blocks unless exactly one storage relation is set.

    Exactly one of object-storage or s3-credentials must be present at a time:
    - none active  → Blocked (too few)
    - one active   → Active
    - both active  → Blocked (too many)
    """
    harness.begin()
    harness.charm.leadership_gate.get_status = MagicMock(return_value=ActiveStatus())

    if add_s3_credentials:
        harness.add_relation("s3-credentials", "s3-provider")
    if add_object_storage:
        harness.add_relation("object-storage", "object-storage-provider")

    harness.charm.on.install.emit()

    assert isinstance(harness.charm.s3_relations_conflict_detector.status, expected_status)


def test_kubernetes_created_method(
    harness, mocked_lightkube_client, mocked_kubernetes_service_patch
):
    """Test whether we try to create Kubernetes resources when we have leadership."""
    # Arrange
    # Needed because kubernetes component will only apply to k8s if we are the leader
    harness.set_leader(True)
    harness.begin()

    # Need to mock the leadership-gate to be active, and the kubernetes auth component so that it
    # sees the expected resources when calling _get_missing_kubernetes_resources

    harness.charm.leadership_gate.get_status = MagicMock(return_value=ActiveStatus())

    # Add relation with data.  This should trigger a charm reconciliation due to relation-changed.
    add_sdi_relation_to_harness(harness, "object-storage", data=MOCK_OBJECT_STORAGE_DATA)

    harness.charm.kubernetes_resources.component._get_missing_kubernetes_resources = MagicMock(
        return_value=[]
    )

    # Act
    harness.charm.on.install.emit()

    # FIXME: This is a hardcoded count of the Kubernetes objects that should be created.
    # `reconcile` is called 3 times (13 resources × 3 = 39):
    #   - `object_storage_relation_changed` fires once but triggers 2 reconcile cycles because
    #     both RelationCountGateComponent and SdiRelationDataReceiverComponent observe it
    #     (CharmReconciler registers them independently without deduplication).
    #   - `install` triggers a third reconcile cycle.
    assert mocked_lightkube_client.apply.call_count == 39
    assert isinstance(harness.charm.kubernetes_resources.status, ActiveStatus)


def test_pebble_services_running(
    harness, mocked_lightkube_client, mocked_kubernetes_service_patch
):
    """Test that if the Kubernetes Component is Active, the pebble services successfully start."""
    # Arrange
    harness.set_model_name(EXPECTED_ENVIRONMENT["ARGO_NAMESPACE"])
    harness.begin()
    harness.set_can_connect("argo-controller", True)

    # Mock:
    # * leadership_gate to have get_status=>Active
    # * s3_relations_conflict_detector to be active (tested separately)
    # * object_storage_relation to return mock data, making the item go active
    # * kubernetes_resources to have get_status=>Active
    harness.charm.leadership_gate.get_status = MagicMock(return_value=ActiveStatus())
    harness.charm.s3_relations_conflict_detector.get_status = MagicMock(
        return_value=ActiveStatus()
    )
    harness.charm.object_storage_relation.component.get_data = MagicMock(
        return_value=MOCK_OBJECT_STORAGE_DATA
    )
    harness.charm.kubernetes_resources.get_status = MagicMock(return_value=ActiveStatus())

    # Act
    harness.charm.on.install.emit()

    # Assert
    container = harness.charm.unit.get_container("argo-controller")
    service = container.get_service("argo-controller")
    assert service.is_running()
    # Assert the environment variables that are set from inputs are correctly applied
    environment = container.get_plan().services["argo-controller"].environment
    assert environment == EXPECTED_ENVIRONMENT


@pytest.mark.parametrize(
    "raw_endpoint, expected_endpoint",
    [
        ("http://10.0.0.1", "10.0.0.1"),
        ("https://s3.example.com", "s3.example.com"),
        ("http://10.0.0.1:9000", "10.0.0.1:9000"),
        ("https://s3.example.com:443", "s3.example.com:443"),
        ("10.0.0.1", "10.0.0.1"),
        ("10.0.0.1:9000", "10.0.0.1:9000"),
    ],
)
def test_s3_endpoint_scheme_is_stripped(
    harness,
    mocked_lightkube_client,
    mocked_kubernetes_service_patch,
    mocker,
    raw_endpoint,
    expected_endpoint,
):
    """Test that URL schemes are stripped from S3 endpoints before passing to Argo.

    Argo's S3 client requires a bare host[:port] endpoint and rejects full URLs
    such as 'http://10.0.0.1' with "Endpoint url cannot have fully qualified paths".
    """
    harness.begin()
    harness.charm.leadership_gate.get_status = MagicMock(return_value=ActiveStatus())

    s3_component = harness.charm.s3_relation.component
    s3_component.get_data = MagicMock(
        return_value=[
            {
                **MOCK_S3_DATA_BASE,
                "endpoint": raw_endpoint,
            }
        ]
    )

    mocker.patch.object(
        type(harness.charm),
        "active_storage_component",
        new_callable=PropertyMock,
        return_value=s3_component,
    )

    context = harness.charm._context_callable()

    assert context["s3_minio_endpoint"] == expected_endpoint
    assert context["s3_region"] == MOCK_S3_DATA_BASE["region"]
