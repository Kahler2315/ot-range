"""Tests for the attacker scope guard.

This is the mechanism that stops range tooling being pointed at real
equipment, so it is tested harder than anything else in the repo —
including deliberate attempts to configure around it.
"""

from __future__ import annotations

import ipaddress

import pytest

from attacker.common import scope
from attacker.common.scope import ScopeViolation, resolve_target

LOOPBACK_ONLY = ["127.0.0.0/8"]


@pytest.fixture
def scope_file(tmp_path):
    def _write(networks):
        path = tmp_path / "scope.yml"
        body = "allowed_networks:\n" + "".join(f"  - {n}\n" for n in networks)
        path.write_text(body)
        return path

    return _write


def test_loopback_target_allowed(scope_file):
    target = resolve_target("127.0.0.1", 5502, scope_file(LOOPBACK_ONLY))
    assert target.ip == ipaddress.ip_address("127.0.0.1")
    assert target.connect_host == "127.0.0.1"
    assert target.port == 5502


def test_public_address_refused(scope_file):
    with pytest.raises(ScopeViolation, match="outside the configured range"):
        resolve_target("8.8.8.8", 502, scope_file(LOOPBACK_ONLY))


def test_public_address_refused_even_when_configured(scope_file, tmp_path):
    """The structural ceiling is not configurable — this is the key property."""
    path = tmp_path / "scope.yml"
    path.write_text("allowed_networks:\n  - 8.8.8.8/32\n")
    with pytest.raises(ScopeViolation, match="not configurable"):
        resolve_target("8.8.8.8", 502, path)


def test_whole_internet_in_config_refused(scope_file, tmp_path):
    path = tmp_path / "scope.yml"
    path.write_text("allowed_networks:\n  - 0.0.0.0/0\n")
    with pytest.raises(ScopeViolation, match="not configurable"):
        resolve_target("127.0.0.1", 5502, path)


def test_private_address_outside_configured_subset_refused(scope_file):
    """RFC 1918 is structurally allowed but must still be in the config."""
    with pytest.raises(ScopeViolation, match="outside the configured range"):
        resolve_target("10.1.2.3", 502, scope_file(LOOPBACK_ONLY))


def test_private_address_allowed_when_configured(scope_file):
    target = resolve_target("10.1.2.3", 502, scope_file(["10.0.0.0/8"]))
    assert target.ip == ipaddress.ip_address("10.1.2.3")


def test_rfc5737_documentation_range_allowed(scope_file):
    target = resolve_target("192.0.2.10", 502, scope_file(["192.0.2.0/24"]))
    assert target.ip == ipaddress.ip_address("192.0.2.10")


def test_ipv6_loopback_allowed(scope_file):
    target = resolve_target("::1", 502, scope_file(["::1/128"]))
    assert target.ip == ipaddress.ip_address("::1")


def test_ipv6_public_refused(scope_file):
    with pytest.raises(ScopeViolation):
        resolve_target("2001:4860:4860::8888", 502, scope_file(["::1/128"]))


def test_missing_config_refuses(tmp_path):
    with pytest.raises(ScopeViolation, match="scope config not found"):
        resolve_target("127.0.0.1", 5502, tmp_path / "does-not-exist.yml")


def test_empty_config_refuses(tmp_path):
    path = tmp_path / "scope.yml"
    path.write_text("allowed_networks: []\n")
    with pytest.raises(ScopeViolation, match="no 'allowed_networks'"):
        resolve_target("127.0.0.1", 5502, path)


def test_malformed_yaml_refuses(tmp_path):
    path = tmp_path / "scope.yml"
    path.write_text("allowed_networks: [unclosed\n")
    with pytest.raises(ScopeViolation, match="not valid YAML"):
        resolve_target("127.0.0.1", 5502, path)


def test_garbage_network_entry_refuses(tmp_path):
    path = tmp_path / "scope.yml"
    path.write_text("allowed_networks:\n  - not-a-network\n")
    with pytest.raises(ScopeViolation, match="invalid network"):
        resolve_target("127.0.0.1", 5502, path)


def test_hostname_resolving_out_of_scope_refused(scope_file, monkeypatch):
    monkeypatch.setattr(
        scope,
        "_resolve_all",
        lambda host, port: [ipaddress.ip_address("93.184.216.34")],
    )
    with pytest.raises(ScopeViolation, match="outside the configured range"):
        resolve_target("range.example", 502, scope_file(LOOPBACK_ONLY))


def test_hostname_with_any_out_of_scope_address_refused(scope_file, monkeypatch):
    """A name resolving to both in- and out-of-scope addresses is refused outright."""
    monkeypatch.setattr(
        scope,
        "_resolve_all",
        lambda host, port: [
            ipaddress.ip_address("127.0.0.1"),
            ipaddress.ip_address("93.184.216.34"),
        ],
    )
    with pytest.raises(ScopeViolation, match="outside the configured range"):
        resolve_target("split-horizon.example", 502, scope_file(LOOPBACK_ONLY))


def test_unresolvable_hostname_refused(scope_file, monkeypatch):
    def _boom(host, port):
        raise ScopeViolation("could not resolve target host 'nope.invalid'")

    monkeypatch.setattr(scope, "_resolve_all", _boom)
    with pytest.raises(ScopeViolation, match="could not resolve"):
        resolve_target("nope.invalid", 502, scope_file(LOOPBACK_ONLY))


def test_target_connects_to_checked_address_not_hostname(scope_file, monkeypatch):
    """Callers must connect to the address that was checked, closing the
    re-resolution gap a second DNS lookup would open."""
    monkeypatch.setattr(
        scope,
        "_resolve_all",
        lambda host, port: [ipaddress.ip_address("127.0.0.5")],
    )
    target = resolve_target("range.local", 5502, scope_file(LOOPBACK_ONLY))
    assert target.connect_host == "127.0.0.5"
    assert target.host == "range.local"


def test_shipped_scope_config_is_within_ceiling():
    """The config committed to the repo must itself be in-range."""
    networks = scope._load_configured_networks(scope.DEFAULT_SCOPE_PATH)
    assert networks
    for network in networks:
        assert scope._within_structural_ceiling(network)


def test_banner_states_simulated_environment(scope_file):
    target = resolve_target("127.0.0.1", 5502, scope_file(LOOPBACK_ONLY))
    banner = scope.scope_banner(target, "S03 — unauthorised command")
    assert "SIMULATED" in banner
    assert "127.0.0.1:5502" in banner
    assert "S03" in banner


def test_guard_exits_nonzero_on_violation(scope_file, capsys):
    with pytest.raises(SystemExit) as excinfo:
        scope.guard("8.8.8.8", 502, "S01 — recon", scope_file(LOOPBACK_ONLY))
    assert excinfo.value.code == 2
    assert "SCOPE GUARD" in capsys.readouterr().err
