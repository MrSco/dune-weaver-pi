# Dune Weaver host as a Home Assistant add-on (DW Mini fork).
#
# Home Assistant builds this from the add-on folder alone, so the app is
# fetched from GitHub at exactly this add-on version: tag addon-<version>
# (Home Assistant passes the version as BUILD_VERSION). The frontend is
# committed pre-built in static/dist, so no Node toolchain is needed.
# For testing uncommitted changes, deploy.sh uses Dockerfile.local instead.
FROM python:3.11-slim-bookworm

ARG BUILD_VERSION
ARG DW_REPO=https://github.com/MrSco/dune-weaver-pi

RUN apt-get update \
 && apt-get install -y --no-install-recommends nginx tzdata curl git ca-certificates \
 && rm -rf /var/lib/apt/lists/* /etc/nginx/sites-enabled/default

RUN git clone --depth 1 --branch "addon-${BUILD_VERSION}" "${DW_REPO}" /src \
 && mkdir -p /app \
 && cd /src \
 && cp -r main.py VERSION requirements-nonrpi.txt modules static templates patterns /app/ \
 && cp dune_weaver/nginx.conf /etc/nginx/conf.d/dune-weaver.conf \
 && cp dune_weaver/run.sh /run.sh \
 && chmod +x /run.sh \
 && cd / && rm -rf /src \
 && pip install --no-cache-dir -r /app/requirements-nonrpi.txt

ENV PYTHONUNBUFFERED=1 \
    DW_DISABLE_SELF_UPDATE=1 \
    DW_PATTERN_CACHE=1 \
    DW_CLEAR_VARIANT=mini \
    DW_FIRMWARE_REPO=MrSco/dune-weaver-firmware

CMD ["/run.sh"]
