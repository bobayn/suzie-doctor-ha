FROM ghcr.io/home-assistant/base:latest

ARG BUILD_VERSION="0.2.48-dev"
ARG BUILD_ARCH="aarch64"

RUN apk add --no-cache python3 py3-aiohttp py3-yaml py3-cryptography

COPY rootfs /
COPY run.sh /run.sh
RUN chmod +x /run.sh

LABEL io.hass.version="${BUILD_VERSION}" \
      io.hass.type="app" \
      io.hass.arch="${BUILD_ARCH}"

CMD ["/run.sh"]
