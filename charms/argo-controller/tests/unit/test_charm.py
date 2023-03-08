# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import json

import pytest
import yaml
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.testing import Harness

from charm import ArgoControllerCharm


@pytest.fixture(scope="function")
def harness() -> Harness:
    """Create and return Harness for testing."""
    harness = Harness(ArgoControllerCharm)

    return harness


class TestCharm:
    """Test class for Argo Workflows Controller."""

    def test_not_leader(self, harness):
        """Test not a leader scenario."""
        harness.begin_with_initial_hooks()
        assert isinstance(harness.charm.model.unit.status, WaitingStatus)

    def test_missing_image(self, harness):
        """Test missing image scenario."""
        harness.set_leader(True)
        harness.begin_with_initial_hooks()
        assert isinstance(harness.charm.model.unit.status, BlockedStatus)

    def test_main_no_relation(self, harness):
        """Test no relation scenario."""
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

    def test_main_with_relation(self, harness):
        """Test relation scenario."""
        harness.set_leader(True)
        harness.set_model_name("test_model")
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

    def test_prometheus_data_set(self, harness: Harness, mocker):
        """Test Prometheus data setting."""
        harness.set_leader(True)
        harness.set_model_name("test_kubeflow")
        harness.begin()

        mock_net_get = mocker.patch("ops.testing._TestingModelBackend.network_get")

        bind_address = "1.1.1.1"
        fake_network = {
            "bind-addresses": [
                {
                    "interface-name": "eth0",
                    "addresses": [{"hostname": "cassandra-tester-0", "value": bind_address}],
                }
            ]
        }
        mock_net_get.return_value = fake_network
        rel_id = harness.add_relation("metrics-endpoint", "otherapp")
        harness.add_relation_unit(rel_id, "otherapp/0")
        harness.update_relation_data(rel_id, "otherapp", {})

        # basic data
        assert json.loads(
            harness.get_relation_data(rel_id, harness.model.app.name)["scrape_jobs"]
        )[0]["static_configs"][0]["targets"] == ["*:9090"]

        # load alert rules from rules files
        # currently there is single alert per file
        test_alerts = []
        with open("src/prometheus_alert_rules/loglines_error.rule") as f:
            file_alert = yaml.safe_load(f.read())
            test_alerts.append(file_alert["alert"])
        with open("src/prometheus_alert_rules/loglines_warning.rule") as f:
            file_alert = yaml.safe_load(f.read())
            test_alerts.append(file_alert["alert"])
        with open("src/prometheus_alert_rules/unit_unavailable.rule") as f:
            file_alert = yaml.safe_load(f.read())
            test_alerts.append(file_alert["alert"])
        with open("src/prometheus_alert_rules/workflows_erroring.rule") as f:
            file_alert = yaml.safe_load(f.read())
            test_alerts.append(file_alert["alert"])
        with open("src/prometheus_alert_rules/workflows_failing.rule") as f:
            file_alert = yaml.safe_load(f.read())
            test_alerts.append(file_alert["alert"])
        with open("src/prometheus_alert_rules/workflows_pending.rule") as f:
            file_alert = yaml.safe_load(f.read())
            test_alerts.append(file_alert["alert"])

        # alert rules
        alert_rules = json.loads(
            harness.get_relation_data(rel_id, harness.model.app.name)["alert_rules"]
        )
        assert alert_rules is not None
        assert alert_rules["groups"] is not None

        # there are 6 groups with single alert
        rules = []
        for group in alert_rules["groups"]:
            rules.append(group["rules"][0])

        # verify number of alerts is the same in relation and in the rules file
        assert len(rules) == len(test_alerts)

        # verify alerts in relation match alerts in the rules file
        for rule in rules:
            assert rule["alert"] in test_alerts
