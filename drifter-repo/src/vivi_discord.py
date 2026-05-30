#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Vivi Discord Bot
Bridges Vivi to Discord with slash commands. Pushes alerts to a channel,
accepts text queries that round-trip through vivi_v2, and lets the user
ask "what is the car doing right now?" from anywhere.
UNCAGED TECHNOLOGY — EST 1991
"""

import asyncio
import json
import logging
import os
import signal
import threading
import time
from pathlib import Path
from typing import Optional

import paho.mqtt.client as mqtt

from config import (
    MQTT_HOST, MQTT_PORT, TOPICS,
    DRIFTER_DIR, DISCORD_COMMAND_PREFIX,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [DISCORD] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

CONFIG_PATH = DRIFTER_DIR / "discord.yaml"


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(CONFIG_PATH.read_text()) or {}
    except Exception as e:
        log.warning(f"discord.yaml load failed: {e}")
        return {}


class DiscordBridge:
    def __init__(self, cfg: dict, client: mqtt.Client) -> None:
        self.cfg = cfg
        self.client = client
        self.alert_channel_id = int(cfg.get('alert_channel_id', 0) or 0)
        self.command_channel_id = int(cfg.get('command_channel_id', 0) or 0)
        self.bot = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._pending: dict[str, asyncio.Future] = {}

    async def _start_bot(self) -> None:
        try:
            import discord
            from discord.ext import commands
        except ImportError:
            log.error("discord.py not installed — install: pip install 'discord.py>=2.3'")
            return

        intents = discord.Intents.default()
        intents.message_content = True
        self.bot = commands.Bot(command_prefix=DISCORD_COMMAND_PREFIX + ' ', intents=intents)

        @self.bot.event
        async def on_ready():
            log.info(f"discord ready as {self.bot.user}")
            try:
                synced = await self.bot.tree.sync()
                log.info(f"slash commands synced: {len(synced)}")
            except Exception as e:
                log.warning(f"slash sync failed: {e}")

        @self.bot.tree.command(name="vivi", description="Ask Vivi something")
        async def vivi_cmd(interaction):
            await interaction.response.defer(thinking=True)
            query = interaction.data.get('options', [{}])[0].get('value', '') if interaction.data.get('options') else ''
            response = await self._ask_vivi(query or "what's the car doing?")
            await interaction.followup.send(response or "(no response)")

        @self.bot.tree.command(name="status", description="Get current Drifter status")
        async def status_cmd(interaction):
            await interaction.response.defer(thinking=True)
            response = await self._ask_vivi("Give me a one-sentence status of the vehicle.")
            await interaction.followup.send(response or "(offline)")

        @self.bot.event
        async def on_message(message):
            if message.author == self.bot.user:
                return
            if message.content.lower().startswith(DISCORD_COMMAND_PREFIX.lower()):
                query = message.content[len(DISCORD_COMMAND_PREFIX):].strip()
                async with message.channel.typing():
                    response = await self._ask_vivi(query)
                await message.reply(response or "(no response)")
            await self.bot.process_commands(message)

        token = self.cfg.get('bot_token') or os.environ.get('DISCORD_BOT_TOKEN', '')
        if not token:
            log.error("no discord bot_token — set DISCORD_BOT_TOKEN env or discord.yaml")
            return
        await self.bot.start(token)

    async def _ask_vivi(self, query: str) -> str:
        req_id = f"discord-{int(time.time()*1000)}"
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[req_id] = fut
        self.client.publish(TOPICS['vivi2_query'], json.dumps({
            'query': query, 'request_id': req_id, 'source': 'discord',
        }))
        try:
            return await asyncio.wait_for(fut, timeout=20)
        except asyncio.TimeoutError:
            return "Vivi timed out."
        finally:
            self._pending.pop(req_id, None)

    def push_alert(self, payload: dict) -> None:
        if not self.bot or not self.alert_channel_id or not self.loop:
            return
        try:
            chan = self.bot.get_channel(self.alert_channel_id)
            if chan:
                level = payload.get('level', 1)
                emoji = {0: '🟢', 1: '🟡', 2: '🟠', 3: '🔴'}.get(level, '⚪')
                msg = f"{emoji} **DRIFTER** {payload.get('message', '')}"
                asyncio.run_coroutine_threadsafe(chan.send(msg), self.loop)
        except Exception as e:
            log.debug(f"push_alert failed: {e}")

    def on_vivi_response(self, payload: dict) -> None:
        req_id = payload.get('request_id')
        if not req_id or req_id not in self._pending:
            return
        fut = self._pending.get(req_id)
        if fut and self.loop and not fut.done():
            self.loop.call_soon_threadsafe(fut.set_result, payload.get('response', ''))


def main() -> None:
    log.info("DRIFTER Vivi Discord starting...")
    cfg = _load_config()

    running = [True]

    def _handle_signal(sig, frame):
        running[0] = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-discord")
    bridge = DiscordBridge(cfg, client)

    def on_message(_c, _u, msg) -> None:
        try:
            data = json.loads(msg.payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if not isinstance(data, dict):
            return
        if msg.topic == TOPICS['alert_message']:
            bridge.push_alert(data)
        elif msg.topic == TOPICS['safety_alert']:
            bridge.push_alert(data)
        elif msg.topic == TOPICS['vivi2_response']:
            bridge.on_vivi_response(data)
        elif msg.topic == TOPICS['discord_outbound']:
            bridge.push_alert(data)

    client.on_message = on_message

    connected = False
    while not connected and running[0]:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            connected = True
        except Exception as e:
            log.warning(f"Waiting for MQTT broker... ({e})")
            time.sleep(3)

    if not running[0]:
        return

    client.subscribe([
        (TOPICS['alert_message'], 0),
        (TOPICS['safety_alert'], 0),
        (TOPICS['vivi2_response'], 0),
        (TOPICS['discord_outbound'], 1),
    ])
    client.loop_start()
    client.publish(TOPICS['discord_status'], json.dumps({'status': 'up', 'ts': time.time()}), retain=True)
    log.info("Discord Bridge LIVE")

    def _run_bot():
        loop = asyncio.new_event_loop()
        bridge.loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(bridge._start_bot())
        except Exception as e:
            log.error(f"bot crashed: {e}")
        finally:
            loop.close()

    threading.Thread(target=_run_bot, daemon=True).start()

    while running[0]:
        time.sleep(1)

    client.publish(TOPICS['discord_status'], json.dumps({'status': 'down', 'ts': time.time()}), retain=True)
    client.loop_stop()
    client.disconnect()
    log.info("Discord Bridge stopped")


if __name__ == '__main__':
    main()
