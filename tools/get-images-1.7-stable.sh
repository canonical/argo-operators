#!/bin/bash
#
# This script returns list of container images that are managed by this charm and/or its workload
#
# static list
STATIC_IMAGE_LIST=(
)
# dynamic list
git checkout origin/track/3.3
IMAGE_LIST=()
IMAGE_LIST+=($(find -type f -name metadata.yaml -exec yq '.resources | to_entries | .[] | .value | ."upstream-source"' {} \;))
VERSION=$(grep upstream-source charms/argo-controller/metadata.yaml | awk -F':' '{print $3}')
IMAGE_LIST+=($(grep "executor_image =" charms/argo-controller/src/charm.py | awk '{print $3}' | sed s/f//g | sed s/\"//g | sed s/{version}/$VERSION/g))

printf "%s\n" "${STATIC_IMAGE_LIST[@]}"
printf "%s\n" "${IMAGE_LIST[@]}"
