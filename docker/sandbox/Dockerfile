FROM node:22-slim@sha256:9c2c405e3ff9b9afb2873232d24bb06367d649aa3e6259cbe314da59578e81e9 AS node-base

FROM python:3.14.3-slim@sha256:6a27522252aef8432841f224d9baaa6e9fce07b07584154fa0b9a96603af7456

COPY --from=node-base /usr/local/bin/node /usr/local/bin/node
COPY --from=node-base /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm

# git version tracks base image; updated via Dependabot base image bumps
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin sandbox \
    && mkdir -p /workspace \
    && chown sandbox:sandbox /workspace

WORKDIR /workspace

USER sandbox

CMD ["bash"]
