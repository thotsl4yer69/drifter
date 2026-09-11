"""The physical-test gate must never pass on cached or fabricated data."""
import pytest

from config import TOPICS
from vehicle_check import Evidence


def record(evidence, times, *, source='obd_bridge', rpm=800):
    for ts in times:
        for key, value in {'rpm': rpm, 'speed': 0, 'coolant': 90}.items():
            evidence.receive(TOPICS[key], {'value': value, 'ts': ts, 'source': source}, now=ts)


def test_live_continuity_passes_and_optional_voltage_is_reported_unknown():
    evidence = Evidence(100)
    record(evidence, range(102, 161, 2))
    report = evidence.report(160, engine_running=True)
    assert report['passed']
    assert report['metrics']['voltage']['last'] is None
    assert report['metrics']['rpm']['median_interval_s'] == 2


@pytest.mark.parametrize('times', [[], [101], [101, 103], [101, 140, 159], [155, 159]])
def test_missing_stale_gaps_and_late_start_fail(times):
    evidence = Evidence(100)
    record(evidence, times)
    assert not evidence.report(160)['passed']


@pytest.mark.parametrize('value', [None, '800', True, float('nan'), float('inf'), -1, 20000])
def test_invalid_metric_rejected(value):
    evidence = Evidence(100)
    evidence.receive(TOPICS['rpm'], {'value': value, 'ts': 101, 'source': 'obd_bridge'}, now=101)
    assert evidence.rejected == 1
    assert not evidence.samples['rpm']


def test_retained_message_is_never_physical_test_evidence():
    evidence = Evidence(100)
    evidence.receive(TOPICS['rpm'], {'value': 800, 'ts': 101, 'source': 'obd_bridge'}, now=101, retained=True)
    assert not evidence.samples['rpm']


def test_two_real_sources_fail_arbitration_gate():
    evidence = Evidence(100)
    record(evidence, [101, 103])
    record(evidence, [105], source='can_bridge')
    assert not evidence.report(106)['passed']


def test_engine_off_can_pass_ignition_check_but_not_idle_check():
    evidence = Evidence(100)
    record(evidence, [101, 103], rpm=0)
    assert evidence.report(104)['passed']
    assert not evidence.report(104, engine_running=True)['passed']
