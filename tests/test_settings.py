"""Tests for config.load_settings() and save_settings()."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import config


class TestLoadSettings:
    def test_returns_defaults_when_no_file(self, tmp_path):
        fake_path = tmp_path / "nonexistent.json"
        with patch.object(config, 'SETTINGS_FILE', fake_path):
            result = config.load_settings()
        assert result == config.SETTINGS_DEFAULTS
        assert result['coolant_amber'] == 104
        assert result['tts_engine'] == 'piper'
        assert result['setup_complete'] is False

    def test_loads_saved_values(self, tmp_path):
        fake_path = tmp_path / "settings.json"
        fake_path.write_text(json.dumps({'coolant_amber': 110, 'tts_engine': 'espeak'}))
        with patch.object(config, 'SETTINGS_FILE', fake_path):
            result = config.load_settings()
        assert result['coolant_amber'] == 110
        assert result['tts_engine'] == 'espeak'
        # Defaults still present for unsaved keys
        assert result['coolant_red'] == 108
        assert result['temp_unit'] == 'C'

    def test_handles_corrupt_json(self, tmp_path):
        fake_path = tmp_path / "settings.json"
        fake_path.write_text("NOT JSON {{{")
        with patch.object(config, 'SETTINGS_FILE', fake_path):
            result = config.load_settings()
        # Falls back to defaults
        assert result == config.SETTINGS_DEFAULTS

    def test_merges_new_defaults_with_old_file(self, tmp_path):
        """If settings.json has only a subset of keys, defaults fill the rest."""
        fake_path = tmp_path / "settings.json"
        fake_path.write_text(json.dumps({'data_retention_days': 30}))
        with patch.object(config, 'SETTINGS_FILE', fake_path):
            result = config.load_settings()
        assert result['data_retention_days'] == 30
        assert result['llm_max_tokens'] == 500  # default


class TestSaveSettings:
    def test_creates_file(self, tmp_path):
        fake_path = tmp_path / "settings.json"
        with patch.object(config, 'SETTINGS_FILE', fake_path):
            ok = config.save_settings({'coolant_amber': 100})
        assert ok is True
        assert fake_path.exists()
        saved = json.loads(fake_path.read_text())
        assert saved['coolant_amber'] == 100

    def test_creates_parent_dirs(self, tmp_path):
        fake_path = tmp_path / "sub" / "dir" / "settings.json"
        with patch.object(config, 'SETTINGS_FILE', fake_path):
            ok = config.save_settings({'temp_unit': 'F'})
        assert ok is True
        saved = json.loads(fake_path.read_text())
        assert saved['temp_unit'] == 'F'

    def test_roundtrip(self, tmp_path):
        fake_path = tmp_path / "settings.json"
        original = dict(config.SETTINGS_DEFAULTS)
        original['coolant_amber'] = 106
        original['tts_engine'] = 'espeak'
        with patch.object(config, 'SETTINGS_FILE', fake_path):
            config.save_settings(original)
            loaded = config.load_settings()
        assert loaded['coolant_amber'] == 106
        assert loaded['tts_engine'] == 'espeak'

    def test_returns_false_on_permission_error(self, tmp_path):
        # Use a path that is a directory to trigger an OS error on open()
        fake_path = tmp_path
        with patch.object(config, 'SETTINGS_FILE', fake_path):
            ok = config.save_settings({'test': True})
        assert ok is False


class TestSettingsDefaults:
    def test_all_expected_keys_present(self):
        expected_keys = [
            'coolant_amber', 'coolant_red', 'voltage_undercharge', 'voltage_critical',
            'stft_lean_idle', 'ltft_lean_warn', 'ltft_lean_crit',
            'voice_cooldown', 'tts_engine', 'voice_min_level',
            'temp_unit', 'pressure_unit',
            'llm_model', 'llm_max_tokens', 'llm_tools_enabled',
            'data_retention_days', 'setup_complete',
        ]
        for key in expected_keys:
            assert key in config.SETTINGS_DEFAULTS, f"Missing key: {key}"
