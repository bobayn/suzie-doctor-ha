FROM ghcr.io/home-assistant/base:latest

ARG BUILD_VERSION="0.2.1-dev"
ARG BUILD_ARCH="aarch64"

RUN apk add --no-cache python3 py3-pip && \
    apk add --no-cache --virtual .build-deps build-base python3-dev musl-dev && \
    python3 -m venv /opt/suzie-doctor-venv && \
    /opt/suzie-doctor-venv/bin/pip install --no-cache-dir aiohttp==3.12.15 PyYAML==6.0.2 && \
    apk del .build-deps

COPY rootfs /
COPY run.sh /run.sh
RUN chmod +x /run.sh

LABEL io.hass.version="${BUILD_VERSION}" \
      io.hass.type="app" \
      io.hass.arch="${BUILD_ARCH}"

CMD ["/run.sh"]
