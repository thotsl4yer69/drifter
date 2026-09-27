from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHELL = (ROOT / "cockpit-v4/src/app/shell.jsx").read_text()
LEDGER = (ROOT / "cockpit-v4/src/directions/ledger.jsx").read_text()
ADAPTER = (ROOT / "cockpit-v4/src/data/adapter.js").read_text()
CSS = (ROOT / "cockpit-v4/src/styles/drifter-dna.css").read_text()


def test_touch_navigation_exposes_operator_surfaces():
    for label in ("drive", "map", "diag", "rf", "foot", "vivi", "system"):
        assert f"l: '{label}'" in LEDGER


def test_drive_uses_contextual_perception_not_rf_tile():
    drive = SHELL[SHELL.index("function ShDriveMain"):SHELL.index("function ShSurface")]
    assert "<PerceptionTile sim={sim} />" in drive
    assert "<LgRf sim={sim} />" not in drive


def test_hardware_strip_is_persistent_on_drive():
    assert "<HwStrip sim={sim} />" in SHELL
    for label in ("OBD", "GPS", "HAILO", "CAM", "SDR", "REC", "LINK"):
        assert f"['{label}'" in SHELL


def test_adapter_maps_perception_topics_without_fake_live_defaults():
    assert "s.perception = { state: 'offline', vision: 'offline'" in ADAPTER
    for topic in (
        "drifter/vision/status",
        "drifter/vision/perception/status",
        "drifter/vision/perception/event",
        "drifter/vision/object",
        "drifter/vision/fcw/warning",
        "drifter/vision/dashcam/status",
    ):
        assert topic in ADAPTER


def test_touch_targets_are_large_enough_for_pi_screen():
    assert "width:58px; min-height:58px" in CSS
