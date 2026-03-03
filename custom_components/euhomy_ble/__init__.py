"""The Euhomy BLE integration."""

from __future__ import annotations

from homeassistant.components import bluetooth
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady

import logging

from .const import CONF_DEVICE_ID, CONF_LOCAL_KEY, CONF_MODEL, CONF_UUID, DOMAIN
from .coordinator import EuhomyBLECoordinator
from .models import EuhomyConfigEntry, EuhomyData, EuhomyState
from .tuya_ble import TuyaBLEClient, TuyaDP

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.CLIMATE,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: EuhomyConfigEntry) -> bool:
    """Set up Euhomy BLE from a config entry."""
    address   = entry.unique_id
    assert address is not None

    local_key = entry.data[CONF_LOCAL_KEY]
    model     = entry.data[CONF_MODEL]
    device_id = entry.data.get(CONF_DEVICE_ID, "")
    uuid      = entry.data.get(CONF_UUID, "")

    # Resolve the BLE device object from the address.
    ble_device = bluetooth.async_ble_device_from_address(
        hass, address.upper(), connectable=True
    )
    if ble_device is None:
        raise ConfigEntryNotReady(
            f"Euhomy BLE device {address} is not reachable. "
            "Make sure it is powered on and within Bluetooth range."
        )

    client = TuyaBLEClient(
        ble_device=ble_device,
        local_key=local_key,
        device_id=device_id,
        uuid=uuid,
    )

    entry_data = EuhomyData(address=address, model=model, client=client)
    coordinator = EuhomyBLECoordinator(hass, entry_data)

    # Store coordinator on the entry so platforms can access it.
    entry.runtime_data = coordinator

    await coordinator.async_start()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # ── Service: euhomy_ble.scan_dps ─────────────────────────────────────────
    async def _handle_scan_dps(_call: ServiceCall) -> None:
        """Query all DPs and log them as WARNING for easy identification."""
        for e in hass.config_entries.async_entries(DOMAIN):
            try:
                coord: EuhomyBLECoordinator = e.runtime_data
            except (AttributeError, RuntimeError):
                continue
            await coord.async_scan_dps()

    hass.services.async_register(DOMAIN, "scan_dps", _handle_scan_dps)

    # ── Service: euhomy_ble.write_dp ─────────────────────────────────────────
    # Debug service to write arbitrary DPs. Usage from Developer Tools → Services:
    #   service: euhomy_ble.write_dp
    #   data:
    #     dp_id: 106        # DP number to test
    #     dp_type: 4        # 1=BOOL 2=INT 3=STRING 4=ENUM 5=BITMAP
    #     value: "m"        # value as string (use \x01 notation for raw bytes)
    async def _handle_write_dp(call: ServiceCall) -> None:
        """Write an arbitrary DP to the device — for DP discovery/testing."""
        dp_id   = int(call.data["dp_id"])
        dp_type = int(call.data["dp_type"])
        value   = call.data["value"]
        if dp_type == 2:   # INT
            value = int(value)
        elif dp_type == 1:  # BOOL
            value = bool(value)
        elif dp_type == 4 and isinstance(value, (int, float)):
            # ENUM with integer → convert to single char byte (0→'\x00', 1→'\x01', 2→'\x02')
            value = chr(int(value))
        _LOGGER.warning("=== write_dp TEST: dp_id=%d dp_type=%d value=%r ===", dp_id, dp_type, value)
        for e in hass.config_entries.async_entries(DOMAIN):
            try:
                coord: EuhomyBLECoordinator = e.runtime_data
            except (AttributeError, RuntimeError):
                continue
            await coord._client.publish_dp(TuyaDP(dp_id=dp_id, dp_type=dp_type, value=value))

    hass.services.async_register(DOMAIN, "write_dp", _handle_write_dp)

    async def _async_stop(_event: Event) -> None:
        await coordinator.async_stop()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop)
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: EuhomyConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator: EuhomyBLECoordinator = entry.runtime_data
    await coordinator.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
