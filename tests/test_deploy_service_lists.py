"""Enforce that the deploy scripts stay in sync with config.SERVICES.

config.py SERVICES is the single source of truth for the monitored service set
(the same list /healthz checks). The deploy must start exactly that set, or the
final /healthz gate in oneshot.sh comes back 'degraded' for a service that was
simply never started. These tests fail loudly the moment a new service is added
to config.py without updating the deploy scripts.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, 'src')

import config

REPO = Path(__file__).resolve().parent.parent
# Boot/oneshot aux units that run but are intentionally NOT health-monitored.
AUX_UNITS = {'drifter-boot-manager', 'drifter-boot-reason', 'drifter-db-checkpoint'}


def _oneshot_services() -> set[str]:
    text = (REPO / 'scripts' / 'oneshot.sh').read_text()
    m = re.search(r'SERVICES=\((.*?)\)', text, re.DOTALL)
    assert m, "could not find SERVICES=( ... ) array in oneshot.sh"
    return {t for t in m.group(1).split() if t.startswith('drifter-')}


def _install_services() -> set[str]:
    text = (REPO / 'install.sh').read_text()
    m = re.search(r'SERVICES="([^"]*)"', text)
    assert m, "could not find SERVICES=\"...\" in install.sh"
    return set(m.group(1).split())


def _unit_files() -> set[str]:
    return {p.stem for p in (REPO / 'services').glob('*.service')}


def test_oneshot_starts_exactly_config_services():
    assert _oneshot_services() == set(config.SERVICES)


def test_install_enables_at_least_config_services():
    install = _install_services()
    missing = set(config.SERVICES) - install
    assert not missing, f"install.sh does not enable: {sorted(missing)}"


def test_install_extras_are_only_known_aux_units():
    extras = _install_services() - set(config.SERVICES)
    assert extras <= AUX_UNITS, f"unexpected extras in install.sh: {sorted(extras - AUX_UNITS)}"


def test_every_monitored_service_has_a_unit_file():
    missing = set(config.SERVICES) - _unit_files()
    assert not missing, f"config.SERVICES with no services/*.service: {sorted(missing)}"


def test_every_deploy_service_has_a_unit_file():
    units = _unit_files()
    for name in _oneshot_services() | _install_services():
        assert name in units, f"{name} has no services/{name}.service"
