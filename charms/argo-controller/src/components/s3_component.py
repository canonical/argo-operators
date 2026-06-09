# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

from charmed_kubeflow_chisme.components.component import Component
from object_storage import S3Info, S3Requirer
from ops import ActiveStatus, BlockedStatus, CharmBase, StatusBase

logger = logging.getLogger(__name__)


class S3Component(Component):
    """A Component that provides S3 storage connection data via the s3-credentials relation.

    Wraps an S3Requirer to read connection info (endpoint, credentials, bucket, etc.)
    from the relation bag and expose it through the standard Component interface.
    """

    def __init__(
        self,
        charm: CharmBase,
        name: str,
        relation_name: str,
        is_optional: bool,
    ):
        """Initialise the S3Component.

        Args:
            charm: The parent charm instance.
            name: A unique name for this component within the CharmReconciler.
            relation_name: The name of the s3-credentials relation.
            is_optional: When True, the component reports Active even if the
                relation is absent, allowing the charm to fall back to another
                storage backend.
        """
        super().__init__(charm=charm, name=name)
        self.relation_name = relation_name
        self.s3_client = S3Requirer(charm=charm, relation_name=relation_name)
        self.is_optional = is_optional

    def get_data(self) -> S3Info:
        """Return S3 connection info from the relation bag.

        Returns:
            An S3Info dict containing keys such as ``endpoint``, ``access-key``,
            ``secret-key``, and ``bucket`` as provided by the related application.
        """
        return self.s3_client.get_storage_connection_info()

    def get_status(self) -> StatusBase:
        """Return the component status based on relation availability.

        Returns:
            ActiveStatus if the relation is present (or the component is optional).
            BlockedStatus prompting the operator to add the relation otherwise.
        """
        if self.is_optional:
            return ActiveStatus()

        if not self._charm.model.get_relation(self.relation_name):
            return BlockedStatus(f"Please add the missing relation: {self.relation_name}")

        return ActiveStatus()
