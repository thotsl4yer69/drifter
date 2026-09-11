"""Exercise the installer transaction in an isolated filesystem.

Only systemd and pip are substituted. Staging, module compilation/imports,
backups, file replacement and rollback run as written in the real installer.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def install_env(tmp_path):
    runtime, units, binary, commands = (tmp_path / name for name in ('runtime', 'units', 'bin', 'commands'))
    for path in (runtime / 'venv/bin', units, binary, commands):
        path.mkdir(parents=True)
    (runtime / 'config.py').write_text('# previous runtime\n')
    (runtime / '.env').write_text('ELM_WIFI_HOST=adapter.example\n')
    (runtime / 'calibration.json').write_text('{"preserve": true}\n')
    (binary / 'drifter').write_text('# previous CLI\n')
    (units / 'drifter-obdbridge.service').write_text('# previous unit\n')
    python = runtime / 'venv/bin/python3'
    python.write_text(f'#!{sys.executable}\n' + '''import os, sys
if sys.argv[1:3] == ['-m', 'pip']:
    raise SystemExit(0)
if sys.argv[1:3] == ['-m', 'compileall'] and os.getenv('FAIL_COMPILE') == '1':
    raise SystemExit(2)
os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
''')
    python.chmod(0o755)
    state_file = tmp_path / 'systemctl.json'
    original = ['drifter-obdbridge', 'drifter-alerts', 'drifter-watchdog']
    state_file.write_text(json.dumps({'active': original, 'enabled': original, 'calls': []}))
    systemctl = commands / 'systemctl'
    systemctl.write_text(f'#!{sys.executable}\n' + '''import json, os, pathlib, sys
p = pathlib.Path(os.environ['FAKE_SYSTEMCTL_STATE'])
s = json.loads(p.read_text())
action, *args = sys.argv[1:]
names = [n.removesuffix('.service') for n in args if not n.startswith('-')]
s['calls'].append([action, *names])
rc = 0
if action in ('is-active', 'is-enabled'):
    rc = 0 if all(n in s['active' if action == 'is-active' else 'enabled'] for n in names) else 3
elif action == 'restart' and os.getenv('FAIL_RESTART') == '1' and not s.get('failed_once'):
    s['failed_once'] = True
    rc = 1
elif action in ('start', 'restart', 'enable'):
    key = 'enabled' if action == 'enable' else 'active'
    s[key] = sorted(set(s[key]) | set(names))
elif action in ('stop', 'disable'):
    key = 'enabled' if action == 'disable' else 'active'
    s[key] = sorted(set(s[key]) - set(names))
p.write_text(json.dumps(s))
raise SystemExit(rc)
''')
    systemctl.chmod(0o755)
    env = {**os.environ, 'PATH': str(commands) + os.pathsep + os.environ['PATH'],
           'DRIFTER_INSTALL_ROOT': str(runtime), 'DRIFTER_DIR': str(runtime),
           'DRIFTER_SYSTEMD_DIR': str(units), 'DRIFTER_BIN_DIR': str(binary),
           'FAKE_SYSTEMCTL_STATE': str(state_file)}
    return runtime, units, binary, state_file, env, original


@pytest.mark.skipif(os.geteuid() != 0, reason='Installer requires root; all paths remain isolated')
@pytest.mark.parametrize('failure', ['', 'FAIL_RESTART', 'FAIL_COMPILE'])
def test_installer_preserves_configuration_and_recovers_from_failure(install_env, failure):
    runtime, units, binary, state_file, env, original = install_env
    if failure:
        env[failure] = '1'
    result = subprocess.run(['bash', str(ROOT / 'scripts/install-obd.sh')],
                            env=env, capture_output=True, text=True, timeout=45)
    assert (result.returncode != 0) == bool(failure), result.stdout + result.stderr
    assert (runtime / '.env').read_text() == 'ELM_WIFI_HOST=adapter.example\n'
    assert (runtime / 'calibration.json').read_text() == '{"preserve": true}\n'
    state = json.loads(state_file.read_text())
    assert 'drifter-watchdog' in state['active']
    if failure:
        assert (runtime / 'config.py').read_text() == '# previous runtime\n'
        assert (binary / 'drifter').read_text() == '# previous CLI\n'
        assert (units / 'drifter-obdbridge.service').read_text() == '# previous unit\n'
        assert not (runtime / 'elm_protocol.py').exists()
        assert not (units / 'drifter-safety.service').exists()
        assert set(state['active']) == set(original)
        assert set(state['enabled']) == set(original)
    else:
        assert (runtime / 'elm_protocol.py').read_bytes() == (ROOT / 'src/elm_protocol.py').read_bytes()
        assert (runtime / '_config_topics.py').read_bytes() == (ROOT / 'src/_config_topics.py').read_bytes()
        assert (runtime / 'vehicle_check.py').exists()
        assert 'drifter-safety' in state['active']
        backup = next((runtime / 'backups').iterdir())
        assert (backup / 'runtime/config.py').read_text() == '# previous runtime\n'
