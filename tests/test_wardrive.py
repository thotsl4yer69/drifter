#!/usr/bin/env python3
"""
Tests for wardrive.py — parsing logic is exercised without hardware.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from wardrive import parse_nmcli_wifi, parse_hcitool_classic, parse_hcitool_le


# ── parse_nmcli_wifi ──────────────────────────────────────────────

NMCLI_SAMPLE = """\
SSID:HomeNetwork
BSSID:AA\\:BB\\:CC\\:DD\\:EE\\:FF
SIGNAL:72
CHAN:6
SECURITY:WPA2

SSID:
BSSID:11\\:22\\:33\\:44\\:55\\:66
SIGNAL:40
CHAN:11
SECURITY:--

SSID:Café\\ Free
BSSID:DE\\:AD\\:BE\\:EF\\:CA\\:FE
SIGNAL:88
CHAN:1
SECURITY:WPA3
"""


def test_parse_nmcli_basic():
    nets = parse_nmcli_wifi(NMCLI_SAMPLE)
    assert len(nets) == 3


def test_parse_nmcli_ssid_and_bssid():
    nets = parse_nmcli_wifi(NMCLI_SAMPLE)
    assert nets[0]['ssid'] == 'HomeNetwork'
    assert nets[0]['bssid'] == 'AA:BB:CC:DD:EE:FF'


def test_parse_nmcli_hidden_ssid():
    nets = parse_nmcli_wifi(NMCLI_SAMPLE)
    assert nets[1]['ssid'] == '<hidden>'


def test_parse_nmcli_signal_conversion():
    nets = parse_nmcli_wifi(NMCLI_SAMPLE)
    # 72% → (72/2) - 100 = -64 dBm
    assert nets[0]['signal_pct'] == 72
    assert nets[0]['signal_dbm'] == -64


def test_parse_nmcli_security():
    nets = parse_nmcli_wifi(NMCLI_SAMPLE)
    assert nets[0]['security'] == 'WPA2'
    assert nets[2]['security'] == 'WPA3'


def test_parse_nmcli_empty_output():
    assert parse_nmcli_wifi('') == []


def test_parse_nmcli_no_bssid_skipped():
    # Lines without a valid entry should be skipped
    bad = "SSID:NoAddr\nSIGNAL:50\nCHAN:6\nSECURITY:WPA2\n"
    nets = parse_nmcli_wifi(bad)
    assert nets == []


# ── parse_hcitool_classic ─────────────────────────────────────────

HCITOOL_CLASSIC = """\
Scanning ...
\tAA:BB:CC:DD:EE:FF\tMy Phone
\t11:22:33:44:55:66\t(unknown)
"""


def test_parse_hcitool_classic_count():
    devs = parse_hcitool_classic(HCITOOL_CLASSIC)
    assert len(devs) == 2


def test_parse_hcitool_classic_addr():
    devs = parse_hcitool_classic(HCITOOL_CLASSIC)
    assert devs[0]['addr'] == 'AA:BB:CC:DD:EE:FF'
    assert devs[0]['name'] == 'My Phone'
    assert devs[0]['type'] == 'classic'


def test_parse_hcitool_classic_unknown():
    devs = parse_hcitool_classic(HCITOOL_CLASSIC)
    assert devs[1]['name'] == '(unknown)'


def test_parse_hcitool_classic_empty():
    assert parse_hcitool_classic('Scanning ...\n') == []


# ── parse_hcitool_le ──────────────────────────────────────────────

HCITOOL_LE = """\
LE Scan ...
AA:BB:CC:11:22:33 MyEarbuds
AA:BB:CC:11:22:33 MyEarbuds
DE:AD:BE:EF:00:01 (unknown)
"""


def test_parse_hcitool_le_dedup():
    devs = parse_hcitool_le(HCITOOL_LE)
    assert len(devs) == 2


def test_parse_hcitool_le_type():
    devs = parse_hcitool_le(HCITOOL_LE)
    for d in devs:
        assert d['type'] == 'ble'


def test_parse_hcitool_le_name():
    devs = parse_hcitool_le(HCITOOL_LE)
    names = {d['addr']: d['name'] for d in devs}
    assert names['AA:BB:CC:11:22:33'] == 'MyEarbuds'
