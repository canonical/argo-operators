#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for the Argo Server charm."""

import logging
import unittest
from unittest.mock import Mock, patch

from lightkube.core.exceptions import ApiError
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.testing import Harness

from charm import ArgoServerOperatorCharm
from tests.unit.helpers import _FakeApiError

logger = logging.getLogger(__name__)


@patch("lightkube.core.client.GenericSyncClient", Mock)
class TestCharm(unittest.TestCase):
    """Unit tests for Argo Server charm."""

    @patch("charm.KubernetesServicePatch", lambda x, y: None)
    def setUp(self):
        """Set up actions before every test."""
        self.harness = Harness(ArgoServerOperatorCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    @patch("charm.ArgoServerOperatorCharm._create_resource")
    def test_on_install(self, create_resource):
        """Test install event."""
        self.harness.set_leader(True)
        self.harness.charm.on.install.emit()

        create_resource.assert_called()
        # Check status is Active
        self.assertEqual(self.harness.charm.unit.status, ActiveStatus())

    @patch("charm.ArgoServerOperatorCharm._patch_resource")
    def test_on_config_changed(self, patch_resource):
        """Test config_changed event."""
        self.harness.set_leader(True)
        self.harness.charm.on.config_changed.emit()

        patch_resource.assert_called()
        # Check status is Active
        self.assertEqual(self.harness.charm.unit.status, ActiveStatus())

    def test_argo_server_pebble_ready(self):
        """Test that the layer gets changed to the expected one."""
        # Check the initial Pebble plan is empty
        initial_plan = self.harness.get_container_pebble_plan("argo-server")
        self.assertEqual(initial_plan.to_yaml(), "{}\n")
        # Expected plan after Pebble ready with default config
        expected_plan = {
            "services": {
                "argo-server": {
                    "override": "replace",
                    "summary": "argo server dashboard",
                    "command": "argo server --auth-mode server",
                    "startup": "enabled",
                }
            },
        }
        # Get the argo-server container from the model
        container = self.harness.model.unit.get_container("argo-server")
        # Emit the PebbleReadyEvent carrying the argo-server container
        self.harness.charm.on.argo_server_pebble_ready.emit(container)
        # Get the plan now we've run PebbleReady
        updated_plan = self.harness.get_container_pebble_plan("argo-server").to_dict()
        # Check we've got the plan we expected
        self.assertEqual(expected_plan, updated_plan)
        # Check the service was started
        service = self.harness.model.unit.get_container("argo-server").get_service("argo-server")
        self.assertTrue(service.is_running())
        # Ensure we set an ActiveStatus with no message
        self.assertEqual(self.harness.model.unit.status, ActiveStatus())

    @patch("charm.ApiError", _FakeApiError)
    @patch("charm.ArgoServerOperatorCharm._create_resource")
    def test_blocked_on_install(self, create_resource):
        """Test unit status is blocked when failed to create resources."""
        self.harness.set_leader(True)
        create_resource.side_effect = _FakeApiError()
        try:
            self.harness.charm.on.install.emit()
        except ApiError:
            self.assertEqual(
                self.harness.charm.unit.status,
                BlockedStatus(
                    "Creating resources failed with code "
                    f"{create_resource.side_effect.response.code}."
                ),
            )

    def test_status_with_no_leader(self):
        """Test that check_leader raises an exception when the the unit is not the leader."""
        # Change the leadership status, triggering the event
        self.harness.charm.on.install.emit()
        self.assertEqual(self.harness.model.unit.status, WaitingStatus("Waiting for leadership"))
