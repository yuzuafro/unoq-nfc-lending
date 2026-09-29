#!/bin/sh
# Run the tests in a throwaway container built from the App base image, so the
# running App is not touched. Usage: sh tests/run.sh [pytest args]
set -e
cd "$(dirname "$0")/.."
docker build -q -t nfc-sample-test -f - python >/dev/null <<'DOCKERFILE'
FROM ghcr.io/arduino/app-bricks/python-apps-base:0.12.0
USER root
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt pytest
DOCKERFILE
docker run --rm -u "$(id -u):$(id -g)" -e PYTHONDONTWRITEBYTECODE=1 -v "$PWD":/src -w /src \
  --entrypoint python3 nfc-sample-test -m pytest -q -p no:cacheprovider -W ignore::DeprecationWarning tests "$@"
