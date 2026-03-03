"""Sensor entities for the Euhomy BLE refrigerator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfElectricPotential, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    BATTERY_PROT_HIGH,
    BATTERY_PROT_LOW,
    BATTERY_PROT_MEDIUM,
    DOMAIN,
    FAULT_DESCRIPTIONS,
)
from .coordinator import EuhomyBLECoordinator
from .models import EuhomyConfigEntry, EuhomyState


def _fault_text(state: EuhomyState) -> str:
    """Convert the fault bitmap to a human-readable string."""
    if state.fault == 0:
        return "OK"
    active = [desc for mask, desc in FAULT_DESCRIPTIONS.items() if state.fault & mask]
    return ", ".join(active) if active else f"Unknown (0x{state.fault:02x})"


def _battery_prot_text(state: EuhomyState) -> str:
    labels = {
        BATTERY_PROT_HIGH:   "High (H)",
        BATTERY_PROT_MEDIUM: "Medium (M)",
        BATTERY_PROT_LOW:    "Low (L)",
    }
    return labels.get(state.battery_prot, state.battery_prot)


@dataclass(frozen=True, kw_only=True)
class EuhomySensorDescription(SensorEntityDescription):
    """Extends SensorEntityDescription with a state extractor."""
    value_fn: Any = None   # Callable[[EuhomyState], Any]


SENSOR_DESCRIPTIONS: tuple[EuhomySensorDescription, ...] = (
    EuhomySensorDescription(
        key="temperature_current",
        name="Current Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda s: s.temp_current,
    ),
    EuhomySensorDescription(
        key="fault",
        name="Fault",
        icon="mdi:alert-circle-outline",
        value_fn=_fault_text,
    ),
    EuhomySensorDescription(
        key="battery_protection",
        name="Battery Protection",
        icon="mdi:car-battery",
        value_fn=_battery_prot_text,
    ),
    EuhomySensorDescription(
        key="battery_voltage",
        name="Battery Voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=2,
        icon="mdi:car-battery",
        value_fn=lambda s: s.battery_voltage if s.battery_voltage > 0 else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EuhomyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities from config entry."""
    coordinator: EuhomyBLECoordinator = entry.runtime_data
    address = entry.unique_id
    model   = entry.data["model"]

    async_add_entities(
        EuhomySensor(coordinator, address, model, desc)
        for desc in SENSOR_DESCRIPTIONS
    )


class EuhomySensor(SensorEntity):
    """A sensor for one DP of the Euhomy refrigerator."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EuhomyBLECoordinator,
        address: str,
        model: str,
        description: EuhomySensorDescription,
    ) -> None:
        self.entity_description  = description
        self._coordinator        = coordinator
        self._attr_unique_id     = f"{address}_{description.key}"
        self._attr_device_info   = {
            "identifiers":  {(DOMAIN, address)},
            "name":         f"Euhomy {model}",
            "manufacturer": "Euhomy",
            "model":        model,
        }

    @property
    def available(self) -> bool:
        return self._coordinator._entry_data.state.available

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self._coordinator._entry_data.state)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self._coordinator.async_add_listener(self._handle_coordinator_update)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
