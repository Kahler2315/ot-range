"""Attack → detection regression tests.

The project's headline claim is that every attack ships with a detection
that is proven to fire. These tests are that proof: each one stands up a
real simulator behind a real tap, runs the real attack script over a real
socket, and asserts the detection engine catches it.

Equally important is the negative case — a clean run must produce no
alerts at all. A detection that fires on everything is not a detection.

Slower than the rest of the suite (each test starts processes and drives a
physical process to a failure state), so they are marked `e2e`. Run just
these with `-m e2e`, or skip them with `-m "not e2e"`.
"""

from __future__ import annotations

import pytest

from process_sim.server import LT101_SPOOF_HR_INDEX
from sensor.detect import Baseline, Detector
from tests.harness import ATTACKER_SOURCE_IP, HMI_SOURCE_IP, running_range

pytestmark = pytest.mark.e2e


def analyze(records):
    return Detector(Baseline.load()).analyze(records)


def rule_ids(alerts):
    return {a.rule_id for a in alerts}


def test_clean_run_produces_no_alerts(tmp_path):
    """The false-positive guard. Normal plant operation must be silent."""
    with running_range(tmp_path) as rng:
        result = rng.hmi_poll(cycles=20)
        assert result.returncode == 0, result.stderr

        records = rng.records()
        assert records, "tap captured no traffic"

        alerts = analyze(records)
        assert alerts == [], f"clean run produced false positives: {[a.message for a in alerts]}"


def test_s01_recon_is_detected(tmp_path):
    with running_range(tmp_path) as rng:
        rng.hmi_poll(cycles=5)

        result = rng.run_module(
            "attacker.s01_recon",
            "--port",
            str(rng.tap_port),
            "--source-ip",
            ATTACKER_SOURCE_IP,
            "--sweep-count",
            "8",
            "--unit-id-max",
            "5",
        )
        assert result.returncode == 0, result.stderr

        alerts = analyze(rng.records())
        fired = rule_ids(alerts)

        assert "MODBUS_UNAUTHORIZED_SOURCE" in fired
        assert "MODBUS_POINT_ENUMERATION" in fired

        for alert in alerts:
            if alert.rule_id in ("MODBUS_UNAUTHORIZED_SOURCE", "MODBUS_POINT_ENUMERATION"):
                assert alert.source == ATTACKER_SOURCE_IP


def test_s01_recon_does_not_implicate_the_hmi(tmp_path):
    """Recon must not get the legitimate HMI blamed for it."""
    with running_range(tmp_path) as rng:
        rng.hmi_poll(cycles=10)
        rng.run_module(
            "attacker.s01_recon",
            "--port",
            str(rng.tap_port),
            "--source-ip",
            ATTACKER_SOURCE_IP,
            "--sweep-count",
            "8",
        )
        alerts = analyze(rng.records())
        assert alerts, "expected recon to be detected"
        assert all(a.source != HMI_SOURCE_IP for a in alerts)


def test_s03_unauthorized_command_is_detected(tmp_path):
    with running_range(tmp_path, level=90.0, speed=600.0) as rng:
        rng.hmi_poll(cycles=5)

        result = rng.run_module(
            "attacker.s03_unauthorized_command",
            "--port",
            str(rng.tap_port),
            "--source-ip",
            ATTACKER_SOURCE_IP,
            "--timeout",
            "90",
            "--poll",
            "1.0",
        )
        assert result.returncode == 0, f"attack did not reach full impact:\n{result.stdout}"

        alerts = analyze(rng.records())
        write_alerts = [a for a in alerts if a.rule_id == "MODBUS_UNAUTHORIZED_WRITE"]

        assert write_alerts, f"unauthorised write not detected. fired: {rule_ids(alerts)}"
        alert = write_alerts[0]
        assert alert.severity == "critical"
        assert alert.source == ATTACKER_SOURCE_IP
        assert alert.technique.startswith("T0855")


def test_s03_reaches_overflow_and_pump_damage(tmp_path):
    """The attack must actually break the process, not just send packets.

    A scenario whose 'impact' is invisible teaches nothing.
    """
    with running_range(tmp_path, level=90.0, speed=600.0) as rng:
        result = rng.run_module(
            "attacker.s03_unauthorized_command",
            "--port",
            str(rng.tap_port),
            "--source-ip",
            ATTACKER_SOURCE_IP,
            "--timeout",
            "90",
            "--poll",
            "1.0",
        )
        assert result.returncode == 0, result.stdout
        assert "TANK OVERFLOWING" in result.stdout
        assert "PUMP FAULT" in result.stdout


def test_s03_write_targets_the_pump_and_mode_coils(tmp_path):
    """The detection should name the points that were actually attacked,
    so an analyst reading the alert knows what moved."""
    from common.pointmap import load as load_pointmap

    pm = load_pointmap()
    expected = {pm["P101_RUN"].index, pm["MODE_AUTO"].index}

    with running_range(tmp_path, level=95.0, speed=600.0) as rng:
        rng.run_module(
            "attacker.s03_unauthorized_command",
            "--port",
            str(rng.tap_port),
            "--source-ip",
            ATTACKER_SOURCE_IP,
            "--timeout",
            "90",
            "--poll",
            "1.0",
        )
        alerts = analyze(rng.records())
        write_alerts = [a for a in alerts if a.rule_id == "MODBUS_UNAUTHORIZED_WRITE"]
        assert write_alerts

        addresses = set(write_alerts[0].evidence["addresses"])
        assert expected <= addresses, f"expected {expected} among attacked addresses {addresses}"


def test_s05_manipulation_of_view_is_detected(tmp_path):
    with running_range(tmp_path, level=90.0, speed=600.0) as rng:
        rng.hmi_poll(cycles=5)

        result = rng.run_module(
            "attacker.s05_manipulation_of_view",
            "--port",
            str(rng.tap_port),
            "--source-ip",
            ATTACKER_SOURCE_IP,
            "--timeout",
            "90",
            "--poll",
            "1.0",
        )
        assert result.returncode == 0, f"attack did not reach full impact:\n{result.stdout}"

        alerts = analyze(rng.records())
        view_alerts = [a for a in alerts if a.rule_id == "MODBUS_VIEW_MANIPULATION"]

        assert view_alerts, f"view manipulation not detected. fired: {rule_ids(alerts)}"
        alert = view_alerts[0]
        assert alert.severity == "critical"
        assert alert.technique.startswith("T0856")
        # The reported value is the whole lie — it must be nowhere near
        # the physical high-high threshold the float actually tripped at.
        assert alert.evidence["lt_101_reported_pct"] < 90.0


def test_s05_reaches_hardwired_trip_while_reported_level_stays_frozen(tmp_path):
    """The attack must actually diverge from reality, not just send
    packets — the hardwired float has to trip for real while LT_101
    never leaves the spoofed value."""
    with running_range(tmp_path, level=90.0, speed=600.0) as rng:
        result = rng.run_module(
            "attacker.s05_manipulation_of_view",
            "--port",
            str(rng.tap_port),
            "--source-ip",
            ATTACKER_SOURCE_IP,
            "--timeout",
            "90",
            "--poll",
            "1.0",
        )
        assert result.returncode == 0, result.stdout
        assert "LSHH_101 (hardwired) TRIPPED" in result.stdout
        assert "LT_101 ever left 50%   : False" in result.stdout


def test_s05_initial_spoof_write_is_also_caught_by_the_baseline_rule(tmp_path):
    """Defense in depth: arming the spoof is itself a write to an address
    outside the published point map, from an unrecognised source — the
    same baseline-allowlist mechanism that catches S03 should flag it,
    independent of whether the cross-consistency rule also fires. If an
    analyst only sees this alert (e.g. missed the later contradiction in
    a noisy log), they still had a chance to catch the attack."""
    with running_range(tmp_path, level=90.0, speed=600.0) as rng:
        rng.run_module(
            "attacker.s05_manipulation_of_view",
            "--port",
            str(rng.tap_port),
            "--source-ip",
            ATTACKER_SOURCE_IP,
            "--timeout",
            "90",
            "--poll",
            "1.0",
        )
        alerts = analyze(rng.records())
        write_alerts = [a for a in alerts if a.rule_id == "MODBUS_UNAUTHORIZED_WRITE"]
        assert write_alerts, f"spoof-arming write not caught. fired: {rule_ids(alerts)}"
        assert any(LT101_SPOOF_HR_INDEX in a.evidence.get("addresses", []) for a in write_alerts)
