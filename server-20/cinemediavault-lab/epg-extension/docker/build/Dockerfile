FROM node:22-alpine
ARG GIT_REPO=https://github.com/iptv-org/epg.git
ARG GIT_BRANCH=master
ARG WORKDIR=/epg
ENV CRON_SCHEDULE="0 0 * * *"
ENV RUN_AT_STARTUP=true
RUN apk update \
    && apk upgrade --available \
    && apk add curl git tzdata bash \
    && npm install pm2 -g \
    && mkdir $(echo "${WORKDIR}") -p \
    && cd $(echo "${WORKDIR}") \
    && git clone --depth 1 -b $(echo "${GIT_BRANCH}") $(echo "${GIT_REPO}") . \
    && npm install \
    && mkdir /public
RUN apk del git curl \
  && rm -rf /var/cache/apk/*
COPY pm2.config.js $WORKDIR
# CineVault overlay: keep the upstream tvpassport config intact under a new name and
# load it through a wrapper that restores the new/episode/year fields the DVR needs.
COPY tvpassport-enrich.config.js $WORKDIR/sites/tvpassport.com/tvpassport.com.enrich.js
RUN cd $WORKDIR/sites/tvpassport.com \
    && cp tvpassport.com.config.js tvpassport.com.base.js \
    && cp tvpassport.com.enrich.js tvpassport.com.config.js \
    && rm tvpassport.com.enrich.js
WORKDIR $WORKDIR
EXPOSE 3000
CMD [ "pm2-runtime", "pm2.config.js" ]
