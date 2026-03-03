"""Config flow for the Euhomy BLE integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS

from .const import (
    BLE_LOCAL_NAME,
    CONF_DEVICE_ID,
    CONF_LOCAL_KEY,
    CONF_MODEL,
    CONF_UUID,
    DOMAIN,
    KNOWN_MAC,
    MODEL_CFC25,
    SUPPORTED_MODELS,
    TUYA_MANUFACTURER_ID,
)

# Normalise MAC for comparison (upper-case, colon-separated)
_KNOWN_MAC_NORM = KNOWN_MAC.upper()


def _is_euhomy_device(service_info: BluetoothServiceInfoBleak) -> bool:
    """
    Return True if the advertisement is our Euhomy CFC-25.

    Matching strategy:
      1. Exact MAC match  →  safest, no false positives.
      2. name == "TY" AND Tuya manufacturer_id present  →  very likely Tuya BLE
         device; we accept it and let the user confirm in the UI.

    "TY" is the standard Tuya BLE advertisement name shared by ALL Tuya BLE
    devices, so name alone is not enough — we combine it with manufacturer_id.
    """
    # Priority 1: known MAC (certain match)
    if service_info.address.upper() == _KNOWN_MAC_NORM:
        return True
    # Priority 2: name "TY" + Tuya manufacturer data
    if (
        service_info.name == BLE_LOCAL_NAME
        and TUYA_MANUFACTURER_ID in service_info.manufacturer_data
    ):
        return True
    return False


def _model_from_service_info(service_info: BluetoothServiceInfoBleak) -> str:
    """Best-effort model identification from advertisement."""
    # MAC match → definitely the known CFC-25
    if service_info.address.upper() == _KNOWN_MAC_NORM:
        return MODEL_CFC25
    return MODEL_CFC25   # default for other Tuya BLE devices in this integration


class EuhomyBLEConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Euhomy BLE."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_devices: dict[str, tuple[str, str]] = {}
        # {address: (display_name, model)}

    # ── Automatic discovery (HA calls this when a matching device is seen) ────

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle automatic Bluetooth discovery."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        if not _is_euhomy_device(discovery_info):
            return self.async_abort(reason="not_supported")

        self._discovery_info = discovery_info
        self.context["title_placeholders"] = {
            "name": discovery_info.name or discovery_info.address
        }
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user to confirm discovery, then request the local key."""
        assert self._discovery_info is not None
        if user_input is not None:
            return await self.async_step_local_key()

        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={
                "name":    self._discovery_info.name or self._discovery_info.address,
                "address": self._discovery_info.address,
            },
        )

    # ── Manual flow (user opens Integrations → Add) ───────────────────────────

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick from discovered Euhomy devices."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            _name, model = self._discovered_devices[address]
            # Synthesise a discovery_info-like object for later steps.
            self._discovery_info = next(
                (
                    si
                    for si in async_discovered_service_info(self.hass, False)
                    if si.address == address
                ),
                None,
            )
            return await self.async_step_local_key()

        current_ids = self._async_current_ids(include_ignore=False)
        for si in async_discovered_service_info(self.hass, False):
            if si.address in current_ids or si.address in self._discovered_devices:
                continue
            if _is_euhomy_device(si):
                display = f"{si.name} ({si.address})" if si.name else si.address
                self._discovered_devices[si.address] = (display, _model_from_service_info(si))

        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {addr: name for addr, (name, _) in self._discovered_devices.items()}
                    )
                }
            ),
        )

    # ── Local key step (common to both paths) ─────────────────────────────────

    async def async_step_local_key(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """
        Request the Tuya local key from the user.

        How to obtain the local key:
          1. Install the official "Tuya Smart" app on your phone.
          2. Re-pair the CFC-25 through Tuya Smart (it uses the same Tuya backend
             as the Euhomy app – the device will move to the Tuya account).
          3. Log in to https://iot.tuya.com → Cloud → Devices.
          4. Find your device and copy the "Local Key" (16-character hex string).

        Alternatively you can keep using the Euhomy app; in that case:
          - Create a developer account on iot.tuya.com.
          - Create a project and link your Euhomy (= Tuya) account to it.
          - The device will appear under Device Management with its local key.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            local_key = user_input[CONF_LOCAL_KEY].strip()
            device_id = user_input[CONF_DEVICE_ID].strip()
            uuid      = user_input[CONF_UUID].strip()
            if len(local_key) != 16:
                errors[CONF_LOCAL_KEY] = "invalid_local_key_length"
            elif not device_id:
                errors[CONF_DEVICE_ID] = "required"
            elif not uuid:
                errors[CONF_UUID] = "required"
            else:
                assert self._discovery_info is not None or self._discovered_devices
                address = (
                    self._discovery_info.address
                    if self._discovery_info
                    else next(iter(self._discovered_devices))
                )
                model = _model_from_service_info(self._discovery_info) if self._discovery_info else MODEL_CFC25

                await self.async_set_unique_id(address, raise_on_progress=False)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"Euhomy {model}",
                    data={
                        CONF_ADDRESS:   address,
                        CONF_LOCAL_KEY: local_key,
                        CONF_DEVICE_ID: device_id,
                        CONF_UUID:      uuid,
                        CONF_MODEL:     model,
                    },
                )

        return self.async_show_form(
            step_id="local_key",
            data_schema=vol.Schema({
                vol.Required(CONF_LOCAL_KEY): str,
                vol.Required(CONF_DEVICE_ID): str,
                vol.Required(CONF_UUID):      str,
            }),
            errors=errors,
            description_placeholders={},
        )
