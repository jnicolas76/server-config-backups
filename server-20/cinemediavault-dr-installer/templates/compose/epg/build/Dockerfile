# CineMediaVault EPG collector image.
#
# The upstream iptv-org/epg project is cloned at build time rather than
# vendored into this package: it is a large, frequently updated third-party
# codebase with its own licence, and cloning keeps the provenance obvious.
FROM node:22-alpine

ARG GIT_REPO=https://github.com/iptv-org/epg.git
ARG GIT_BRANCH=master
ARG WORKDIR=/epg

ENV CRON_SCHEDULE="0 3 * * *"
ENV RUN_AT_STARTUP=true

RUN apk update \
    && apk upgrade --available \
    && apk add --no-cache curl git tzdata bash \
    && npm install --global pm2 \
    && mkdir -p "${WORKDIR}" \
    && cd "${WORKDIR}" \
    && git clone --depth 1 -b "${GIT_BRANCH}" "${GIT_REPO}" . \
    && npm install \
    && mkdir -p /public

# Build tools are not needed at runtime.
RUN apk del git curl && rm -rf /var/cache/apk/*

COPY pm2.config.js ${WORKDIR}/

# CineMediaVault overlay. The upstream tvpassport parser drops three attributes
# the DVR relies on - the "new episode" flag, the episode number and the year -
# so a wrapper re-attaches them. The untouched upstream config is preserved as
# tvpassport.com.base.js and does all the real work; the wrapper is fail-safe
# and leaves the upstream result alone if anything about the page changes.
COPY tvpassport-enrich.config.js ${WORKDIR}/sites/tvpassport.com/tvpassport.com.enrich.js
RUN cd ${WORKDIR}/sites/tvpassport.com \
    && cp tvpassport.com.config.js tvpassport.com.base.js \
    && cp tvpassport.com.enrich.js tvpassport.com.config.js \
    && rm tvpassport.com.enrich.js

WORKDIR ${WORKDIR}
EXPOSE 3000
CMD ["pm2-runtime", "pm2.config.js"]
