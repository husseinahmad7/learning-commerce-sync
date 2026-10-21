#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
mkdir -p .wheelhouse
podman run --rm --userns=keep-id --user "$(id -u):$(id -g)" \
  --read-only --cap-drop=all --security-opt=no-new-privileges \
  --tmpfs /tmp:size=64m,mode=1777,nosuid,nodev,noexec \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD/requirements.lock:/requirements.lock:ro" \
  -v "$PWD/.wheelhouse:/wheels:rw" \
  docker.io/library/python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f \
  python -m pip download --index-url https://pypi.org/simple --no-cache-dir \
    --require-hashes --only-binary=:all: -r /requirements.lock --dest /wheels
