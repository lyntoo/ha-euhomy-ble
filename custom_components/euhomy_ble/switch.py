"""Switch entity for the Euhomy BLE refrigerator."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
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
    """Set up switch entities from config entry."""
    coordinator: EuhomyBLECoordinator = entry.runtime_data
    async_add_entities([
        EuhomyPanelLockSwitch(coordinator, entry.unique_id, entry.data["model"]),
    ])


class EuhomyPanelLockSwitch(SwitchEntity):
    """Switch to lock / unlock the fridge panel buttons."""

    _attr_has_entity_name = True
    _attr_name = "Panel Lock"
    _attr_icon = "mdi:lock"

    def __init__(self, coordinator: EuhomyBLECoordinator, address: str, model: str) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{address}_panel_lock"
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
    def is_on(self) -> bool:
        return self._coordinator._entry_data.state.lock

    async def async_turn_on(self, **kwargs) -> None:
        await self._coordinator.async_set_lock(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._coordinator.async_set_lock(False)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self._coordinator.async_add_listener(self._handle_coordinator_update)
        )
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
