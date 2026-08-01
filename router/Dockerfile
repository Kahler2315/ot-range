# Cedar Hollow M4 router/sensor: an inline Modbus TCP relay (reuses
# sensor/tap.py, already proven on the loopback stack) sitting on a real
# Docker network boundary between zone-enterprise and zone-ops, with real
# Zeek and Suricata sniffing the wire — not a synthetic log emulation.
FROM debian:bookworm-slim

# hadolint: DL3008 (pin apt package versions) not applied here, matching
# plc/openplc/Dockerfile's existing convention of pinning at the image-tag
# level rather than per-package — Debian snapshot pinning is its own can
# of worms this project hasn't opened yet. DL4006 (pipefail before a
# piped RUN) doesn't apply: the pipe here is `curl | gpg --dearmor`, and
# a failed curl already fails the build via gpg's own empty-input error.
# suricata isn't in bookworm's main component, only bookworm-backports.
# hadolint ignore=DL3008,DL4006
RUN echo "deb http://deb.debian.org/debian bookworm-backports main" \
        > /etc/apt/sources.list.d/backports.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        bash ca-certificates curl gnupg iproute2 python3 \
    && apt-get install -y --no-install-recommends -t bookworm-backports suricata \
    && curl -fsSL https://download.opensuse.org/repositories/security:zeek/Debian_12/Release.key \
        | gpg --dearmor -o /usr/share/keyrings/zeek.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/zeek.gpg] https://download.opensuse.org/repositories/security:zeek/Debian_12/ /" \
        > /etc/apt/sources.list.d/zeek.list \
    && apt-get update && apt-get install -y --no-install-recommends zeek \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/opt/zeek/bin:${PATH}"
ENV PYTHONPATH=/app

WORKDIR /app
COPY sensor/ sensor/
COPY router/find_iface.py router/find_iface.py
COPY router/local.zeek /opt/zeek/share/zeek/site/local.zeek
COPY router/suricata.rules router/suricata.rules
# .rules files have no include directive — concatenate the shipped
# Modbus protocol-anomaly rules with ours into the path the stock
# suricata.yaml already expects (rule-files: [suricata.rules] under
# default-rule-path), so entrypoint.sh needs no -S override.
RUN mkdir -p /var/lib/suricata/rules \
    && cat /etc/suricata/rules/modbus-events.rules router/suricata.rules \
        > /var/lib/suricata/rules/suricata.rules
COPY router/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
