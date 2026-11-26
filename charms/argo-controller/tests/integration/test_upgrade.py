# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Upgrade argo-controller from the previous stable track to the locally built charm."""

import logging
import subprocess
import time
from pathlib import Path

import jubilant
import lightkube
import pytest
import yaml
from charms_dependencies import ARGO_CONTROLLER_PREVIOUS, MINIO
from lightkube import codecs
from lightkube.generic_resource import create_namespaced_resource
from lightkube.models.core_v1 import Container, PodSpec
from lightkube.models.meta_v1 import ObjectMeta
from lightkube.models.rbac_v1 import PolicyRule, RoleRef, Subject
from lightkube.resources.apiextensions_v1 import CustomResourceDefinition
from lightkube.resources.core_v1 import ConfigMap, Pod
from lightkube.resources.rbac_authorization_v1 import Role, RoleBinding

log = logging.getLogger(__name__)

CHARM_ROOT = Path(".")
METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
CONFIG = yaml.safe_load(Path("./config.yaml").read_text())
APP = METADATA["name"]
IMAGE = METADATA["resources"]["oci-image"]["upstream-source"]
EXECUTOR_IMAGE = CONFIG["options"]["executor-image"]["default"]
CONFIGMAP_NAME = "argo-workflow-controller-configmap"
MINIO_CLIENT_IMAGE = "cgr.dev/chainguard/minio-client:latest-dev"
WORKFLOW_TIMEOUT = 300

Workflow = create_namespaced_resource("argoproj.io", "v1alpha1", "Workflow", "workflows")

# Workflow names shared between the ordered tests below
WORKFLOWS = {}


@pytest.fixture(scope="module")
def juju(request: pytest.FixtureRequest):
    """Use the model given by --model (created if missing) or a temporary one."""
    model = request.config.getoption("--model")
    keep = bool(request.config.getoption("--keep-models"))
    if model:
        juju = jubilant.Juju()
        try:
            juju.add_model(model)
        except jubilant.CLIError:
            juju = jubilant.Juju(model=model)
        yield juju
        _dump_debug_log(request, juju)
        if not keep:
            juju.destroy_model(model, destroy_storage=True, force=True)
    else:
        with jubilant.temp_model(keep=keep) as juju:
            yield juju
            _dump_debug_log(request, juju)


def _dump_debug_log(request: pytest.FixtureRequest, juju: jubilant.Juju):
    if request.session.testsfailed:
        print(juju.debug_log(limit=1000), end="")


@pytest.fixture(scope="module")
def charm_path(request: pytest.FixtureRequest) -> Path:
    if path := request.config.getoption("--charm-path"):
        return Path(path).resolve()
    subprocess.run(["charmcraft", "pack"], cwd=CHARM_ROOT, check=True)
    return next(CHARM_ROOT.glob(f"{APP}_*.charm")).resolve()


@pytest.fixture(scope="module")
def client() -> lightkube.Client:
    return lightkube.Client(field_manager=APP)


def grant_executor_rbac(client: lightkube.Client, namespace: str):
    """Allow the workflow's service account to report step outputs.

    See https://argo-workflows.readthedocs.io/en/latest/workflow-rbac/
    """
    metadata = ObjectMeta(name="argo-executor", namespace=namespace)
    client.apply(
        Role(
            metadata=metadata,
            rules=[
                PolicyRule(
                    apiGroups=["argoproj.io"],
                    resources=["workflowtaskresults"],
                    verbs=["create", "patch"],
                )
            ],
        )
    )
    client.apply(
        RoleBinding(
            metadata=metadata,
            roleRef=RoleRef(
                apiGroup="rbac.authorization.k8s.io", kind="Role", name="argo-executor"
            ),
            subjects=[Subject(kind="ServiceAccount", name="default", namespace=namespace)],
        )
    )


def create_artifact_bucket(client: lightkube.Client, namespace: str):
    """Create the `mlpipeline` bucket in minio, Argo fails if it does not exist."""
    url = f"http://minio.{namespace}.svc.cluster.local:9000"
    command = (
        f"mc alias set storage {url} {MINIO.config['access-key']} {MINIO.config['secret-key']}"
        " && mc mb storage/mlpipeline -p"
    )
    pod = Pod(
        metadata=ObjectMeta(name="create-artifact-bucket", namespace=namespace),
        spec=PodSpec(
            restartPolicy="Never",
            containers=[
                Container(
                    name="mc", image=MINIO_CLIENT_IMAGE, command=["sh", "-c"], args=[command]
                )
            ],
        ),
    )
    client.create(pod)
    try:
        phase = wait_for(
            lambda: client.get(Pod, pod.metadata.name, namespace=namespace).status.phase,
            until=lambda phase: phase in ("Succeeded", "Failed"),
            timeout=WORKFLOW_TIMEOUT,
        )
        assert phase == "Succeeded", f"Bucket creation pod ended in phase {phase}"
    finally:
        client.delete(Pod, pod.metadata.name, namespace=namespace)


def wait_for(get, until, timeout: int):
    """Poll `get()` every 5s until `until(result)` is true or timeout, return the last result."""
    deadline = time.monotonic() + timeout
    while not until(result := get()) and time.monotonic() < deadline:
        time.sleep(5)
    return result


def workflow_phase(client: lightkube.Client, name: str, namespace: str) -> str:
    status = client.get(Workflow, name, namespace=namespace).status or {}
    return status.get("phase", "")


def submit_workflow(client: lightkube.Client, path: str, namespace: str) -> str:
    workflow = codecs.load_all_yaml(Path(path).read_text())[0]
    created = client.create(workflow, namespace=namespace)
    log.info(f"Submitted workflow {created.metadata.name}")
    return created.metadata.name


def wait_for_workflow(client: lightkube.Client, name: str, namespace: str, phases: tuple) -> str:
    return wait_for(
        lambda: workflow_phase(client, name, namespace),
        until=lambda phase: phase in phases,
        timeout=WORKFLOW_TIMEOUT,
    )


def assert_workflow_succeeds(client: lightkube.Client, name: str, namespace: str):
    phase = wait_for_workflow(client, name, namespace, ("Succeeded", "Failed", "Error"))
    assert phase == "Succeeded", f"Workflow {name} ended in phase {phase!r}"


def test_deploy_previous(juju: jubilant.Juju):
    juju.deploy(
        ARGO_CONTROLLER_PREVIOUS.charm,
        channel=ARGO_CONTROLLER_PREVIOUS.channel,
        trust=ARGO_CONTROLLER_PREVIOUS.trust,
    )
    juju.deploy(MINIO.charm, channel=MINIO.channel, config=MINIO.config, trust=MINIO.trust)
    juju.integrate(f"{APP}:object-storage", f"{MINIO.charm}:object-storage")
    juju.wait(
        lambda status: jubilant.all_active(status, APP, MINIO.charm),
        error=jubilant.any_error,
        timeout=60 * 10,
    )


def test_workflows_before_upgrade(juju: jubilant.Juju, client: lightkube.Client):
    create_artifact_bucket(client, juju.model)
    grant_executor_rbac(client, juju.model)

    name = submit_workflow(client, "tests/data/simple_artifact.yaml", juju.model)
    assert_workflow_succeeds(client, name, juju.model)

    # Left running across the refresh, checked in test_inflight_workflow_succeeds
    name = submit_workflow(client, "tests/data/long_running.yaml", juju.model)
    phase = wait_for_workflow(client, name, juju.model, ("Running", "Failed", "Error"))
    assert phase == "Running", f"Workflow {name} ended in phase {phase!r}"
    WORKFLOWS["in-flight"] = name


def test_upgrade(juju: jubilant.Juju, client: lightkube.Client, charm_path: Path):
    phase = workflow_phase(client, WORKFLOWS["in-flight"], juju.model)
    WORKFLOWS["in-flight-before-refresh"] = phase
    log.info(f"Refreshing {APP} to {charm_path} with {IMAGE}")
    juju.refresh(APP, path=charm_path, resources={"oci-image": IMAGE})
    juju.wait(
        lambda status: status.apps[APP].charm_origin == "local"
        and jubilant.all_active(status, APP, MINIO.charm)
        and jubilant.all_agents_idle(status, APP),
        error=jubilant.any_error,
        timeout=60 * 10,
    )


def test_upgraded_resources(juju: jubilant.Juju, client: lightkube.Client):
    pod = client.get(Pod, f"{APP}-0", namespace=juju.model)
    container = next(c for c in pod.spec.containers if c.name == APP)
    assert container.image.endswith(IMAGE.split("/", 1)[-1]), container.image

    crd = client.get(CustomResourceDefinition, "workflowtasksets.argoproj.io")
    schema = crd.spec.versions[0].schema.openAPIV3Schema
    # Nested schema properties are plain dicts in lightkube models
    assert schema.properties["spec"].get(
        "properties"
    ), "workflowtasksets CRD is not the full v4 schema"

    configmap = client.get(ConfigMap, CONFIGMAP_NAME, namespace=juju.model)
    assert "workflowDefaults" in configmap.data, configmap.data

    plan = juju.exec(
        f"PEBBLE_SOCKET=/charm/containers/{APP}/pebble.socket /charm/bin/pebble plan",
        unit=f"{APP}/0",
    ).stdout
    assert "TRANSIENT_ERROR_PATTERN" in plan, plan


def test_inflight_workflow_succeeds(juju: jubilant.Juju, client: lightkube.Client):
    if WORKFLOWS["in-flight-before-refresh"] != "Running":
        log.warning(
            f"Workflow {WORKFLOWS['in-flight']} was already "
            f"{WORKFLOWS['in-flight-before-refresh']!r} before the refresh, "
            "the in-flight check is not meaningful"
        )
    assert_workflow_succeeds(client, WORKFLOWS["in-flight"], juju.model)


def test_workflow_after_upgrade(juju: jubilant.Juju, client: lightkube.Client):
    name = submit_workflow(client, "tests/data/simple_artifact.yaml", juju.model)
    assert_workflow_succeeds(client, name, juju.model)

    pods = client.list(Pod, namespace=juju.model, labels={"workflows.argoproj.io/workflow": name})
    executor_images = {
        c.image
        for pod in pods
        for c in (pod.spec.initContainers or []) + pod.spec.containers
        if c.name in ("init", "wait")
    }
    assert executor_images == {EXECUTOR_IMAGE}, executor_images
