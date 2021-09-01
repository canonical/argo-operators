import logging
from pathlib import Path

import pytest
import yaml
from pytest_operator.plugin import OpsTest

log = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
CHARM_ROOT = "."
APP_NAME = "argo-controller"

MINIO_CONFIG = {
    "access-key": "minio",
    "secret-key": "minio-secret-key",
}

@pytest.mark.abort_on_fail
async def test_build_and_deploy_with_relations(ops_test: OpsTest):
    built_charm_path = await ops_test.build_charm(CHARM_ROOT)
    log.info(f"Built charm {built_charm_path}")

    image_path = METADATA["resources"]["oci-image"]["upstream-source"]
    resources = {"oci-image": image_path}

    await ops_test.model.deploy(
        entity_url=built_charm_path,
        application_name=APP_NAME,
        resources=resources,
    )

    # Deploy required relations
    await ops_test.model.deploy(
        entity_url="minio",
        config=MINIO_CONFIG
    )
    await ops_test.model.add_relation(f"{APP_NAME}:object-storage", "minio:object-storage")

    await ops_test.model.wait_for_idle(timeout=60 * 10)
    # TODO: This does not handle blocked status right.  Sometimes it passes when argo-controller
    #  is still setting up


async def create_artifact_bucket(ops_test: OpsTest):
    # Ensure bucket is available
    model_name = ops_test.model_name
    # TODO Get minio name and port dynamically
    port = "9000"
    url = f"http://minio.{model_name}.svc.cluster.local:{port}"
    alias = "storage"
    bucket = "mlpipeline"

    minio_cmd = (
        f"mc alias set {alias} {url} {MINIO_CONFIG['access-key']} {MINIO_CONFIG['secret-key']}"
        f"&& mc mb {alias}/{bucket} -p"
    )
    kubectl_cmd = (
        "microk8s",
        "kubectl",
        "run",
        "--rm",
        "-i",
        "--restart=Never",
        "--command",
        f"--namespace={ops_test.model_name}",
        "minio-deployment-test",
        "--image=minio/mc",
        "--",
        "sh",
        "-c",
        minio_cmd,
    )

    ret_code, stdout, stderr = await ops_test.run(*kubectl_cmd)
    assert (
            ret_code == 0
    ), f"kubectl command to create argo bucket returned code {ret_code} with stdout:\n{stdout}\nstderr:\n{stderr}"


async def submit_workflow_using_artifact(ops_test: OpsTest):
    kubectl_cmd = (
        "microk8s",
        "kubectl",
        f"--namespace={ops_test.model_name}",
        "create",
        "-f",
        "tests/data/simple_artifact.yaml",
    )
    ret_code, stdout, stderr = await ops_test.run(*kubectl_cmd)
    assert (
            ret_code == 0
    ), f"kubectl command to submit argo workflow returned code {ret_code} with stdout:\n{stdout}\nstderr:\n{stderr}"

    workflow_name = stdout.split()[0]
    log.info(f"Found workflow_name={workflow_name}")
    log.info(f"Waiting on {workflow_name} to finish")

    kubectl_wait_cmd = (
        "microk8s",
        "kubectl",
        f"--namespace={ops_test.model_name}",
        "wait",
        workflow_name,
        "--for=condition=Completed",
        "--timeout=120s",
    )
    ret_code, stdout, stderr = await ops_test.run(*kubectl_wait_cmd)
    assert (
            ret_code == 0
    ), f"kubectl command to wait on argo workflow completion returned code {ret_code} with stdout:\n{stdout}\nstderr:\n{stderr}"


async def test_workflow_using_artifacts(ops_test: OpsTest):
    # Argo will fail if the artifact bucket it uses does not exist
    await create_artifact_bucket(ops_test)

    # Submit argo workflow using artifacts and wait for it to finish
    await submit_workflow_using_artifact(ops_test)
