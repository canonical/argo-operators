import logging

from charmed_kubeflow_chisme.components import Component
from ops import ActiveStatus, BlockedStatus, StatusBase

logger = logging.getLogger(__name__)


class S3RelationsConflictDetectorComponent(Component):
    """Component to detect conflicting S3 relations."""

    def __init__(
        self,
        *args,
        object_storage_relation_name: str = "object-storage",
        s3_relation_name: str = "s3-credentials",
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.object_storage_relation_name = object_storage_relation_name
        self.s3_relation_name = s3_relation_name

    def get_status(self) -> StatusBase:
        """Check that both relations are not present simultaneously."""
        object_storage_relation = self._charm.model.get_relation(self.object_storage_relation_name)
        s3_relation = self._charm.model.get_relation(self.s3_relation_name)

        if object_storage_relation and s3_relation:
            logger.error(
                f"Both '{self.object_storage_relation_name}' and '{self.s3_relation_name}' "
                "relations are present, remove one to unblock."
            )
            return BlockedStatus(
                f"Cannot have both '{self.object_storage_relation_name}' and "
                f"'{self.s3_relation_name}' relations at the same time."
            )
        return ActiveStatus()
