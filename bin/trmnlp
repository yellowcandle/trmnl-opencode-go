#!/usr/bin/env bash
# Runs trmnlp via the official Docker image (works without local Ruby 4+).
# See https://github.com/usetrmnl/trmnlp
set -euo pipefail
IMAGE="${TRMNLP_IMAGE:-trmnl/trmnlp}"
exec docker run --rm \
  --publish 4567:4567 \
  --volume "${HOME}/.config/trmnlp:/root/.config/trmnlp" \
  --volume "$(pwd):/plugin" \
  -e TRMNL_API_KEY \
  "$IMAGE" "$@"
