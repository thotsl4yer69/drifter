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


class TestSettingsSchema:
    """SETTINGS_SCHEMA drives the operator-facing cockpit overlay.
    Internal-state flags (setup_complete) must never appear there even
    though they remain in SETTINGS_DEFAULTS for the onboarding flow."""

    def test_setup_complete_excluded(self):
        keys = {entry['key'] for entry in config.SETTINGS_SCHEMA}
        assert 'setup_complete' not in keys

    def test_every_schema_key_exists_in_defaults(self):
        for entry in config.SETTINGS_SCHEMA:
            assert entry['key'] in config.SETTINGS_DEFAULTS, \
                f"Schema key {entry['key']} not in SETTINGS_DEFAULTS"

    def test_required_fields_present_on_every_entry(self):
        for entry in config.SETTINGS_SCHEMA:
            for field in ('key', 'label', 'description', 'type', 'section'):
                assert field in entry, \
                    f"Entry {entry.get('key')} missing required field {field}"

    def test_enum_entries_have_options(self):
        for entry in config.SETTINGS_SCHEMA:
            if entry['type'] == 'enum':
                assert 'enum_options' in entry, f"{entry['key']} missing enum_options"
                assert len(entry['enum_options']) >= 2, \
                    f"{entry['key']} enum_options should have >=2 choices"

    def test_known_enums_have_correct_options(self):
        by_key = {e['key']: e for e in config.SETTINGS_SCHEMA}
        assert by_key['tts_engine']['enum_options'] == ['piper', 'espeak']
        assert by_key['temp_unit']['enum_options'] == ['C', 'F']
        assert by_key['pressure_unit']['enum_options'] == ['PSI', 'kPa', 'bar']

    def test_sections_table_covers_every_used_section(self):
        sec_keys = {s['key'] for s in config.SETTINGS_SECTIONS}
        used = {e['section'] for e in config.SETTINGS_SCHEMA}
        assert used.issubset(sec_keys), \
            f"Schema uses sections not declared in SETTINGS_SECTIONS: {used - sec_keys}"


class TestValidateSettingsPayload:
    """validate_settings_payload gates POST /api/settings on the
    operator-visible schema. Internal-state flags pass through to
    save_settings (which still gates on SETTINGS_DEFAULTS)."""

    def test_valid_payload_returns_unchanged(self):
        ok, err = config.validate_settings_payload({'temp_unit': 'F', 'voice_min_level': 1})
        assert err is None
        assert ok == {'temp_unit': 'F', 'voice_min_level': 1}

    def test_rejects_invalid_enum(self):
        ok, err = config.validate_settings_payload({'temp_unit': 'K'})
        assert ok is None
        assert err is not None and 'temp_unit' in err

    def test_rejects_int_below_min(self):
        ok, err = config.validate_settings_payload({'voice_min_level': -1})
        assert ok is None
        assert 'voice_min_level' in err

    def test_rejects_int_above_max(self):
        ok, err = config.validate_settings_payload({'voice_min_level': 99})
        assert ok is None
        assert 'voice_min_level' in err

    def test_rejects_bool_for_int_field(self):
        # bool is a subclass of int in Python — must be rejected
        # explicitly so True doesn't sneak through as 1.
        ok, err = config.validate_settings_payload({'voice_min_level': True})
        assert ok is None
        assert 'voice_min_level' in err

    def test_rejects_non_object_body(self):
        ok, err = config.validate_settings_payload(['not', 'an', 'object'])
        assert ok is None
        assert err is not None

    def test_internal_state_flag_passes_through_without_schema_check(self):
        # setup_complete is not in SETTINGS_SCHEMA but is in
        # SETTINGS_DEFAULTS — onboarding flow must still be able to set it.
        ok, err = config.validate_settings_payload({'setup_complete': True})
        assert err is None
        assert ok == {'setup_complete': True}

    def test_float_range_enforced(self):
        ok, err = config.validate_settings_payload({'voltage_undercharge': 99.0})
        assert ok is None
        assert 'voltage_undercharge' in err

    def test_unknown_key_passes_through(self):
        # save_settings still drops it via the SETTINGS_DEFAULTS
        # allowlist; the validator's job is schema enforcement, not
        # allowlist enforcement.
        ok, err = config.validate_settings_payload({'totally_unknown_key': 42})
        assert err is None
