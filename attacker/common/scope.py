"""Scope guard for attacker tooling.

Every script under `attacker/` must resolve its target through
`resolve_target()` before opening a socket. Targets outside the range's
own address space are refused, and the refusal cannot be configured away:
globally-routable addresses are rejected structurally, not by policy.

Two layers:

1. **Structural ceiling (not configurable).** Only loopback, RFC 1918
   private, RFC 5737 documentation, and IPv6 unique-local/loopback
   addresses can ever be targeted. Editing `attacker/scope.yml` to point
   at a real utility does not work — a public address is refused no
   matter what the config says.
2. **Configured allowlist (narrows further).** `attacker/scope.yml` lists
   the CIDRs this particular range deployment actually uses. It can only
   narrow what layer 1 already permits, never widen it.

Hostnames are resolved *before* the check, every resolved address must
pass, and the checked address is what the caller connects to — so a name
that resolves to both a loopback and a public address is refused rather
than raced, and there is no window for a second lookup to return
something different.
"""

from __future__ import annotations

import dataclasses
import ipaddress
import socket
import sys
from pathlib import Path

import yaml

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

DEFAULT_SCOPE_PATH = Path(__file__).resolve().parent.parent / "scope.yml"

# Layer 1. Nothing outside these ranges is ever a permissible target,
# regardless of configuration. Keep this list conservative — every entry
# is a range that cannot route to the public internet.
STRUCTURAL_ALLOWED: tuple[IPNetwork, ...] = (
    ipaddress.ip_network("127.0.0.0/8"),  # loopback
    ipaddress.ip_network("10.0.0.0/8"),  # RFC 1918
    ipaddress.ip_network("172.16.0.0/12"),  # RFC 1918 (includes Docker default bridge)
    ipaddress.ip_network("192.168.0.0/16"),  # RFC 1918
    ipaddress.ip_network("192.0.2.0/24"),  # RFC 5737 TEST-NET-1
    ipaddress.ip_network("198.51.100.0/24"),  # RFC 5737 TEST-NET-2
    ipaddress.ip_network("203.0.113.0/24"),  # RFC 5737 TEST-NET-3
    ipaddress.ip_network("::1/128"),  # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),  # IPv6 unique local
)


class ScopeViolation(Exception):
    """Raised when a target is outside the permitted range address space."""


@dataclasses.dataclass(frozen=True)
class Target:
    """A target that has passed the scope guard.

    `ip` is the address that was actually checked. Connect to this, not to
    the original hostname — re-resolving would reopen the gap the check
    just closed.
    """

    host: str
    ip: IPAddress
    port: int

    @property
    def connect_host(self) -> str:
        return str(self.ip)


def _load_configured_networks(scope_path: Path) -> list[IPNetwork]:
    if not scope_path.is_file():
        raise ScopeViolation(
            f"scope config not found at {scope_path}. Attacker tooling refuses to run "
            "without an explicit in-range target definition."
        )
    try:
        data = yaml.safe_load(scope_path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ScopeViolation(f"scope config at {scope_path} is not valid YAML: {exc}") from exc

    raw = data.get("allowed_networks")
    if not raw:
        raise ScopeViolation(
            f"scope config at {scope_path} defines no 'allowed_networks'. Refusing to run."
        )

    networks: list[IPNetwork] = []
    for entry in raw:
        try:
            network = ipaddress.ip_network(str(entry), strict=False)
        except ValueError as exc:
            raise ScopeViolation(f"invalid network {entry!r} in {scope_path}: {exc}") from exc
        if not _within_structural_ceiling(network):
            raise ScopeViolation(
                f"network {network} in {scope_path} is outside the permitted range address "
                "space. Attacker tooling is hard-bound to loopback, RFC 1918, and RFC 5737 "
                "documentation ranges. This limit is not configurable."
            )
        networks.append(network)
    return networks


def _within_structural_ceiling(network: IPNetwork) -> bool:
    return any(
        network.version == allowed.version and network.subnet_of(allowed)
        for allowed in STRUCTURAL_ALLOWED
    )


def _address_permitted(ip: IPAddress, configured: list[IPNetwork]) -> bool:
    in_ceiling = any(ip in allowed for allowed in STRUCTURAL_ALLOWED)
    in_config = any(ip in network for network in configured)
    return in_ceiling and in_config


def _resolve_all(host: str, port: int) -> list[IPAddress]:
    """Resolve a host to every address it maps to.

    A literal IP is returned as-is. A name that resolves to several
    addresses returns all of them, so the caller can require that *every*
    one is in scope.
    """
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ScopeViolation(f"could not resolve target host {host!r}: {exc}") from exc

    addresses: list[IPAddress] = []
    for info in infos:
        sockaddr = info[4]
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            continue
        if ip not in addresses:
            addresses.append(ip)

    if not addresses:
        raise ScopeViolation(f"target host {host!r} resolved to no usable addresses")
    return addresses


def resolve_target(host: str, port: int, scope_path: Path | str = DEFAULT_SCOPE_PATH) -> Target:
    """Resolve and scope-check a target. Raises ScopeViolation if out of range.

    Fails closed: any resolution failure, config problem, or single
    out-of-scope address among several refuses the whole target.
    """
    configured = _load_configured_networks(Path(scope_path))
    addresses = _resolve_all(host, port)

    for ip in addresses:
        if not _address_permitted(ip, configured):
            raise ScopeViolation(
                f"REFUSING TARGET {host}:{port} — resolved address {ip} is outside the "
                "configured range address space.\n"
                "This tooling only ever runs against the simulated range. If you are "
                "trying to point it at real equipment, stop."
            )

    return Target(host=host, ip=addresses[0], port=port)


def scope_banner(target: Target, scenario: str) -> str:
    return (
        "\n"
        "============================================================\n"
        f" OT RANGE — {scenario}\n"
        "------------------------------------------------------------\n"
        f" target        : {target.host}:{target.port} (resolved {target.ip})\n"
        " scope         : simulated range address space only\n"
        " authorisation : this is a SIMULATED environment. Never point\n"
        "                 this tooling at real equipment or any network\n"
        "                 you do not own and control.\n"
        "============================================================\n"
    )


def guard(
    host: str, port: int, scenario: str, scope_path: Path | str = DEFAULT_SCOPE_PATH
) -> Target:
    """Scope-check a target and print the banner, or exit non-zero.

    The entry point every attacker script should use.
    """
    try:
        target = resolve_target(host, port, scope_path)
    except ScopeViolation as exc:
        print(f"\n[SCOPE GUARD] {exc}\n", file=sys.stderr)
        raise SystemExit(2) from exc
    print(scope_banner(target, scenario))
    return target
