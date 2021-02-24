"""
Minio Interface
"""

# The unique Charmhub library identifier, never change it
LIBID = "7d0810fb78cf4afd81cc70118b27a127"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft push-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

from provide_interface import ProvideAppInterface
from require_interface import RequireAppInterface

from ops.charm import CharmBase

# Serialized Data Schema for Minio Interface
MINIO_SCHEMA="""
type: object
properties:
  service:
    type: string
  port:
    type: number
  access-key:
    type: string
  secret-key:
    type: string
"""

class MinioProvide(ProvideAppInterface):
    def __init__(
        self, charm: CharmBase, relation_name: str
    ):
        super().__init__(charm, relation_name, MINIO_SCHEMA)


class MinioRequire(RequireAppInterface):
    def __init__(
        self, charm: CharmBase, relation_name: str
    ):
        super().__init__(charm, relation_name, MINIO_SCHEMA)
