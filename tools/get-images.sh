#!/bin/bash
#
# This script returns list of container images that are managed by this charm and/or its workload
#
IMAGE_LIST=()
IMAGE_LIST+=($(find -type f -name metadata.yaml -exec yq '.resources | to_entries | .[] | .value | ."upstream-source"' {} \;))
VERSION=$(grep upstream-source charms/argo-controller/metadata.yaml | awk -F':' '{print $3}')
IMAGE_LIST+=($(yq '.options.executor-image.default' ./charms/argo-controller/config.yaml))
printf "%s\n" "${IMAGE_LIST[@]}"

