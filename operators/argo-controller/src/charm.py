#!/usr/bin/env python3

import logging
import os
from pathlib import Path
from base64 import b64encode
import yaml

from ops.charm import CharmBase
from ops.main import main
from ops.model import ActiveStatus, MaintenanceStatus, WaitingStatus, BlockedStatus
from ops.framework import StoredState

from oci_image import OCIImageResource, OCIImageResourceError
from charms.minio.v0.minio_interface import MinioRequire


class ArgoControllerCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        if not self.unit.is_leader():
            # We can't do anything useful when not the leader, so do nothing.
            self.model.unit.status = WaitingStatus('Waiting for leadership')
            return
        self.log = logging.getLogger(__name__)
        self.minio = MinioRequire(self, "minio")
        self.image = OCIImageResource(self, 'oci-image')
        for event in [self.on.install,
                      self.on.leader_elected,
                      self.on.upgrade_charm,
                      self.on.config_changed,
                      self.on.minio_relation_changed]:
            self.framework.observe(event, self.main)


    def main(self, event):
        try:
            image_details = self.image.fetch()
        except OCIImageResourceError as e:
            self.model.unit.status = e.status
            self.log.info(e)
            return

        self.model.unit.status = MaintenanceStatus('Setting pod spec')

        self.log.info("RELATIONS: {}".format(self.model.relations["minio"]))

        if not self.minio.is_created:
            self.log.info("Waiting for Minio")
            self.model.unit.status = BlockedStatus("Waiting for Minio")
            return

        if not self.minio.is_available:
            self.log.info("Waiting for Minio data")
            self.model.unit.status = MaintenanceStatus("Waiting for Minio data")
            return

        minio_data = self.minio.data[0]

        service = minio_data["service"]
        port = minio_data["port"]
        access_key = minio_data["access-key"]
        secret_key = minio_data["secret-key"]
        crd = yaml.safe_load(Path("files/crd-v1alpha1.yaml").read_text())

        self.model.pod.set_spec({
            'version': 3,
            'serviceAccount': {
                'roles': [{
                    'global': True,
                    'rules': [
                        {
                            'apiGroups': [''],
                            'resources': ['pods', 'pods/exec'],
                            'verbs': ['create', 'get', 'list', 'watch', 'update', 'patch'],
                        },
                        {
                            'apiGroups': [''],
                            'resources': ['configmaps'],
                            'verbs': ['get', 'watch', 'list'],
                        },
                        {
                            'apiGroups': [''],
                            'resources': ['persistentvolumeclaims'],
                            'verbs': ['create', 'delete'],
                        },
                        {
                            'apiGroups': ['argoproj.io'],
                            'resources': ['workflows', 'workflows/finalizers'],
                            'verbs': ['get', 'list', 'watch', 'update', 'patch'],
                        },
                    ],
                }],
            },
            'service': {
                'updateStrategy': {
                    'type': 'RollingUpdate',
                    'rollingUpdate': {'maxUnavailable': 1},
                },
            },
            'containers': [
                {
                    'name': self.model.app.name,
                    'imageDetails': image_details,
                    'imagePullPolicy': 'Always',
                    'args': ['--configmap', 'argo-controller-configmap-config'],
                    'envConfig': {'ARGO_NAMESPACE': os.environ['JUJU_MODEL_NAME']},
                    'volumeConfig': [
                        {
                            'name': 'configmap',
                            'mountPath': '/config-map.yaml',
                            'files': [{
                                'path': 'config',
                                'content': yaml.dump(
                                    {
                                        'executorImage': 'argoproj/argoexec:v2.3.0',
                                        'containerRuntimeExecutor': self.model.config.get('executor'),
                                        'kubeletInsecure': self.model.config.get('kubelet-insecure'),
                                        'artifactRepository': {
                                            's3': {
                                                'bucket': self.model.config.get('bucket'),
                                                'keyPrefix': self.model.config.get('key-prefix'),
                                                'endpoint': f'{service}:{port}',
                                                'insecure': True,
                                                'accessKeySecret': {
                                                    'name': 'mlpipeline-minio-artifact',
                                                    'key': 'accesskey',
                                                },
                                                'secretKeySecret': {
                                                    'name': 'mlpipeline-minio-artifact',
                                                    'key': 'secretkey',
                                                },
                                            }
                                        },
                                    }
                                )
                            }],
                        }
                    ],
                },
            ],
            'kubernetesResources': {
                    'customResourceDefinitions': [{
                        'name': crd['metadata']['name'],
                        'labels': {},
                        'spec': crd['spec'],
                    }],
                'secrets': [
                    {
                        'name': 'mlpipeline-minio-artifact',
                        'type': 'Opaque',
                        'data': {
                            'accesskey': b64encode(
                                access_key.encode('utf-8')
                            ),
                            'secretkey': b64encode(
                                secret_key.encode('utf-8')
                            ),
                        },
                    }
                ],
            }
        })

        self.model.unit.status = ActiveStatus()


if __name__ == "__main__":
    main(ArgoControllerCharm)
