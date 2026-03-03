"""Climate entity for the Euhomy BLE refrigerator."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import PRECISION_WHOLE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    AVAILABLE_MODES,
    DOMAIN,
    MODE_ECO,
    MODE_MAX,
    TEMP_MAX_C,
    TEMP_MIN_C,
    TEMP_STEP,
)
from .coordinator import EuhomyBLECoordinator
from .models import EuhomyConfigEntry

_LOGGER = logging.getLogger(__name__)

# Map Tuya mode strings → HA preset names (shown in the Lovelace card).
# Confirmed from user manual p.4-5:
#   MAX = powerful cooling (compressor runs quickly to reduce temperature)
#   ECO = energy-saving cooling (compressor runs slowly to save car battery)
_TUYA_TO_HA_PRESET: dict[str, str] = {
    MODE_MAX: "MAX",
    MODE_ECO: "ECO",
}
_HA_PRESET_TO_TUYA = {v: k for k, v in _TUYA_TO_HA_PRESET.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EuhomyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up climate entity from config entry."""
    coordinator: EuhomyBLECoordinator = entry.runtime_data
    async_add_entities([EuhomyClimate(coordinator, entry.unique_id, entry.data["model"])])


class EuhomyClimate(ClimateEntity):
    """
    Climate entity representing the Euhomy CFC-25 refrigerator.

    The device is always in "cool" mode when powered on, so we only expose
    HVAC modes COOL (on) and OFF.  Operating modes (manual / eco) are
    surfaced as HA presets.
    """

    _attr_has_entity_name          = True
    _attr_name                     = None   # use device name as entity name
    _attr_hvac_modes               = [HVACMode.COOL, HVACMode.OFF]
    _attr_supported_features       = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
    )
    _attr_target_temperature_step  = TEMP_STEP
    _attr_min_temp                 = TEMP_MIN_C
    _attr_max_temp                 = TEMP_MAX_C
    _attr_precision                = PRECISION_WHOLE
    _attr_preset_modes             = list(_TUYA_TO_HA_PRESET.values())

    def __init__(
        self,
        coordinator: EuhomyBLECoordinator,
        address: str,
        model: str,
    ) -> None:
        self._coordinator  = coordinator
        self._attr_unique_id = f"{address}_climate"
        self._attr_device_info = {
            "identifiers":    {(DOMAIN, address)},
            "name":           f"Euhomy {model}",
            "manufacturer":   "Euhomy",
            "model":          model,
            "sw_version":     None,   # populated if firmware version DP is found
        }

    # ── State ─────────────────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return self._coordinator._entry_data.state.available

    @property
    def hvac_mode(self) -> HVACMode:
        return HVACMode.COOL if self._coordinator._entry_data.state.power else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction:
        if not self._coordinator._entry_data.state.power:
            return HVACAction.OFF
        s = self._coordinator._entry_data.state
        return HVACAction.COOLING if s.temp_current > s.temp_set else HVACAction.IDLE

    @property
    def current_temperature(self) -> float | None:
        return float(self._coordinator._entry_data.state.temp_current)

    @property
    def target_temperature(self) -> float | None:
        return float(self._coordinator._entry_data.state.temp_set)

    @property
    def temperature_unit(self) -> str:
        unit = self._coordinator._entry_data.state.temp_unit
        return UnitOfTemperature.FAHRENHEIT if unit == "f" else UnitOfTemperature.CELSIUS

    @property
    def preset_mode(self) -> str | None:
        tuya_mode = self._coordinator._entry_data.state.mode
        return _TUYA_TO_HA_PRESET.get(tuya_mode, "Manual")

    # ── Commands ──────────────────────────────────────────────────────────────

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        await self._coordinator.async_set_power(hvac_mode == HVACMode.COOL)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get("temperature")
        if temp is not None:
            await self._coordinator.async_set_temperature(int(temp))

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        tuya_mode = _HA_PRESET_TO_TUYA.get(preset_mode, MODE_MAX)
        await self._coordinator.async_set_mode(tuya_mode)

    # ── HA integration hooks ──────────────────────────────────────────────────

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self._coordinator.async_add_listener(self._handle_coordinator_update)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
