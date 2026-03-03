"""
DataUpdateCoordinator for the Euhomy BLE integration.

Manages the BLE connection lifecycle, periodic heartbeats and reconnection.
DP updates are delivered via the push callback from TuyaBLEClient, so the
coordinator itself does not poll – it only keeps the connection alive.
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    DP_BATTERY_PROT,
    DP_BATTERY_VOLTAGE,
    DP_FAULT,
    DP_LOCK,
    DP_MODE,
    DP_SWITCH,
    DP_TEMP_CURRENT,
    DP_TEMP_SET,
    DP_TEMP_UNIT,
    DP_TYPE_BITMAP,
    DP_TYPE_BOOL,
    DP_TYPE_ENUM,
    DP_TYPE_INT,
    DOMAIN,
    MODE_ECO,
    MODE_MAX,
)
from .models import EuhomyData, EuhomyState
from .tuya_ble import TuyaBLEClient, TuyaDP

_LOGGER = logging.getLogger(__name__)

# How often (seconds) to send a BLE heartbeat to keep the connection alive.
HEARTBEAT_INTERVAL = 20
# How long (seconds) to wait before attempting a reconnect after dropout.
RECONNECT_DELAY = 5


class EuhomyBLECoordinator(DataUpdateCoordinator[EuhomyState]):
    """
    Coordinator that owns the TuyaBLEClient and keeps it healthy.

    Entities call coordinator.data to read state and call coordinator methods
    (set_power, set_temperature, …) to issue commands.
    """

    def __init__(self, hass: HomeAssistant, entry_data: EuhomyData) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry_data.address}",
            # No polling interval – updates arrive as BLE push notifications.
        )
        self._entry_data    = entry_data
        self._client        = entry_data.client
        self._hb_task:       asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._reconnecting  = False
        self._stopping      = False   # set True during async_stop to block reconnects
        self._scan_verbose  = False   # set True during scan_dps to log all DPs as WARNING

        # Wire the DP callback from the BLE client to our handler.
        self._client._on_dp_update    = self._handle_dp_update
        self._client._on_disconnect_cb = self._handle_disconnect

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def async_start(self) -> None:
        """Connect and start the heartbeat loop."""
        self._stopping = False
        ok = await self._try_connect()
        if not ok and not self._reconnecting:
            self._start_reconnect_loop()

    async def async_stop(self) -> None:
        """Stop all tasks and disconnect cleanly — never triggers a reconnect."""
        self._stopping = True
        # Disconnect callback must be cleared BEFORE calling disconnect(),
        # otherwise bleak fires it and our handler would spawn a new reconnect task.
        self._client._on_disconnect_cb = None
        if self._reconnect_task:
            self._reconnect_task.cancel()
            self._reconnect_task = None
        if self._hb_task:
            self._hb_task.cancel()
            self._hb_task = None
        await self._client.disconnect()

    # ── Commands ──────────────────────────────────────────────────────────────

    def _check_connected(self) -> bool:
        if not self._client.connected:
            _LOGGER.warning("Command ignored — device not connected (reconnect in progress)")
            return False
        return True

    async def async_scan_dps(self) -> None:
        """Query all DPs and log every one as WARNING for 5 seconds."""
        _LOGGER.warning("=== DP SCAN started — all incoming DPs will be logged ===")
        self._scan_verbose = True
        await self._client.query_dps()

        async def _reset() -> None:
            await asyncio.sleep(5)
            self._scan_verbose = False
            _LOGGER.warning("=== DP SCAN ended ===")

        self.hass.async_create_background_task(_reset(), name=f"{DOMAIN}_scan_reset")

    async def async_set_power(self, on: bool) -> None:
        if not self._check_connected():
            return
        self._entry_data.state.power = on  # optimistic update
        await self._client.publish_dp(
            TuyaDP(dp_id=DP_SWITCH, dp_type=DP_TYPE_BOOL, value=on)
        )

    async def async_set_temperature(self, temp_c: int) -> None:
        if not self._check_connected():
            return
        self._entry_data.state.temp_set = temp_c  # optimistic update for HA slider
        await self._client.publish_dp(
            TuyaDP(dp_id=DP_TEMP_SET, dp_type=DP_TYPE_INT, value=temp_c)
        )

    async def async_set_mode(self, mode: str) -> None:
        if not self._check_connected():
            return
        self._entry_data.state.mode = mode  # optimistic update
        # Device expects ENUM byte: \x00=MAX, \x01=ECO
        raw = "\x01" if mode == MODE_ECO else "\x00"
        await self._client.publish_dp(
            TuyaDP(dp_id=DP_MODE, dp_type=DP_TYPE_ENUM, value=raw)
        )

    async def async_set_lock(self, locked: bool) -> None:
        self._entry_data.state.lock = locked  # optimistic update
        raw = "\x01" if locked else "\x00"
        await self._client.publish_dp(
            TuyaDP(dp_id=DP_LOCK, dp_type=DP_TYPE_ENUM, value=raw)
        )

    async def async_set_temp_unit(self, unit: str) -> None:
        """Set temperature unit: 'c' (Celsius) or 'f' (Fahrenheit). Sends raw ENUM byte."""
        if not self._check_connected():
            return
        self._entry_data.state.temp_unit = unit  # optimistic update
        raw_byte = b"\x01" if unit == "f" else b"\x00"
        await self._client.publish_dp(
            TuyaDP(dp_id=DP_TEMP_UNIT, dp_type=DP_TYPE_ENUM, value=raw_byte.decode("latin-1"))
        )

    async def async_set_battery_protection(self, level: str) -> None:
        """Set battery protection level: 'h' (high), 'm' (medium), 'l' (low)."""
        if not self._check_connected():
            return
        self._entry_data.state.battery_prot = level  # optimistic update
        _bp = {"l": "\x00", "m": "\x01", "h": "\x02"}
        raw = _bp.get(level, "\x01")
        await self._client.publish_dp(
            TuyaDP(dp_id=DP_BATTERY_PROT, dp_type=DP_TYPE_ENUM, value=raw)
        )

    # ── Internal ──────────────────────────────────────────────────────────────

    def _start_reconnect_loop(self) -> None:
        """Spawn the reconnect background task (guarded against double-start)."""
        if self._stopping or self._reconnecting:
            return
        self._reconnecting = True
        _LOGGER.warning("BLE connection lost — will retry every %ds", RECONNECT_DELAY)
        self._reconnect_task = self.hass.async_create_background_task(
            self._reconnect_loop(), name=f"{DOMAIN}_reconnect"
        )

    async def _try_connect(self) -> bool:
        """Attempt one connection; start heartbeat on success. Never creates tasks."""
        ok = await self._client.connect()
        if ok:
            self._reconnecting = False
            self._reconnect_task = None
            if not self._hb_task or self._hb_task.done():
                self._hb_task = self.hass.async_create_background_task(
                    self._heartbeat_loop(), name=f"{DOMAIN}_heartbeat"
                )
        return ok

    @callback
    def _handle_disconnect(self) -> None:
        """Called by TuyaBLEClient when the device drops the connection unexpectedly."""
        if not self._stopping:
            self._start_reconnect_loop()

    async def _reconnect_loop(self) -> None:
        while not self._client.connected and not self._stopping:
            await asyncio.sleep(RECONNECT_DELAY)
            if self._stopping:
                break
            _LOGGER.debug("Attempting reconnect to %s", self._entry_data.address)
            ble_device = bluetooth.async_ble_device_from_address(
                self.hass, self._entry_data.address, connectable=True
            )
            if ble_device:
                self._client._ble_device = ble_device
            await self._try_connect()
        self._reconnecting = False

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            if self._client.connected:
                try:
                    await self._client.heartbeat()
                except Exception as exc:
                    _LOGGER.debug("Heartbeat failed: %s", exc)

    @callback
    def _handle_dp_update(self, dps: list[TuyaDP]) -> None:
        """Merge incoming DP values into state and notify HA entities."""
        state = self._entry_data.state

        _DP_TYPE_NAMES = {0: "RAW", 1: "BOOL", 2: "INT", 3: "STRING", 4: "ENUM", 5: "BITMAP"}

        for dp in dps:
            _LOGGER.debug("DP update: id=%d type=%d value=%r", dp.dp_id, dp.dp_type, dp.value)
            if self._scan_verbose:
                _LOGGER.warning(
                    "=== SCAN  dp_id=%-4d (0x%02x)  type=%-8s  value=%r ===",
                    dp.dp_id, dp.dp_id,
                    _DP_TYPE_NAMES.get(dp.dp_type, str(dp.dp_type)),
                    dp.value,
                )

            if dp.dp_id == DP_TEMP_SET:                  # 114 — setpoint/display (integer °C, writable)
                state.temp_set = int(dp.value)
            elif dp.dp_id == 112:                         # 112 — actual internal temperature (°C)
                state.temp_current = int(dp.value)
            elif dp.dp_id == DP_BATTERY_VOLTAGE:           # 122 — battery voltage (mV → V)
                state.battery_voltage = int(dp.value) / 1000.0
            elif dp.dp_id in (117, 119):                  # °F duplicates — ignore
                pass
            # ── DPs still to be identified (DP id = 0 = TBD, skip) ────────────
            elif DP_SWITCH and dp.dp_id == DP_SWITCH:
                state.power = bool(dp.value)
            elif DP_MODE and dp.dp_id == DP_MODE:
                # Device sends ENUM: \x00 = MAX, \x01 = ECO
                state.mode = MODE_ECO if dp.value in ("\x01", 1, True) else MODE_MAX
            elif DP_TEMP_UNIT and dp.dp_id == DP_TEMP_UNIT:
                # Device sends ENUM: \x00 = Celsius, \x01 = Fahrenheit (numeric byte, not "c"/"f")
                state.temp_unit = "f" if dp.value in ("\x01", 1, True) else "c"
            elif DP_FAULT and dp.dp_id == DP_FAULT:
                state.fault = int(dp.value)
            elif DP_LOCK and dp.dp_id == DP_LOCK:
                state.lock = dp.value in ("\x01", 1, True)
            elif DP_BATTERY_PROT and dp.dp_id == DP_BATTERY_PROT:
                # Device sends ENUM: \x00=Low, \x01=Medium, \x02=High
                _bp = {"\x00": "l", "\x01": "m", "\x02": "h"}
                state.battery_prot = _bp.get(dp.value, "m")
            else:
                _LOGGER.warning("Unknown DP id=%d value=%r — note in const.py", dp.dp_id, dp.value)

        state.available = True
        self.async_set_updated_data(state)

    async def _async_update_data(self) -> EuhomyState:
        """Required by DataUpdateCoordinator; we use push so this is a no-op."""
        return self._entry_data.state
