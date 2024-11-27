# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from pathlib import Path

import pytest
import yaml
from charmed_kubeflow_chisme.testing import (
    GRAFANA_AGENT_APP,
    assert_alert_rules,
    assert_logging,
    assert_metrics_endpoint,
    deploy_and_assert_grafana_agent,
    get_alert_rules,
)
from pytest_operator.plugin import OpsTest

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
CHARM_ROOT = "."
ARGO_CONTROLLER = "argo-controller"
ARGO_CONTROLLER_TRUST = True
MINIO = "minio"
MINIO_CHANNEL = "latest/edge"
MINIO_CONFIG = {
    "access-key": "minio",
    "secret-key": "minio-secret-key",
}

log = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
async def test_build_and_deploy_with_relations(ops_test: OpsTest):
    built_charm_path = await ops_test.build_charm(CHARM_ROOT)
    log.info(f"Built charm {built_charm_path}")

    image_path = METADATA["resources"]["oci-image"]["upstream-source"]
    resources = {"oci-image": image_path}

    await ops_test.model.deploy(
        entity_url=built_charm_path,
        application_name=ARGO_CONTROLLER,
        resources=resources,
        trust=ARGO_CONTROLLER_TRUST,
    )

    # Deploy required relations
    await ops_test.model.deploy(entity_url=MINIO, config=MINIO_CONFIG, channel=MINIO_CHANNEL)
    await ops_test.model.integrate(f"{ARGO_CONTROLLER}:object-storage", f"{MINIO}:object-storage")

    await ops_test.model.wait_for_idle(timeout=60 * 10)
    # TODO: This does not handle blocked status right.  Sometimes it passes when argo-controller
    #  is still setting up

    # The unit should be active before creating/testing resources
    await ops_test.model.wait_for_idle(apps=[ARGO_CONTROLLER], status="active", timeout=1000)

    # Deploying grafana-agent-k8s and add all relations
    await deploy_and_assert_grafana_agent(
        ops_test.model, ARGO_CONTROLLER, metrics=True, dashboard=True, logging=True
    )


async def create_artifact_bucket(ops_test: OpsTest):
    # Ensure bucket is available
    model_name = ops_test.model_name
    # TODO Get minio name and port dynamically
    port = "9000"
    url = f"http://minio.{model_name}.svc.cluster.local:{port}"
    alias = "storage"
    bucket = "mlpipeline"

    minio_cmd = (
        f"mc alias set {alias} {url} {MINIO_CONFIG['access-key']} {MINIO_CONFIG['secret-key']}"  # noqa
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
    assert ret_code == 0, (
        f"kubectl command to create argo bucket returned code {ret_code} with "
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )


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
    assert ret_code == 0, (
        f"kubectl command to submit argo workflow returned code {ret_code} with "
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )

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
    assert ret_code == 0, (
        f"kubectl command to wait on argo workflow completion returned code {ret_code} with"
        f" stdout:\n{stdout}\nstderr:\n{stderr}"
    )


async def test_workflow_using_artifacts(ops_test: OpsTest):
    # Argo will fail if the artifact bucket it uses does not exist
    await create_artifact_bucket(ops_test)

    # Submit argo workflow using artifacts and wait for it to finish
    await submit_workflow_using_artifact(ops_test)


async def test_alert_rules(ops_test):
    """Test check charm alert rules and rules defined in relation data bag."""
    app = ops_test.model.applications[ARGO_CONTROLLER]
    alert_rules = get_alert_rules()
    log.info("found alert_rules: %s", alert_rules)
    await assert_alert_rules(app, alert_rules)


async def test_metrics_enpoint(ops_test):
    """Test metrics_endpoints are defined in relation data bag and their accessibility.

    This function gets all the metrics_endpoints from the relation data bag, checks if
    they are available from the grafana-agent-k8s charm and finally compares them with the
    ones provided to the function.
    """
    app = ops_test.model.applications[ARGO_CONTROLLER]
    await assert_metrics_endpoint(app, metrics_port=9090, metrics_path="/metrics")


async def test_logging(ops_test):
    """Test logging is defined in relation data bag."""
    app = ops_test.model.applications[GRAFANA_AGENT_APP]
    await assert_logging(app)
