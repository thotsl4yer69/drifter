"""Offline tests for the in-car LCD dashboard, auto-connect, and boot manager.

No hardware: these exercise the pure formatting / parsing / state helpers.
Rendering (PIL/numpy → framebuffer) and GPIO are guarded behind optional
imports in the modules, so this suite runs anywhere — including CI boxes
without Pillow, numpy, RPi.GPIO, or a real /dev/fb1.
"""
import sys

sys.path.insert(0, 'src')

import auto_connect as ac
import config
import lcd_dashboard as lcd

TH = config.LCD_THEME


# ──────────────────── framebuffer resolution (regression) ────────────────
# These guard the in-car blank-LCD bug: the SPI panel must be resolved by
# sysfs driver name (not a hardcoded fb1) and waited for, so a late/renumbered
# probe doesn't leave the dash dark for the whole drive.

def _mk_fb(root, idx, name):
    d = root / f"fb{idx}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "name").write_text(name + "\n")


def test_resolve_fb_prefers_spi_panel_by_name(tmp_path):
    _mk_fb(tmp_path, 0, "vc4drmfb")     # HDMI/DRM plane
    _mk_fb(tmp_path, 1, "fb_ili9486")   # the SPI dash panel
    assert lcd.resolve_fb_device(
        preferred="/dev/fbNONE", sysfs_root=str(tmp_path)) == "/dev/fb1"


def test_resolve_fb_survives_renumber(tmp_path):
    # SPI panel grabbed fb0 and the DRM plane is fb1 — must follow the name,
    # not the index, or we'd blit the menu onto the HDMI plane.
    _mk_fb(tmp_path, 0, "fb_ili9486")
    _mk_fb(tmp_path, 1, "vc4drmfb")
    assert lcd.resolve_fb_device(
        preferred="/dev/fb1", sysfs_root=str(tmp_path)) == "/dev/fb0"


def test_resolve_fb_none_when_only_drm_plane(tmp_path):
    _mk_fb(tmp_path, 0, "vc4drmfb")     # only HDMI present, no SPI panel yet
    assert lcd.resolve_fb_device(
        preferred="/dev/fbNONE", sysfs_root=str(tmp_path)) is None


def test_wait_for_fb_times_out_without_panel(tmp_path):
    # No panel + zero budget → returns None promptly (caller then exits 1 so
    # systemd retries). Must not hang.
    assert lcd.wait_for_fb(
        timeout=0, interval=0.01,
        preferred="/dev/fbNONE", sysfs_root=str(tmp_path)) is None


def test_ap_fallback_default_is_recovery_fast():
    # 300s read as "hotspot never came up" on first car launch; lock the
    # internet-first 90s recovery default in code so a reinstall can't revert it.
    assert config.AUTOCONNECT_AP_FALLBACK_SEC <= 90


# ───────────────────────── lcd_dashboard helpers ─────────────────────────

def test_fmt_uptime_buckets():
    assert lcd.fmt_uptime(0) == '0m 00s'
    assert lcd.fmt_uptime(65) == '1m 05s'
    assert lcd.fmt_uptime(3 * 3600 + 12 * 60) == '3h 12m'
    assert lcd.fmt_uptime(3 * 86400 + 4 * 3600) == '3d 04h'


def test_fmt_bytes():
    assert lcd.fmt_bytes(512) == '512B'
    assert lcd.fmt_bytes(1536) == '1.5K'
    assert lcd.fmt_bytes(1.2 * 1024 ** 3) == '1.2G'


def test_level_color_higher_is_worse():
    assert lcd.level_color(50, 70, 80, TH) == TH['ok']
    assert lcd.level_color(72, 70, 80, TH) == TH['warn']
    assert lcd.level_color(85, 70, 80, TH) == TH['crit']


def test_level_color_lower_is_worse_voltage():
    # Battery volts: warn at 13.2, crit at 12.0 (lower is worse).
    assert lcd.level_color(14.2, 13.2, 12.0, TH, higher_is_worse=False) == TH['ok']
    assert lcd.level_color(13.0, 13.2, 12.0, TH, higher_is_worse=False) == TH['warn']
    assert lcd.level_color(11.8, 13.2, 12.0, TH, higher_is_worse=False) == TH['crit']


def test_service_state_color():
    assert lcd.service_state_color('active', TH) == TH['ok']
    assert lcd.service_state_color('activating', TH) == TH['warn']
    assert lcd.service_state_color('failed', TH) == TH['crit']
    assert lcd.service_state_color('inactive', TH) == TH['crit']


def test_signal_quality():
    assert lcd.signal_quality(None) == (0, 'n/a')
    assert lcd.signal_quality(-50)[0] == 4
    assert lcd.signal_quality(-70)[0] == 2
    assert lcd.signal_quality(-90)[0] == 0


def test_val_extraction():
    assert lcd._val({'value': 720}) == 720
    assert lcd._val(720) == 720
    assert lcd._val({'no_value': 1}) is None


# ───────────────────────── auto_connect helpers ─────────────────────────

def test_parse_wifi_scan_drops_blanks():
    out = "MZ1312_DRIFTER\nMyPhone\n\n  \nNeighbourNet\n"
    assert ac.parse_wifi_scan(out) == {'MZ1312_DRIFTER', 'MyPhone', 'NeighbourNet'}


def test_parse_active_ssid():
    out = "no:OtherNet\nyes:MyPhone\nno:Cafe\n"
    assert ac.parse_active_ssid(out) == 'MyPhone'
    assert ac.parse_active_ssid("no:OtherNet\nno:Cafe\n") is None


def test_pick_target_ssid_priority():
    visible = {'Cafe', 'MyPhone', 'MZ1312_DRIFTER'}
    # Known list is priority order — first match wins.
    assert ac.pick_target_ssid(visible, ['MyPhone', 'MZ1312_DRIFTER']) == 'MyPhone'
    assert ac.pick_target_ssid(visible, ['Nope', 'MZ1312_DRIFTER']) == 'MZ1312_DRIFTER'
    assert ac.pick_target_ssid(visible, ['Nope', 'AlsoNope']) is None


# ───────────────────────── config wiring ─────────────────────────

def test_new_topics_present():
    for key in ('network_status', 'lcd_command', 'lcd_status', 'boot_status'):
        assert key in config.TOPICS
        assert config.TOPICS[key].startswith('drifter/')


def test_lcd_services_registered_and_classified():
    for svc in ('drifter-lcd', 'drifter-autoconnect'):
        assert svc in config.SERVICES
        # Both are SHARED — must appear in BOTH personas.
        assert svc in config.MODES['drive']
        assert svc in config.MODES['foot']


def test_boot_core_services_subset_of_services():
    assert set(config.BOOT_CORE_SERVICES) <= set(config.SERVICES)


def test_lcd_screens_include_all_renderers():
    assert set(config.LCD_SCREENS) == {
        'status', 'services', 'network', 'diagnostics', 'vehicle'}


# ── In-car diagnostics: DTC decode + OBD staleness ────────────────────

def test_decode_dtc_known_code():
    label, sev = lcd.decode_dtc('P0171')
    assert label.startswith('P0171') and 'Lean' in label
    assert sev in ('AMBER', 'RED')


def test_decode_dtc_case_and_whitespace():
    label, sev = lcd.decode_dtc('  p0171 ')
    assert 'P0171' in label and 'Lean' in label


def test_decode_dtc_unknown_falls_back():
    label, sev = lcd.decode_dtc('P9999')
    assert label == 'P9999' and sev == 'AMBER'


def test_decode_dtc_empty():
    assert lcd.decode_dtc('') == ('', 'AMBER')


def test_build_frame_data_exposes_vehicle_ts():
    # A minimal fake cache exposing the fields build_frame_data reads.
    class _Cache:
        def __init__(self):
            self.connected = True
            self.network = None
            self.gps = None
            self.vehicle = {'rpm': 800}
            self.vehicle_ts = 123.0
            self.dtcs = []
            self.last_alert = None

        def msg_rate(self):
            return 0.0

    class _CC:
        def get(self, key, fn, ttl):
            return {}

    data = lcd.build_frame_data(_Cache(), _CC())
    assert data['mqtt']['vehicle_ts'] == 123.0
