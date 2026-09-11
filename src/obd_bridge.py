#!/usr/bin/env python3
"""MZ1312 DRIFTER — ELM327 vehicle telemetry and diagnostics.
One read-only poller for USB, Bluetooth Classic RFCOMM and Wi-Fi TCP, with
prompt framing, ECU discovery, bounded polling, freshness and reconnects.
On a K-line vehicle drifter-obdbridge owns telemetry; drifter-canbridge idles.
UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import json
import logging
import os
import signal
import time

import elm_link
import elm_protocol
import obd_transport
import vehicle_profile
from config import (
    MQTT_HOST,
    MQTT_PORT,
    OBD_POLL_HZ,
    OBD_SERIAL_BAUD,
    OBD_SERIAL_DEV,
    TOPICS,
    make_mqtt_client,
)
from obd_pids import PID_TABLE, SUPPORT_PROBE_PIDS, applies_to, obd_pid_defs, supported_from_bitmaps

logging.basicConfig(level=logging.INFO, format='%(asctime)s [OBDBRIDGE] %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger(__name__)
PID_DEFS = obd_pid_defs()
STALE_SECONDS = 15.0
RETRY_SECONDS = 5.0
DTC_INTERVAL = 60.0
_ELM_PROTO_NAMES = {
    '0': 'auto (not yet determined)', '1': 'SAE J1850 PWM', '2': 'SAE J1850 VPW',
    '3': 'ISO 9141-2 (K-line)', '4': 'ISO 14230-4 KWP 5-baud (K-line)',
    '5': 'ISO 14230-4 KWP fast (K-line)', '6': 'ISO 15765-4 CAN (11-bit, 500k)',
    '7': 'ISO 15765-4 CAN (29-bit, 500k)', '8': 'ISO 15765-4 CAN (11-bit, 250k)',
    '9': 'ISO 15765-4 CAN (29-bit, 250k)', 'A': 'SAE J1939 CAN',
}


def _open_elm():
    try:
        cfg = elm_link.LinkConfig.from_env(serial_dev=OBD_SERIAL_DEV, serial_baud=OBD_SERIAL_BAUD)
        stream, label = elm_link.open_elm_link(cfg, initializer=elm_protocol.initialize)
        stream.drifter_label = label
        log.info('ELM adapter connected via %s; waiting for ECU', label)
        return stream
    except Exception as exc:
        log.warning('ELM connection failed: %s', exc)
        return None


def _protocol_number(ser) -> str:
    raw = elm_protocol.command(ser, 'ATDPN')
    for line in raw.replace('\r', '\n').splitlines():
        token = line.strip().upper()
        if len(token) == 2 and token[0] == 'A':
            token = token[1:]
        if token in _ELM_PROTO_NAMES:
            return token
    return ''


def detect_protocol(ser) -> str:
    try:
        return _ELM_PROTO_NAMES.get(_protocol_number(ser), 'unknown')
    except OSError:
        return 'unknown'


def _query_pid(ser, pid: str, timeout: float = 3.0) -> list | None:
    number = int(pid[2:], 16)
    spec = PID_TABLE.get(number)
    nbytes = 4 if number in SUPPORT_PROBE_PIDS else spec.nbytes if spec else 1
    raw = elm_protocol.command(ser, pid, timeout=timeout)
    return elm_protocol.pid_data(raw, number, nbytes)


def query_supported_pids(ser):
    """A real zero bitmap is an empty set, never a reason to poll everything."""
    bitmaps = {}
    for probe in SUPPORT_PROBE_PIDS:
        data = _query_pid(ser, f'01{probe:02X}', timeout=30.0 if probe == 0 else 3.0)
        if data is None:
            break
        bitmap = int.from_bytes(bytes(data[:4]), 'big')
        bitmaps[probe] = bitmap
        if not bitmap & 1:
            break  # this ECU did not advertise another support page
    return supported_from_bitmaps(bitmaps) if bitmaps else None


def active_pid_defs(ser):
    supported = query_supported_pids(ser)
    if supported is None:
        supported = applies_to(vehicle_profile.fuel_type())
    return {cmd: spec for cmd, spec in PID_DEFS.items() if spec['pid'] in supported}


def fresh_snapshot(values: dict, sample_ts: dict, now: float) -> dict:
    keys = [key for key in values if 0 <= now - sample_ts.get(key, 0) <= STALE_SECONDS]
    return {**{key: values[key] for key in keys},
            'sample_ts': {key: sample_ts[key] for key in keys},
            'ts': now, 'source': 'obd_bridge'}


def _idle(running_ref, seconds=RETRY_SECONDS):
    deadline = time.monotonic() + seconds
    while running_ref() and time.monotonic() < deadline:
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))


def _publish_status(client, state, stream=None, **extra):
    label = getattr(stream, 'drifter_label', '')
    client.publish(TOPICS['obd_status'], json.dumps({
        'state': state, 'device': label, 'link': label.split(':', 1)[0],
        'ts': time.time(), **extra,
    }), retain=True)


def read_dtcs(stream, protocol: str) -> dict:
    can_protocol = protocol in {'6', '7', '8', '9'}
    result = {}
    for key, mode in (('stored', 3), ('pending', 7)):
        raw = elm_protocol.command(stream, f'{mode:02X}')
        # Without a negotiated protocol we cannot distinguish the CAN count
        # byte from a K-line DTC byte. Do not turn that ambiguity into codes.
        codes = (elm_protocol.dtc_codes(raw, mode, can_protocol=can_protocol)
                 if protocol in {'1', '2', '3', '4', '5', '6', '7', '8', '9'} else None)
        result[key] = codes
        result[key + '_available'] = codes is not None
    result['count'] = sum(len(result[k] or []) for k in ('stored', 'pending'))
    result['ts'] = time.time()
    result['source'] = 'obd_bridge'
    return result


def main() -> None:
    running = True

    def stop(_sig, _frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    client = make_mqtt_client('drifter-obdbridge')
    client.will_set(TOPICS['obd_status'], json.dumps({'state': 'offline', 'reason': 'connection_lost'}), retain=True)
    while running:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            break
        except OSError as exc:
            log.warning('Waiting for MQTT: %s', exc)
            _idle(lambda: running, 3)
    if not running:
        return
    client.loop_start()
    stream = None
    lease = None
    values, sample_ts = {}, {}
    last_snapshot = last_status = last_success = 0.0
    last_dtc = 0.0
    cursor = 0
    schedule = []
    protocol = ''
    vin_pending = False
    try:
        # Aggregate request ceiling, not a promise of per-PID Hz. Physical
        # adapter response time can only lower the achieved rate.
        rate = float(os.getenv('OBD_POLL_HZ', str(OBD_POLL_HZ)))
        if not 0.5 <= rate <= 10:
            raise ValueError('OBD_POLL_HZ must be between 0.5 and 10')
        while running:
            try:
                if obd_transport.select_transport() != obd_transport.ELM327:
                    if stream is not None:
                        stream.close()
                        stream = None
                        values.clear()
                        sample_ts.clear()
                    if lease is not None:
                        lease.close()
                        lease = None
                    _publish_status(client, 'hw_pending', reason='deferring_to_canbridge')
                    _idle(lambda: running)
                    continue
                if lease is None:
                    lease = obd_transport.acquire_telemetry_lease()
                    if lease is None:
                        _publish_status(client, 'hw_pending', reason='waiting_for_telemetry_owner')
                        _idle(lambda: running, 1)
                        continue
                if stream is None:
                    stream = _open_elm()
                    if stream is None:
                        _publish_status(client, 'hw_pending', reason='adapter_unavailable')
                        _idle(lambda: running)
                        continue
                    _publish_status(client, 'connecting', stream, reason='protocol_search')
                    supported = query_supported_pids(stream)
                    protocol = _protocol_number(stream)  # only after an ECU request negotiated the bus
                    if not supported:
                        _publish_status(client, 'ecu_pending', stream,
                                        protocol=_ELM_PROTO_NAMES.get(protocol, 'unknown'),
                                        reason='no_ecu_response' if supported is None else 'no_supported_metrics')
                        stream.close()
                        stream = None
                        _idle(lambda: running)
                        continue
                    active = [cmd for cmd, spec in PID_DEFS.items() if spec['pid'] in supported]
                    if not active:
                        raise elm_protocol.ELMError('ECU supports no DRIFTER metric PIDs')
                    # Interleave critical gauges with the rest so they don't all
                    # wait behind fuel trims and oxygen sensors at startup.
                    primary = [cmd for cmd in ('010C', '010D', '0105', '0142') if cmd in active]
                    schedule = primary + [cmd for cmd in active if cmd not in primary]
                    cursor = 0
                    values.clear()
                    sample_ts.clear()
                    last_success = time.monotonic()
                    last_snapshot = last_status = last_dtc = 0.0
                    vin_pending = True
                    _publish_status(client, 'ecu_connected', stream,
                                    protocol=_ELM_PROTO_NAMES.get(protocol, 'unknown'), supported_pids=active)
                if not client.is_connected():
                    # Do not build an unbounded MQTT publish queue during an
                    # outage. Reconnect to the ECU on broker recovery.
                    raise elm_protocol.ELMError('MQTT broker disconnected')
                started = time.monotonic()
                pid = schedule[cursor % len(schedule)]
                cursor += 1
                data = _query_pid(stream, pid)
                if data is not None:
                    spec = PID_DEFS[pid]
                    value = spec['decode'](data)
                    ts = time.time()
                    values[spec['name']] = value
                    sample_ts[spec['name']] = ts
                    last_success = time.monotonic()
                    payload = {'value': value, 'unit': spec['unit'], 'ts': ts, 'source': 'obd_bridge'}
                    client.publish(spec['topic'], json.dumps(payload))
                    client.publish(TOPICS['obd_pid'], json.dumps({'pid': pid, **payload}))
                    if last_success - last_snapshot >= 1:
                        client.publish(TOPICS['snapshot'], json.dumps(fresh_snapshot(values, sample_ts, ts)))
                        last_snapshot = last_success
                    if last_success - last_status >= 5:
                        _publish_status(client, 'online', stream,
                                        protocol=_ELM_PROTO_NAMES.get(protocol, 'unknown'),
                                        last_ecu_response=ts, supported_pids=schedule)
                        last_status = last_success
                elif time.monotonic() - last_success >= STALE_SECONDS:
                    raise elm_protocol.ELMError('No valid ECU metrics for 15 seconds')
                # Complete a first metric sweep before optional diagnostics.
                if cursor % len(schedule) == 0:
                    if time.monotonic() - last_dtc >= DTC_INTERVAL:
                        client.publish(TOPICS['dtc'], json.dumps(read_dtcs(stream, protocol)), retain=True)
                        last_dtc = time.monotonic()
                    elif vin_pending:
                        vin = elm_protocol.vin_from_reply(elm_protocol.command(stream, '0902', timeout=6))
                        client.publish(TOPICS['obd_vin'], json.dumps({'vin': vin, 'source': 'obd_bridge', 'ts': time.time()}), retain=True)
                        vin_pending = False
                _idle(lambda: running, max(0, 1 / rate - (time.monotonic() - started)))
            except (OSError, RuntimeError, ValueError) as exc:
                log.warning('OBD waiting/reconnecting: %s', exc)
                _publish_status(client, 'ecu_pending', stream, reason=str(exc))
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
                    stream = None
                values.clear()
                sample_ts.clear()
                _idle(lambda: running)
    finally:
        if stream is not None:
            stream.close()
        if lease is not None:
            lease.close()
        _publish_status(client, 'offline')
        client.loop_stop()
        client.disconnect()


if __name__ == '__main__':
    main()
