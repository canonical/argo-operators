## How to Manage Python Dependencies and Environments


### Prerequisites

`tox` is the only tool required locally, as `tox` internally installs and uses `poetry`, be it to manage Python dependencies or to run `tox` environments. To install it: `pipx install tox`.

Optionally, `poerty` can be additionally installed independently just for the sake of running Python commands locally outside of `tox` during debugging/development. To install it: `pipx install poetry`.


### Updating Dependencies

To add/update/remove any dependencies and/or to upgrade Python, simply:

1. add/update/remove such dependencies to/in/from the desired group(s) below `[tool.poetry.group.<your-group>.dependencies]` in `pyproject.toml`, and/or upgrade Python itself in `requires-python` under `[project]`

    _⚠️ dependencies for the charm itself are also defined as dependencies of a dedicated group called `charm`, specifically below `[tool.poetry.group.charm.dependencies]`, and not as project dependencies below `[project.dependencies]` or `[tool.poetry.dependencies]` ⚠️_

2. run `tox -e update-requirements` to update the lock file

    by this point, `poerty`, through `tox`, will let you know if there are any dependency conflicts to solve.

3. optionally, if you also want to update your local environment for running Python commands/scripts yourself and not through tox, see [Running Python Environments](#running-python-environments) below


### Running `tox` Environments

To run `tox` environments, either locally for development or in CI workflows for testing, ensure to have `tox` installed first and then simply run your `tox` environments natively (e.g.: `tox -e lint`). `tox` will internally first install `poetry` and then rely on it to install and run its environments.


### Running Python Environments

To run Python commands locally for debugging/development from any environments built from any combinations of dependency groups without relying on `tox`:
1. ensure you have `poetry` installed
2. install any required dependency groups: `poetry install --only <your-group-a>,<your-group-b>` (or all groups, if you prefer: `poetry install --all-groups`)
3. run Python commands via poetry: `poetry run python3 <your-command>`


## Upgrading Argo Workflows

Argo is deployed as part of Kubeflow Pipelines, so the charm follows the Argo manifests that the
[Kubeflow community distribution](https://github.com/kubeflow/community-distribution) ships for Pipelines.

1. Build the Pipelines manifests for the current and the target release, e.g.:
    ```shell
    git -C community-distribution checkout <tag>
    kubectl kustomize community-distribution/applications/pipeline/upstream/env/platform-agnostic-multi-user > kfp-<tag>.yaml
    ```
2. Diff the two files and review the Argo objects (`argoproj.io` CRDs, `workflow-controller` Deployment and `workflow-controller-configmap`).
3. Apply the changes to the charm:
    * CRDs: replace the `argoproj.io` CRDs in `charms/argo-controller/src/templates/crds.yaml` and update the header comment.
      The `metacontroller.k8s.io` CRDs in that file are not part of the Argo upgrade.
    * ConfigMap: `charms/argo-controller/src/templates/minio_configmap.yaml.j2`. The artifact repository endpoint and
      credentials come from the object storage relation.
    * Controller args and environment: `charms/argo-controller/src/components/pebble_component.py`.
4. Bump the `workflow-controller` and `argoexec` rocks in [argo-workflows-rocks](https://github.com/canonical/argo-workflows-rocks)
   and update `oci-image` in `charms/argo-controller/metadata.yaml` and `executor-image` in `charms/argo-controller/config.yaml`.
5. Check the [Argo upgrading guide](https://argo-workflows.readthedocs.io/en/latest/upgrading/) for breaking changes.
6. Point `ARGO_CONTROLLER_PREVIOUS` in `charms/argo-controller/tests/integration/charms_dependencies.py` to the
   previous stable track, so `tox -e integration-upgrade` tests the upgrade from it.
