#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

import logging
import unittest
from unittest.mock import patch

from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.testing import Harness

from charm import ArgoServerOperatorCharm


logger = logging.getLogger(__name__)


class TestCharm(unittest.TestCase):

    @patch("charm.KubernetesServicePatch", lambda x, y: None)
    def setUp(self):
        self.harness = Harness(ArgoServerOperatorCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    @patch("charm.ArgoServerOperatorCharm._create_resource")
    def test_on_install(self, create_resource):
        """"""
        self.harness.charm.on.install.emit()

        create_resource.assert_called_once()
        # Check status is Active
        self.assertEqual(self.harness.charm.unit.status, ActiveStatus())

    @patch("charm.ArgoServerOperatorCharm._patch_resource")
    def test_on_config_changed(self, patch_resource):
        """"""
        self.harness.charm.on.config_changed.emit()

        patch_resource.assert_called_once()
        # Check status is Active
        self.assertEqual(self.harness.charm.unit.status, ActiveStatus())

    def test_argo_server_pebble_ready(self):
        # Check the initial Pebble plan is empty
        initial_plan = self.harness.get_container_pebble_plan("argo-server")
        self.assertEqual(initial_plan.to_yaml(), "{}\n")
        # Expected plan after Pebble ready with default config
        expected_plan = {
            "services": {
                "argo-server": {
                    "override": "replace",
                    "summary": "argo server dashboard",
                    "command": "argo server",
                    "startup": "enabled"
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


# @pytest.fixture
# def harness():
#     return Harness(Operator)


# def test_not_leader(harness):
#     harness.begin_with_initial_hooks()
#     assert isinstance(harness.charm.model.unit.status, WaitingStatus)


# def test_missing_image(harness):
#     harness.set_leader(True)
#     harness.begin_with_initial_hooks()
#     assert isinstance(harness.charm.model.unit.status, BlockedStatus)


# def test_main(harness):
#     harness.set_leader(True)
#     harness.add_oci_resource(
#         "oci-image",
#         {
#             "registrypath": "test-ci",
#             "username": "",
#             "password": "",
#         },
#     )
#     harness.begin_with_initial_hooks()
#     pod_spec = harness.get_pod_spec()

#     # confirm that we can serialize the pod spec
#     yaml.safe_dump(pod_spec)

#     assert isinstance(harness.charm.model.unit.status, ActiveStatus)
