#!/usr/bin/env python3
"""MZ1312 DRIFTER — isolated ELM / MQTT integration bench.
Runs actual service processes against a fragmented TCP ELM emulator and a
loopback-only broker. Never opens Bluetooth, serial, SocketCAN or a vehicle.
Requires the dev environment plus amqtt. Results are synthetic bench evidence.
UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt
from amqtt.broker import Broker

ROOT = Path(__file__).resolve().parents[1]
logging.basicConfig(level=logging.ERROR)


def unused_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class Elm:
    def __init__(self):
        self.drop_next = False
        self.no_ecu = False
        self.connections = 0
        self.requests = []

    async def handle(self, reader, writer):
        self.connections += 1
        try:
            while True:
                command = (await reader.readuntil(b'\r')).decode().strip().upper()
                self.requests.append(command)
                if self.drop_next and command.startswith('01'):
                    self.drop_next = False
                    return
                if command == 'ATZ': reply = 'ELM327 v1.5\r'
                elif command == 'ATDPN': reply = '3\r'
                elif command.startswith('AT'): reply = 'OK\r'
                elif self.no_ecu: reply = 'NO DATA\r'
                elif command in ('0100', '0120', '0140'):
                    base = int(command[2:], 16)
                    supported = (5, 12, 13, 17, 24, 32, 64, 66)
                    bits = sum(1 << (32 - (pid - base)) for pid in supported if base < pid <= base + 32)
                    reply = f'41{base:02X}{bits:08X}\r'
                elif command in ('0105', '010C', '010D', '0111', '0118', '0142'):
                    value = {'0105': '82', '010C': '0C80', '010D': '00',
                             '0111': '03', '0118': '6480', '0142': '364C'}[command]
                    reply = '41' + command[2:] + value + '\r'
                elif command == '03': reply = '43 01 71 00 00 00 00\r'
                elif command == '07': reply = '47 00 00 00 00 00 00\r'
                elif command == '0902':
                    padded = b'\x00\x00\x00SAJEA51D44XD39283'
                    reply = '\r'.join(f'49 02 {i + 1:02X} ' + padded[i*4:i*4+4].hex(' ')
                                      for i in range(5)) + '\r'
                else: reply = 'NO DATA\r'
                raw = (reply + '>').encode()
                # A prompt and payload split across recv() calls, as on real
                # low-cost adapters. Compact replies deliberately ignore ATS1.
                for part in (raw[:2], raw[2:-1], raw[-1:]):
                    writer.write(part)
                    await writer.drain()
                    await asyncio.sleep(0.003)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()


async def run(output: Path):
    output.mkdir(parents=True, exist_ok=True)
    runtime = output / 'runtime'
    runtime.mkdir(exist_ok=True)
    broker_port = unused_port()
    config = {'listeners': {'default': {'type': 'tcp', 'bind': f'127.0.0.1:{broker_port}'}},
              'plugins': {'amqtt.plugins.authentication.AnonymousAuthPlugin': {'allow_anonymous': True}}}
    broker = Broker(config)
    await broker.start()
    elm = Elm()
    server = await asyncio.start_server(elm.handle, '127.0.0.1', 0)
    elm_port = server.sockets[0].getsockname()[1]
    messages = []
    lock = threading.Lock()
    observer = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='bench-observer')
    observer.on_connect = lambda c, u, f, rc, p: c.subscribe('drifter/#') if rc == 0 else None

    def receive(c, u, msg):
        with lock:
            messages.append((time.monotonic(), msg.topic, json.loads(msg.payload)))

    observer.on_message = receive
    observer.connect('127.0.0.1', broker_port)
    observer.loop_start()
    env = {**os.environ, 'PYTHONPATH': str(ROOT / 'src'), 'DRIFTER_DIR': str(runtime),
           'DRIFTER_MQTT_HOST': '127.0.0.1', 'DRIFTER_MQTT_PORT': str(broker_port),
           'DRIFTER_TRANSPORT': 'elm327', 'DRIFTER_ELM_LINK': 'wifi',
           'ELM_WIFI_HOST': '127.0.0.1', 'ELM_WIFI_PORT': str(elm_port), 'OBD_POLL_HZ': '5'}
    processes, logs = [], []
    checks = []

    def snapshot():
        with lock:
            return list(messages)

    async def wait_for(label, predicate, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate(snapshot()):
                checks.append(label)
                print('PASS ' + label, flush=True)
                return
            if any(p.poll() is not None for p in processes):
                raise AssertionError('A service exited; inspect bench logs')
            await asyncio.sleep(0.05)
        raise AssertionError('Timed out: ' + label)

    try:
        for module in ('obd_bridge_multi', 'can_bridge', 'logger', 'alert_engine', 'safety_engine', 'telemetry_batcher'):
            log = (output / (module + '.log')).open('w')
            logs.append(log)
            processes.append(subprocess.Popen([sys.executable, str(ROOT / 'src' / (module + '.py'))],
                                              env=env, stdout=log, stderr=subprocess.STDOUT))
        await wait_for('decoded RPM through real TCP and MQTT', lambda rows: any(
            t == 'drifter/engine/rpm' and d.get('value') == 800 for _, t, d in rows))
        await wait_for('K-line DTC and VIN', lambda rows: any(
            t == 'drifter/diag/dtc' and d.get('stored') == ['P0171'] for _, t, d in rows) and any(
            t == 'drifter/obd/vin' and d.get('vin') == 'SAJEA51D44XD39283' for _, t, d in rows))
        capture = await asyncio.create_subprocess_exec(
            sys.executable, str(ROOT / 'src' / 'vehicle_check.py'), '--seconds', '10',
            '--engine-running', '--output', str(output / 'continuity.json'), env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        stdout, _ = await capture.communicate()
        if capture.returncode:
            raise AssertionError(stdout.decode())
        checks.append('vehicle-check continuity gate')
        print('PASS vehicle-check continuity gate', flush=True)
        before = time.monotonic()
        elm.drop_next = True
        await wait_for('EOF reported as disconnected', lambda rows: any(
            at > before and t == 'drifter/obd/status' and d.get('state') == 'ecu_pending'
            for at, t, d in rows))
        await wait_for('adapter reconnect resumes measured RPM', lambda rows: any(
            at > before + 4 and t == 'drifter/engine/rpm' for at, t, d in rows))
        before = time.monotonic()
        await broker.shutdown()
        broker = Broker(config)
        await broker.start()
        await wait_for('broker restart restores telemetry subscriptions', lambda rows: any(
            at > before + 1 and t == 'drifter/engine/rpm' for at, t, d in rows), timeout=25)
        await wait_for('alert subscriber recovers after broker restart', lambda rows: any(
            at > before + 1 and t == 'drifter/alert/active' and d.get('alerts')
            for at, t, d in rows), timeout=25)
        before = time.monotonic()
        elm.no_ecu = True
        await wait_for('lost ECU reported without recycling stale samples', lambda rows: any(
            at > before + 10 and t == 'drifter/obd/status' and d.get('state') == 'ecu_pending'
            for at, t, d in rows), timeout=25)
        stale_rows = [d for at, t, d in snapshot() if at > before + 2 and t == 'drifter/snapshot']
        assert not stale_rows, stale_rows
        metrics = [d for _, t, d in snapshot() if t == 'drifter/engine/rpm']
        assert {d['source'] for d in metrics} == {'obd_bridge'}
        checks.extend(['no snapshots fabricated during ECU loss', 'single telemetry owner with both bridges running'])
        assert not any(cmd.startswith(('04', '08', '2E', '31')) for cmd in elm.requests)
        checks.append('only adapter setup and read-only OBD requests sent')
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                await asyncio.to_thread(process.wait, timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait)
        for log in logs:
            log.close()
        observer.loop_stop()
        observer.disconnect()
        server.close()
        await server.wait_closed()
        await broker.shutdown()
    log_files = list((runtime / 'logs').glob('drive_*.jsonl'))
    assert log_files and any('drifter/engine/rpm' in path.read_text() for path in log_files)
    checks.append('logger persisted real pipeline messages')
    report = {'synthetic_bench': True, 'passed': True, 'checks': checks,
              'elm_connections': elm.connections, 'requests': len(elm.requests)}
    (output / 'bench-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    asyncio.run(run(parser.parse_args().output_dir.resolve()))
