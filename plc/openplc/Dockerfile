# OpenPLC Runtime, built from source and pinned to a specific commit.
#
# No official image exists on Docker Hub with any real adoption (checked:
# every "openplc" image found had 0-1 stars, unknown maintenance status).
# Pulling one of those would be an unverified third-party binary running
# the PLC — directly against this repo's own sanitization standard of
# pinned, known-provenance dependencies. Building from the upstream
# source at a pinned commit keeps that guarantee.
#
# Pin update: bump OPENPLC_COMMIT deliberately, re-verify docs/
# openplc-integration.md still matches the new version's Modbus mapping
# and webserver routes before trusting it.

FROM debian:trixie-20251020

ARG OPENPLC_COMMIT=b5d41356dab4aeadca0dd7ca64ba542f870b595d

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workdir

RUN git clone https://github.com/thiagoralves/OpenPLC_v3.git . \
    && git checkout "${OPENPLC_COMMIT}"

RUN mkdir /docker_persistent
VOLUME /docker_persistent

RUN ./install.sh docker \
    && touch /docker_persistent/mbconfig.cfg \
    && touch /docker_persistent/persistent.file \
    && mkdir /docker_persistent/st_files \
    && cp /workdir/webserver/openplc.db /docker_persistent/openplc.db \
    && mv /workdir/webserver/openplc.db /workdir/webserver/openplc_default.db \
    && cp /workdir/webserver/dnp3.cfg /docker_persistent/dnp3.cfg \
    && mv /workdir/webserver/dnp3.cfg /workdir/webserver/dnp3_default.cfg \
    && cp -r /workdir/webserver/st_files/ /docker_persistent/st_files/ \
    && mv /workdir/webserver/st_files /workdir/webserver/st_files_default \
    && cp /workdir/webserver/active_program /docker_persistent/active_program \
    && mv /workdir/webserver/active_program /workdir/webserver/active_program_default \
    && ln -s /docker_persistent/mbconfig.cfg /workdir/webserver/mbconfig.cfg \
    && ln -s /docker_persistent/persistent.file /workdir/webserver/persistent.file \
    && ln -s /docker_persistent/openplc.db /workdir/webserver/openplc.db \
    && ln -s /docker_persistent/dnp3.cfg /workdir/webserver/dnp3.cfg \
    && ln -s /docker_persistent/st_files /workdir/webserver/st_files \
    && ln -s /docker_persistent/active_program /workdir/webserver/active_program

EXPOSE 8080 502

ENTRYPOINT ["./start_openplc.sh"]
