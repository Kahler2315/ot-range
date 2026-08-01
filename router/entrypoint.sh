#!/bin/bash
# Cedar Hollow M4 router/sensor entrypoint.
# bash, not sh/dash: `wait -n` below (waiting on whichever of the three
# background processes exits first) is a bash extension.
#
# Three things run in this one container, all watching the same wire:
#   1. sensor/tap.py — the relay itself (attacker -> router -> openplc).
#      Already-tested code from the M1 loopback stack, reused unchanged.
#   2. Real Zeek, sniffing the zone-enterprise-facing interface — genuine
#      libpcap capture, not tap.py's own synthetic log.
#   3. Suricata, same interface, IDS mode (observe only, never blocks).
#
# One container running multiple long-lived processes is a deliberate
# exception to the one-process-per-container norm used everywhere else
# in this repo: a router/sensor appliance genuinely *is* one thing, and
# real network appliances commonly run several daemons against the same
# interface. Not a pattern to copy for ordinary services.
set -eu

UPSTREAM_HOST="${UPSTREAM_HOST:?UPSTREAM_HOST required}"
UPSTREAM_PORT="${UPSTREAM_PORT:-502}"
RELAY_PORT="${RELAY_PORT:-502}"
ZONE_ENTERPRISE_SUBNET="${ZONE_ENTERPRISE_SUBNET:-10.20.0.0/24}"

# Found by which interface actually holds a zone-enterprise address,
# not assumed to be eth0 — Docker Compose does not guarantee interface
# naming matches the order networks are declared in the compose file.
ENTERPRISE_IFACE="$(python3 /app/router/find_iface.py "$ZONE_ENTERPRISE_SUBNET" || true)"
if [ -z "$ENTERPRISE_IFACE" ]; then
    echo "router: could not find an interface on $ZONE_ENTERPRISE_SUBNET" >&2
    ip -4 addr show >&2
    exit 1
fi
echo "router: zone-enterprise interface is $ENTERPRISE_IFACE"

mkdir -p /zeek-logs
cd /zeek-logs

python3 -m sensor.tap \
    --listen-host 0.0.0.0 --listen-port "$RELAY_PORT" \
    --upstream-host "$UPSTREAM_HOST" --upstream-port "$UPSTREAM_PORT" \
    --log /zeek-logs/tap-relay.log &
TAP_PID=$!

# -C: Docker's veth pairs don't do real TCP checksum computation
# (offloaded to hardware on a real NIC, skipped entirely for
# virtual/intra-host links), so every packet looks checksum-invalid to
# Zeek's default validation. Real traffic, not evidence of tampering —
# see /zeek-logs/reporter.log if this is ever in doubt.
zeek -C -i "$ENTERPRISE_IFACE" local &
ZEEK_PID=$!

# Modbus app-layer detection is off by default in stock suricata.yaml
# (perf/false-positive caution for a protocol most deployments don't
# run) — this range's whole point is Modbus, so turn it on.
suricata -i "$ENTERPRISE_IFACE" -c /etc/suricata/suricata.yaml \
    --set app-layer.protocols.modbus.enabled=yes -l /zeek-logs &
SURICATA_PID=$!

trap 'kill $TAP_PID $ZEEK_PID $SURICATA_PID 2>/dev/null' TERM INT

wait -n "$TAP_PID" "$ZEEK_PID" "$SURICATA_PID"
echo "router: one of tap/zeek/suricata exited, shutting down" >&2
kill $TAP_PID $ZEEK_PID $SURICATA_PID 2>/dev/null || true
wait
