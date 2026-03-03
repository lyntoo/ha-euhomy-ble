"""Data models for the Euhomy BLE integration.

Target device: Euhomy CFC-25 (SKU: CF004-25BL-CAEH), 25L Canadian version.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry

from .tuya_ble import TuyaBLEClient


from typing import TYPE_CHECKING
if TYPE_CHECKING:
    pass

EuhomyConfigEntry = ConfigEntry["EuhomyData"]


@dataclass
class EuhomyState:
    """Current state of the Euhomy CFC-25 car refrigerator."""
    power: bool        = False
    mode: str          = "max"  # "max" (powerful) or "eco" (energy-saving)
    temp_set: int      = 4      # °C  — range -20 to 20 per user manual
    temp_current: float = 4.0  # °C  — DP 122 reports in millidegrees, stored as °C
    temp_unit: str     = "c"    # "c" or "f"
    fault: int         = 0      # bitmap  E1–E6 error flags
    lock: bool         = False  # panel lock
    battery_prot: str  = "m"    # "h" | "m" | "l" battery protection level
    battery_voltage: float = 0.0  # volts (DP 122 in mV → V)
    available: bool    = False  # True once first DP report received


@dataclass
class EuhomyData:
    """Runtime data stored in the config entry."""
    address: str
    model: str
    client: TuyaBLEClient
    state: EuhomyState = field(default_factory=EuhomyState)
