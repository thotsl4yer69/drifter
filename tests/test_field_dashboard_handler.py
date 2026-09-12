import field_dashboard_handler as field


def test_field_routes_are_local_only():
    assert field._local('127.0.0.1')
    assert field._local('10.42.0.55')
    assert not field._local('192.168.1.10')


def test_field_validators_reject_shell_like_values():
    assert field._MAC_RE.fullmatch('AA:BB:CC:DD:EE:FF')
    assert not field._MAC_RE.fullmatch('AA:BB;rm -rf /')
    assert field._DEV_RE.fullmatch('/dev/ttyUSB0')
    assert not field._DEV_RE.fullmatch('/dev/ttyUSB0;id')


def test_rf_field_actions_are_explicit_allowlist():
    assert {'survey', 'hunt_start', 'capture', 'listen', 'recover'} <= field._RF_ACTIONS
    assert 'transmit' not in field._RF_ACTIONS
    assert 'jam' not in field._RF_ACTIONS
