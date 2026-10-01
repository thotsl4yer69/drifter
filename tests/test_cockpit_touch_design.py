from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHELL = (ROOT / "cockpit-v4/src/app/shell.jsx").read_text()
UI = (ROOT / "cockpit-v4/src/shared/touchscreen.jsx").read_text()
DISPLAY = (ROOT / "cockpit-v4/src/data/display-state.js").read_text()
ADAPTER = (ROOT / "cockpit-v4/src/data/adapter.js").read_text()
CSS = (ROOT / "cockpit-v4/src/styles/touchscreen.css").read_text()


def test_touch_navigation_exposes_operator_surfaces():
    for label in ("drive", "recon", "map", "diag", "rf", "vivi", "system"):
        assert f"l: '{label}'" in UI
    assert "aria-current" in UI
    assert "l: 'foot'" not in UI  # FOOT remains a backend persona, not a primary surveillance tab


def test_drive_uses_contextual_perception_not_rf_tile():
    drive = SHELL[SHELL.index("function ShDriveMain"):SHELL.index("function ShSurface")]
    assert "<DrivePanel" in drive
    assert "<PerceptionTile sim={sim}" in UI
    assert "<LgRf" not in drive
    assert "const armed = sim.mode === 'foot' || sim.mode === 'both'" in SHELL


def test_hardware_strip_is_persistent_on_drive():
    assert "<HwStrip sim={sim}" in UI
    for label in ("OBD", "GPS", "HAILO", "CAM", "SDR", "REC", "LINK"):
        assert f"['{label}'" in DISPLAY


def test_adapter_maps_perception_topics_without_fake_live_defaults():
    assert "s.perception = { state: 'offline', vision: 'offline'" in ADAPTER
    assert "observeDisplayFrame(state, topic, data, Date.now(), cached)" in ADAPTER
    assert "invalidateDisplay(state)" in ADAPTER
    assert "applyTopic('drifter/snapshot', v, true)" in ADAPTER
    for topic in ("drifter/vision/status", "drifter/vision/perception/status",
                  "drifter/vision/perception/event", "drifter/vision/object",
                  "drifter/vision/fcw/warning", "drifter/vision/dashcam/status",
                  "drifter/recon/status", "drifter/recon/event",
                  "drifter/vision/alpr/plate"):
        assert topic in ADAPTER


def test_touch_targets_and_compact_rail_have_explicit_rules():
    assert "min-height:58px" in CSS
    assert 'grid-template-areas:"top" "main" "rail"' in CSS
    assert "prefers-reduced-motion:reduce" in CSS


def test_new_samples_are_not_compared_to_previous_timer_tick():
    render = UI[UI.index("export class DrivePanel"):UI.index("export class DataSheet")]
    assert "const now = Date.now()" in render
    assert "clearInterval(this.clock)" in render


def test_dialog_is_modal_and_restores_focus():
    assert "this.dialog.showModal()" in UI
    assert "this.returnFocus.focus()" in UI
    assert "event.key !== 'Tab'" in UI


def test_display_logic_and_adapter_contract_in_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js required for executable frontend contracts")
    suites = sorted((ROOT / "cockpit-v4/tests").glob("*-display.test.mjs"))
    suites.append(ROOT / "cockpit-v4/tests/display-state.test.mjs")
    completed = subprocess.run([node, "--test", *map(str, suites)], cwd=ROOT,
                               capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_cockpit_never_pushes_a_persisted_mode_on_mount():
    assert "DrifterSim.setMode(t.mode)" not in SHELL
    assert "onMode={mode => DrifterSim.setMode(mode)}" in SHELL
    assert "['drive', 'recon'].map" in UI


def test_recon_surface_is_integrated():
    assert "ReconMain" in SHELL
    assert "surf === 'recon'" in SHELL
    recon = (ROOT / "cockpit-v4/src/directions/recon.jsx").read_text()
    for term in ("RECON / HAILO SURVEILLANCE", "evidence ledger", "recent plates", "ENTER RECON"):
        assert term in recon
