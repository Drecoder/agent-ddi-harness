# The image every CI check runs in: the base pinned by digest, git pinned to an exact Debian
# version (the harness's tests drive real git repositories). Nothing else is installed: the
# harness has no dependencies. Tests run as a non-root user with no network.
FROM python:3.12-slim@sha256:6b25cbe6577518889d553cdd5a5da97d259348d8f6e45b527dd731caaba27a07

ARG GIT_VERSION
RUN test -n "$GIT_VERSION" || { echo "GIT_VERSION is required: pin git to an exact Debian version" >&2; exit 1; } \
 && apt-get update \
 && apt-get install -y --no-install-recommends "git=${GIT_VERSION}" \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 --create-home ci

USER 10001
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
WORKDIR /work
