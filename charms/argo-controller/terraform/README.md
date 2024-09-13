# Terraform module for argo-controller

This is a Terraform module facilitating the deployment of argo-controller charm, using the [Terraform juju provider](https://github.com/juju/terraform-provider-juju/). For more information, refer to the provider [documentation](https://registry.terraform.io/providers/juju/juju/latest/docs). 

## Requirements
This module requires a `juju` model to be available. Refer to the [usage section](#usage) below for more details.

## API

### Inputs
The module offers the following configurable inputs:

| Name | Type | Description | Required |
| - | - | - | - |
| `app_name`| string | Application name | False |
| `channel`| string | Channel that the charm is deployed from | False |
| `config`| map(string) | Map of the charm configuration options | False |
| `model_name`| string | Name of the model that the charm is deployed on | True |
| `resources`| map(number) | Map of charm resources revisions | False |
| `revision`| number | Revision number of the charm name | False |

### Outputs
Upon applied, the module exports the following outputs:

| Name | Description |
| - | - |
| `app_name`|  Application name |
| `provides`| Map of `provides` endpoints |
| `requires`|  Map of `reqruires` endpoints |

## Usage

### Integrating in a higher-level module
In order to use this module in a higher-level module, ensure that Terraform is aware of its `juju_model` dependency by passing to the `model_name` input  a reference to the `juju_model` resource's name. For example:

```
resource "juju_model" "testing" {
  name = kubeflow
}

module "argo-controller" {
  source = "<path-to-this-directory>"
  model_name = juju_model.testing.name
}
```

### Applying directly the module from the CLI
Although not recommended, in order to apply the module directly from the CLI, ensure that a `juju` model has already been created and then manually use its name as the value of the `model_name` input. For example:
```
# check that there is a model called `kubeflow`
terraform apply -var "model_name=kubeflow"
```
