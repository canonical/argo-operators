# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from pathlib import Path

import lightkube
import pytest
import yaml
from charmed_kubeflow_chisme.testing import (
    GRAFANA_AGENT_APP,
    assert_alert_rules,
    assert_logging,
    assert_metrics_endpoint,
    assert_security_context,
    deploy_and_assert_grafana_agent,
    generate_container_securitycontext_map,
    get_alert_rules,
    get_pod_names,
)
from charmed_kubeflow_chisme.testing.s3_integration import deploy_and_assert_s3_integrator
from charms_dependencies import S3_INTEGRATOR
from pytest_operator.plugin import OpsTest

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
CONTAINERS_SECURITY_CONTEXT_MAP = generate_container_securitycontext_map(METADATA)
CHARM_ROOT = "."
ARGO_CONTROLLER = METADATA["name"]
ARGO_CONTROLLER_TRUST = True


log = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def lightkube_client() -> lightkube.Client:
    """Returns lightkube Kubernetes client"""
    client = lightkube.Client(field_manager=f"{ARGO_CONTROLLER}")
    return client


@pytest.mark.abort_on_fail
@pytest.mark.skip_if_deployed
async def test_build_and_deploy_with_relations(ops_test: OpsTest, request):
    entity_url = (
        await ops_test.build_charm(CHARM_ROOT)
        if not (entity_url := request.config.getoption("--charm-path"))
        else entity_url
    )
    log.info(f"Built charm {entity_url}")

    image_path = METADATA["resources"]["oci-image"]["upstream-source"]
    resources = {"oci-image": image_path}

    await ops_test.model.deploy(
        entity_url=entity_url,
        application_name=ARGO_CONTROLLER,
        resources=resources,
        trust=ARGO_CONTROLLER_TRUST,
    )

    # Deploy required relations
    await deploy_and_assert_s3_integrator(ops_test.model, s3_integrator=S3_INTEGRATOR)
    await ops_test.model.integrate(
        f"{ARGO_CONTROLLER}:s3-credentials", f"{S3_INTEGRATOR.charm}:s3-credentials"
    )

    await ops_test.model.wait_for_idle(timeout=60 * 10)
    # TODO: This does not handle blocked status right.  Sometimes it passes when argo-controller
    #  is still setting up

    # The unit should be active before creating/testing resources
    await ops_test.model.wait_for_idle(apps=[ARGO_CONTROLLER], status="active", timeout=1000)

    # Deploying grafana-agent-k8s and add all relations
    await deploy_and_assert_grafana_agent(
        ops_test.model, ARGO_CONTROLLER, metrics=True, dashboard=True, logging=True
    )


async def submit_workflow_using_artifact(ops_test: OpsTest):
    kubectl_cmd = (
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
    # Submit argo workflow using artifacts and wait for it to finish
    await submit_workflow_using_artifact(ops_test)


async def test_alert_rules(ops_test):
    """Test check charm alert rules and rules defined in relation data bag."""
    app = ops_test.model.applications[ARGO_CONTROLLER]
    alert_rules = get_alert_rules()
    log.info("found alert_rules: %s", alert_rules)
    await assert_alert_rules(app, alert_rules)


async def test_metrics_endpoint(ops_test):
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


@pytest.mark.parametrize("container_name", list(CONTAINERS_SECURITY_CONTEXT_MAP.keys()))
async def test_container_security_context(
    ops_test: OpsTest,
    lightkube_client: lightkube.Client,
    container_name: str,
):
    """Test container security context is correctly set.

    Verify that container spec defines the security context with correct
    user ID and group ID.
    """
    pod_name = get_pod_names(ops_test.model.name, ARGO_CONTROLLER)[0]
    assert_security_context(
        lightkube_client,
        pod_name,
        container_name,
        CONTAINERS_SECURITY_CONTEXT_MAP,
        ops_test.model.name,
    )
