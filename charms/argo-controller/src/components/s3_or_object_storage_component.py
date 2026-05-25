# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Component that provides a unified S3 data interface from either object-storage or s3.

This component internally manages both the object-storage (SDI) and s3-credentials
relation handlers. It is the only component registered with the CharmReconciler,
ensuring that individual relation statuses (e.g. object-storage being Blocked when
only s3-credentials is used) do not pollute the charm's aggregate status.
"""

import logging
from typing import Optional

from charmed_kubeflow_chisme.components.component import Component
from charmed_kubeflow_chisme.components.serialised_data_interface_components import (
    SdiRelationDataReceiverComponent,
)
from charmed_kubeflow_chisme.exceptions import ErrorWithStatus
from object_storage import S3Requirer
from ops.charm import CharmBase
from ops.model import ActiveStatus, BlockedStatus, StatusBase

logger = logging.getLogger(__name__)


class S3OrObjectStorageComponent(Component):
    """Unified storage component enforcing mutual exclusivity between object-storage and s3.

    Internally creates and manages:
    - An SdiRelationDataReceiverComponent for the object-storage relation
    - An S3Requirer for the s3-credentials relation

    Only this component's get_status() contributes to the charm's aggregate status.
    """

    def __init__(
        self,
        charm: CharmBase,
        name: str,
        object_storage_relation_name: str = "object-storage",
        s3_relation_name: str = "s3-credentials",
    ):
        super().__init__(charm, name)
        self._charm = charm
        self._object_storage_relation_name = object_storage_relation_name
        self._s3_relation_name = s3_relation_name

        # Internal SDI component for object-storage (not registered with CharmReconciler)
        self._object_storage = SdiRelationDataReceiverComponent(
            charm=charm,
            name=f"{name}:_internal_object_storage",
            relation_name=object_storage_relation_name,
        )

        # Internal S3Requirer for s3-credentials
        self._s3_requirer = S3Requirer(
            charm=charm,
            relation_name=s3_relation_name,
        )

        # Expose events from both sub-components so CharmReconciler triggers on them
        self._events_to_observe = [
            # object-storage events
            charm.on[object_storage_relation_name].relation_changed,
            charm.on[object_storage_relation_name].relation_broken,
            # s3-credentials events
            self._s3_requirer.on.storage_connection_info_changed,
            self._s3_requirer.on.storage_connection_info_gone,
        ]

    @property
    def _object_storage_exists(self) -> bool:
        """Return True if the object-storage relation exists."""
        return bool(self._charm.model.relations.get(self._object_storage_relation_name))

    @property
    def _s3_exists(self) -> bool:
        """Return True if the s3-credentials relation exists."""
        return bool(self._charm.model.relations.get(self._s3_relation_name))

    @property
    def _object_storage_active(self) -> bool:
        """Return True if the object-storage relation is active and has data."""
        return isinstance(self._object_storage.get_status(), ActiveStatus)

    @property
    def _s3_active(self) -> bool:
        """Return True if the s3-credentials relation has data."""
        if not self._s3_exists:
            return False
        data = self._get_s3_raw_data()
        return data is not None

    def get_status(self) -> StatusBase:
        """Return status enforcing mutual exclusivity."""
        obj_exists = self._object_storage_exists
        s3_exists = self._s3_exists

        if obj_exists and s3_exists:
            return BlockedStatus(
                "Only one of 'object-storage' or 's3-credentials' relations may be active"
            )

        if not obj_exists and not s3_exists:
            return BlockedStatus(
                "Missing relation: provide either 'object-storage' or 's3-credentials'"
            )

        # Exactly one exists - check if it has data
        if obj_exists:
            return self._object_storage.get_status()

        # s3 exists
        data = self._get_s3_raw_data()
        if not data:
            return BlockedStatus("s3-credentials relation exists but has no data")
        return ActiveStatus()

    @property
    def active_source(self) -> Optional[str]:
        """Return which source is active: 'object-storage', 's3', or None."""
        if self._object_storage_active:
            return "object-storage"
        if self._s3_active:
            return "s3"
        return None

    def _get_s3_raw_data(self) -> Optional[dict]:
        """Get raw S3 data from the s3-credentials relation, or None."""
        if not self._s3_exists:
            return None
        info = self._s3_requirer.get_storage_connection_info()
        if not info or not info.get("access-key") or not info.get("secret-key"):
            return None
        return dict(info)

    def get_data(self) -> dict:
        """Return normalized S3 connection data from whichever relation is active.

        Returns a dict with keys:
            - access-key: str
            - secret-key: str
            - endpoint: str (host:port, scheme is stripped if present)
            - bucket: str (from charm config if set, otherwise from relation)
            - insecure: bool (from charm config if set, otherwise derived from endpoint/relation)

        Raises ErrorWithStatus if no data is available.
        """
        source = self.active_source
        if source is None:
            raise ErrorWithStatus(
                "No active storage relation", BlockedStatus("No active storage relation")
            )

        if source == "object-storage":
            return self._get_object_storage_data()
        return self._get_s3_data()

    def _get_object_storage_data(self) -> dict:
        """Normalize data from the object-storage (SDI) relation."""
        raw = self._object_storage.get_data()
        endpoint = (
            f"{raw['service']}.{raw.get('namespace', self._charm.model.name)}:{raw['port']}"
        )
        bucket = self._charm.model.config.get("bucket", "mlpipeline")
        insecure = self._charm.model.config.get("kubelet-insecure", True)

        return {
            "access-key": raw["access-key"],
            "secret-key": raw["secret-key"],
            "endpoint": endpoint,
            "bucket": bucket,
            "insecure": insecure,
        }

    def _get_s3_data(self) -> dict:
        """Normalize data from the s3-credentials relation."""
        raw = self._get_s3_raw_data()
        endpoint = raw.get("endpoint", "")

        # Derive insecure from endpoint scheme, charm config overrides
        config_insecure = self._charm.model.config.get("kubelet-insecure")
        if config_insecure is not None:
            insecure = config_insecure
        else:
            insecure = endpoint.startswith("http://") if endpoint else True

        # Strip scheme from endpoint: Argo's S3 client expects host:port only,
        # not a full URL. The insecure field controls whether HTTP or HTTPS is used.
        for scheme in ("https://", "http://"):
            if endpoint.startswith(scheme):
                endpoint = endpoint[len(scheme):]
                break

        # Charm config bucket wins, then relation bucket, then default
        config_bucket = self._charm.model.config.get("bucket")
        bucket = config_bucket or raw.get("bucket", "mlpipeline")

        return {
            "access-key": raw["access-key"],
            "secret-key": raw["secret-key"],
            "endpoint": endpoint,
            "bucket": bucket,
            "insecure": insecure,
        }
