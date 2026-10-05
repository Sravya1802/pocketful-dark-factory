#!/bin/sh
# Docker Desktop's VM clock lags the host while the Mac idles (we measured a 4.5 minute lag), which breaks any oracle that compares
# server timestamps with the host clock. Run this (needs a local image with busybox nsenter, e.g. the service image) before attacks.
# usage: resync_docker_clock.sh IMAGE
docker run --rm --privileged --pid=host --user root --entrypoint nsenter "$1" -t 1 -m -u -n -i date -u -s "@$(date +%s)" >/dev/null 2>&1
echo "host: $(date -u)"; echo "vm:   $(docker run --rm --entrypoint date "$1" -u)"
