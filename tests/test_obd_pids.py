# tests/test_obd_pids.py
"""
The canonical OBD-II PID registry (obd_pids.py) is the single source of truth
both transports build from. These tests pin the decode semantics and the
table-derivation helpers so the two bridges can never drift apart again.
"""
import sys

sys.path.insert(0, 'src')

import obd_pids


class TestTableDerivation:
    def test_can_pids_shape(self):
        pids = obd_pids.can_pids()
        # int-keyed, each entry has the fields can_bridge/can_native expect.
        assert 0x0C in pids
        for spec in pids.values():
            assert set(spec) == {'name', 'topic', 'decode', 'unit', 'hz'}

    def test_obd_defs_are_elm_command_keyed(self):
        defs = obd_pids.obd_pid_defs()
        assert '010C' in defs
        assert '0105' in defs
        for cmd, spec in defs.items():
            assert cmd.startswith('01')
            assert set(spec) == {'name', 'topic', 'decode', 'unit', 'pid'}
            assert cmd == f"01{spec['pid']:02X}"

    def test_both_transports_cover_same_pids(self):
        can_keys = set(obd_pids.can_pids())
        obd_keys = {d['pid'] for d in obd_pids.obd_pid_defs().values()}
        assert can_keys == obd_keys == set(obd_pids.PID_TABLE)

    def test_two_byte_pids_derived(self):
        two = obd_pids.two_byte_pids()
        # rpm / maf / run_time / voltage all need both A and B.
        assert {0x0C, 0x10, 0x1F, 0x42} <= two
        # coolant / speed are single-byte.
        assert 0x05 not in two and 0x0D not in two


class TestDecodeSemantics:
    """Byte-for-byte identical to the historical hand-copied tables."""

    def _can(self, pid):
        return obd_pids.can_pids()[pid]['decode']

    def _obd(self, pid):
        cmd = f"01{pid:02X}"
        return obd_pids.obd_pid_defs()[cmd]['decode']

    def test_rpm(self):
        # ((A*256)+B)/4 ; 3000 rpm -> A=0x2E B=0xE0
        assert abs(self._can(0x0C)([0x2E, 0xE0]) - 3000.0) < 0.1
        assert abs(self._obd(0x0C)([0x2E, 0xE0]) - 3000.0) < 0.1

    def test_coolant(self):
        assert self._can(0x05)([0x82]) == 90
        assert self._obd(0x05)([0x82]) == 90

    def test_stft(self):
        assert abs(self._can(0x06)([0x80]) - 0.0) < 0.5

    def test_maf(self):
        # ((A*256)+B)/100 ; A=0x01 B=0x00 -> 2.56 g/s
        assert self._can(0x10)([0x01, 0x00]) == 2.56

    def test_voltage(self):
        # ((A*256)+B)/1000 ; 0x37 0x70 -> 14.192 -> round 2
        assert self._can(0x42)([0x37, 0x70]) == 14.19

    def test_can_and_obd_decoders_agree_everywhere(self):
        for pid in obd_pids.PID_TABLE:
            data = [0x40, 0x20]  # arbitrary two bytes, enough for any decoder
            assert self._can(pid)(data) == self._obd(pid)(data)


class TestCommonStandardPids:
    """MAP (for MAF-less cars) + other common standard PIDs are present and
    decode to standard SAE scaling."""

    def _dec(self, pid):
        return obd_pids.can_pids()[pid]['decode']

    def test_map_present_and_decodes_kpa(self):
        assert 0x0B in obd_pids.PID_TABLE
        # MAP is a raw kPa byte.
        assert self._dec(0x0B)([0x64]) == 100
        assert obd_pids.PID_TABLE[0x0B].name == 'map'

    def test_map_is_combustion_only(self):
        # A pure EV has no intake manifold — MAP must drop out for EVs.
        assert 0x0B not in obd_pids.applies_to('ev')

    def test_ambient_air_temp(self):
        assert self._dec(0x46)([0x50]) == 40  # 0x50 - 40
        # Ambient air temp is not engine-specific → available on any powertrain.
        assert 0x46 in obd_pids.applies_to('ev')

    def test_oil_temp(self):
        assert self._dec(0x5C)([0x78]) == 80  # 0x78 - 40

    def test_fuel_pressure(self):
        assert self._dec(0x0A)([0x64]) == 300  # A * 3

    def test_fuel_rate(self):
        # ((A*256)+B)/20 ; A=0x00 B=0x64 -> 5.0 L/h
        assert self._dec(0x5E)([0x00, 0x64]) == 5.0


class TestPowertrainGate:
    def test_ev_drops_combustion_only_pids(self):
        ev = obd_pids.applies_to('ev')
        # combustion-only PIDs (coolant, MAF, fuel-trim, O2) are excluded...
        assert 0x05 not in ev  # coolant
        assert 0x10 not in ev  # MAF
        assert 0x06 not in ev  # STFT1
        assert 0x14 not in ev  # O2
        # ...but universal PIDs (rpm, speed, voltage) + battery life stay.
        assert {0x0C, 0x0D, 0x42, 0x5B} <= ev

    def test_ice_keeps_combustion_not_battery(self):
        # petrol/diesel keep everything EXCEPT the electrified-only battery PID.
        expected = set(obd_pids.PID_TABLE) - {0x5B}
        assert obd_pids.applies_to('petrol') == expected
        assert obd_pids.applies_to('diesel') == expected
        assert obd_pids.applies_to(None) == expected

    def test_hybrid_keeps_everything(self):
        # a hybrid has both an engine and a traction battery.
        assert obd_pids.applies_to('hybrid') == set(obd_pids.PID_TABLE)


class TestElectrifiedPids:
    def test_hybrid_battery_life_present(self):
        assert 0x5B in obd_pids.PID_TABLE
        p = obd_pids.PID_TABLE[0x5B]
        assert p.name == 'hybrid_batt_life'
        assert p.applies == obd_pids.HYBRID
        # A*100/255 ; 0xFF -> 100%
        assert obd_pids.can_pids()[0x5B]['decode']([0xFF]) == 100.0
        assert obd_pids.can_pids()[0x5B]['decode']([0x80]) == 50.2


class TestSupportBitmaps:
    def test_decode_bitmap(self):
        # Set support bits for 0x04, 0x05, 0x0C in the 0x00 group.
        val = (1 << (31 - 3)) | (1 << (31 - 4)) | (1 << (31 - 11))
        supported = obd_pids.supported_from_bitmaps({0x00: val})
        assert supported == {0x04, 0x05, 0x0C}

    def test_second_group(self):
        # 0x2F lives in the 0x20 group: index = 0x2F - 0x20 - 1 = 14.
        val = 1 << (31 - 14)
        assert obd_pids.supported_from_bitmaps({0x20: val}) == {0x2F}

    def test_unknown_pids_ignored(self):
        # Bit for a PID we don't decode (0x01) must not appear.
        val = 1 << 31  # first PID after 0x00 == 0x01 (not in table)
        assert obd_pids.supported_from_bitmaps({0x00: val}) == set()

    def test_empty_bitmap(self):
        assert obd_pids.supported_from_bitmaps({0x00: 0}) == set()
