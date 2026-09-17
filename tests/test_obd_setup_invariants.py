"""Regression tests for the field-facing ELM327 setup path."""

import obd_setup


class FakeELM:
    def __init__(self):
        self.commands = []
        self._reply = b">"

    def reset_input_buffer(self):
        return None

    def write(self, payload: bytes):
        command = payload.decode("ascii").strip().upper()
        self.commands.append(command)
        replies = {
            "ATZ": "ELM327 v1.5\r>",
            "ATE0": "OK\r>",
            "ATL0": "OK\r>",
            "ATS1": "OK\r>",
            "ATH0": "OK\r>",
            "ATSP0": "OK\r>",
            "ATI": "ELM327 v1.5\r>",
            "0100": "41 00 BE 3E B8 13\r>",
            "ATDP": "ISO 9141-2\r>",
            "010C": "41 0C 0C 80\r>",
        }
        self._reply = replies.get(command, "?\r>").encode("ascii")

    def read(self, _size: int):
        reply, self._reply = self._reply, b""
        return reply

    def close(self):
        return None


def test_probe_keeps_spaced_responses_for_runtime_parser(monkeypatch):
    elm = FakeELM()
    monkeypatch.setattr(obd_setup.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        obd_setup.elm_link,
        "open_elm_link",
        lambda _cfg: (elm, "fake ELM"),
    )

    result = obd_setup.probe(obd_setup._cfg_serial("/dev/fake"))

    assert result["adapter_ok"] is True
    assert result["ecu_ok"] is True
    assert result["rpm"] == 800.0
    assert "ATS1" in elm.commands
    assert "ATS0" not in elm.commands
