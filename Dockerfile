# syntax=docker/dockerfile:1
# Python/Node runtime only. Minecraft and model services stay outside this image.
FROM node:22-trixie-slim AS node-runtime

# FalkorDB Lite 0.10.0 Linux wheels require glibc >=2.39 (Trixie provides 2.41).
FROM python:3.12-slim-trixie AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MINECRAFT_PYTHON=/usr/local/bin/python
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates libstdc++6 libgomp1 \
        libcairo2 libpango-1.0-0 libpangocairo-1.0-0 \
        libjpeg62-turbo libgif7 librsvg2-2 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

FROM runtime AS dependencies
# Native dependencies support canvas fallback builds and embedded graph storage.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential pkg-config libcairo2-dev libpango1.0-dev \
        libjpeg-dev libgif-dev librsvg2-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --prefix=/install -r requirements.txt
COPY bot/package.json bot/package-lock.json ./bot/
RUN npm ci --prefix bot --omit=dev --no-audit --no-fund

FROM runtime AS agent
RUN groupadd --gid 10001 agent \
    && useradd --uid 10001 --gid agent --create-home agent
WORKDIR /app
COPY --from=dependencies /install/ /usr/local/
COPY --from=dependencies /build/bot/node_modules/ ./bot/node_modules/
COPY --chown=agent:agent agent/ ./agent/
COPY --chown=agent:agent bot/ ./bot/
COPY --chown=agent:agent shared/ ./shared/
COPY --chown=agent:agent main.py start.sh start-agent.sh requirements.txt ./
COPY --chown=agent:agent scripts/docker_healthcheck.py ./scripts/docker_healthcheck.py
COPY --chown=agent:agent scripts/__init__.py scripts/launch_checks.py scripts/runtime.sh ./scripts/
# Configuration arrives through Compose's env_file; never copy private .env files.
RUN touch .env && mkdir -p data \
    && chown agent:agent .env data \
    && sed -i 's/\r$//' start.sh start-agent.sh scripts/runtime.sh \
    && chmod +x start.sh start-agent.sh
USER agent
ENTRYPOINT ["/app/start-agent.sh"]
