"""Select entity for the Euhomy BLE refrigerator."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import EuhomyBLECoordinator
from .models import EuhomyConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EuhomyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up select entities from config entry."""
    coordinator: EuhomyBLECoordinator = entry.runtime_data
    async_add_entities([
        EuhomyTempUnitSelect(coordinator, entry.unique_id, entry.data["model"]),
        EuhomyBatteryProtSelect(coordinator, entry.unique_id, entry.data["model"]),
    ])


class EuhomyTempUnitSelect(SelectEntity):
    """Select entity to switch the fridge display between °C and °F."""

    _attr_has_entity_name = True
    _attr_name = "Display Unit"
    _attr_icon = "mdi:thermometer"
    _attr_options = ["Celsius", "Fahrenheit"]

    def __init__(self, coordinator: EuhomyBLECoordinator, address: str, model: str) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{address}_temp_unit"
        self._attr_device_info = {
            "identifiers":  {(DOMAIN, address)},
            "name":         f"Euhomy {model}",
            "manufacturer": "Euhomy",
            "model":        model,
        }

    @property
    def available(self) -> bool:
        return self._coordinator._entry_data.state.available

    @property
    def current_option(self) -> str:
        return "Fahrenheit" if self._coordinator._entry_data.state.temp_unit == "f" else "Celsius"

    async def async_select_option(self, option: str) -> None:
        await self._coordinator.async_set_temp_unit("f" if option == "Fahrenheit" else "c")

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self._coordinator.async_add_listener(self._handle_coordinator_update)
        )
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()


class EuhomyBatteryProtSelect(SelectEntity):
    """Select entity to set the battery protection level (Low / Medium / High)."""

    _attr_has_entity_name = True
    _attr_name = "Battery Protection"
    _attr_icon = "mdi:battery-lock"
    _attr_options = ["Low", "Medium", "High"]

    _LEVEL_TO_OPTION = {"l": "Low", "m": "Medium", "h": "High"}
    _OPTION_TO_LEVEL = {"Low": "l", "Medium": "m", "High": "h"}

    def __init__(self, coordinator: EuhomyBLECoordinator, address: str, model: str) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{address}_battery_prot"
        self._attr_device_info = {
            "identifiers":  {(DOMAIN, address)},
            "name":         f"Euhomy {model}",
            "manufacturer": "Euhomy",
            "model":        model,
        }

    @property
    def available(self) -> bool:
        return self._coordinator._entry_data.state.available

    @property
    def current_option(self) -> str:
        return self._LEVEL_TO_OPTION.get(
            self._coordinator._entry_data.state.battery_prot, "Medium"
        )

    async def async_select_option(self, option: str) -> None:
        level = self._OPTION_TO_LEVEL.get(option, "m")
        await self._coordinator.async_set_battery_protection(level)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self._coordinator.async_add_listener(self._handle_coordinator_update)
        )
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
