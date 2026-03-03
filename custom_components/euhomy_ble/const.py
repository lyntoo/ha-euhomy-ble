"""Constants for the Euhomy BLE integration."""

DOMAIN = "euhomy_ble"

# ── Supported models ──────────────────────────────────────────────────────────
# User's device : SKU CF004-25BL-CAEH  →  Model CFC-25  (25L, Canadian version)
# Reference manual in box : CF004-18BL-USEH (CFC-18, 18L US) — same product
# line, same BLE/Tuya protocol, same modes (MAX/ECO), same DP structure.
# "BL" in SKU = Bluetooth.  "CA" = Canada,  "US" = United States.
MODEL_CFC25 = "CFC-25"
MODEL_CFC18 = "CFC-18"   # kept for potential future multi-model support

SUPPORTED_MODELS: dict[str, str] = {
    # Euhomy CFC-25 (SKU CF004-25BL-CAEH) advertises as "TY" over BLE.
    # "TY" is the generic Tuya BLE advertisement name shared by ALL Tuya BLE
    # devices, so we cannot rely on name alone — we filter by MAC in config_flow.
    "CFC-25": MODEL_CFC25,
    "CFC-18": MODEL_CFC18,   # 18L US variant — same protocol
    # Additional Euhomy models can be added here later.
}

# BLE advertisement name (confirmed by user BLE scan)
BLE_LOCAL_NAME = "TY"

# Known BLE MAC address (from app, "local mode") — REMOVE BEFORE GIT PUSH
KNOWN_MAC = "DC:23:52:1B:5C:7C"

# Tuya cloud virtual device ID (from app device info) — REMOVE BEFORE GIT PUSH
TUYA_VIRTUAL_ID = "eb5cc8atkfnwvaxk"

# Tuya manufacturer_id used in BLE advertisements (0x07D0 = 2000 decimal)
TUYA_MANUFACTURER_ID = 0x07D0

# ── Tuya BLE GATT UUIDs ───────────────────────────────────────────────────────
TUYA_BLE_SERVICE_UUID     = "00001910-0000-1000-8000-00805f9b34fb"
TUYA_BLE_NOTIFY_CHAR_UUID = "00002b10-0000-1000-8000-00805f9b34fb"  # confirmed from device GATT dump
TUYA_BLE_WRITE_CHAR_UUID  = "00002b11-0000-1000-8000-00805f9b34fb"  # confirmed from device GATT dump

# ── Tuya BLE protocol constants ───────────────────────────────────────────────
FRAME_HEADER       = b"\x55\xAA"
PROTOCOL_VERSION   = 0x03
SECURITY_LEVEL_AES = 0x04   # AES-128-ECB with local key
BLE_MTU            = 20      # max bytes per write without response

# ── Tuya BLE command codes ────────────────────────────────────────────────────
CMD_HEARTBEAT    = 0x00
CMD_PRODUCT_INFO = 0x01
CMD_DEVICE_INFO  = 0x02
CMD_PAIR         = 0x03
CMD_DP_REPORT    = 0x05   # device → phone  (state report / push)
CMD_DP_QUERY     = 0x06   # phone → device  (read all DPs)
CMD_DP_PUBLISH   = 0x07   # phone → device  (write / command)

# ── Tuya DP type codes ────────────────────────────────────────────────────────
DP_TYPE_RAW    = 0x00
DP_TYPE_BOOL   = 0x01
DP_TYPE_INT    = 0x02
DP_TYPE_STRING = 0x03
DP_TYPE_ENUM   = 0x04
DP_TYPE_BITMAP = 0x05

# ── DP IDs for the Euhomy CFC-25 (confirmed from live BLE captures) ──────────
# Confirmed by pressing +/- on the physical fridge and watching DP changes:
DP_TEMP_SET     = 114  # int    – setpoint / display (°C integer) [0x72] CONFIRMED writable
#                                  changes when pressing physical +/- buttons
#                                  DP 119 (0x77) = DP 114 in °F (read-only mirror)
DP_TEMP_CURRENT = 112  # int    – actual internal temperature (°C integer) [0x70] CONFIRMED read-only
#                                  reported periodically; DP 117 (0x75) = DP 112 in °F mirror
DP_BATTERY_VOLTAGE = 122  # int – battery voltage (mV) [0x7a] CONFIRMED e.g. 14700 = 14.700V

# TODO: identify by pressing power / mode buttons and watching logs:
DP_SWITCH       = 101  # bool   – power on / off             [0x65] CONFIRMED
DP_MODE         = 103  # enum   – 0x00=MAX | 0x01=ECO         [0x67] CONFIRMED
DP_FAULT        = 0    # bitmap – fault / error flags        TBD
DP_TEMP_UNIT    = 105  # enum   – 0x00=Celsius | 0x01=Fahrenheit  [0x69] CONFIRMED
DP_LOCK         = 102  # enum   – panel lock: 0x00=unlock | 0x01=lock  [0x66] CONFIRMED
DP_BATTERY_PROT = 104  # enum   – 0x00=Low | 0x01=Medium | 0x02=High  [0x68] CONFIRMED

# ── Temperature limits (from CFC-18 manual p.5/p.8 — assumed same for CFC-25) ─
TEMP_MIN_C = -20   # °C  (-4°F)
TEMP_MAX_C =  20   # °C  (68°F)
TEMP_STEP  =   1   # °C

# ── Operating modes (confirmed from user manual p.4-5) ───────────────────────
# MAX  = powerful cooling  (compressor runs quickly)
# ECO  = energy-saving     (compressor runs slowly, saves car battery)
MODE_MAX = "max"
MODE_ECO = "eco"
AVAILABLE_MODES = [MODE_MAX, MODE_ECO]

# ── Fault / error code bitmap (confirmed from user manual p.6-7) ─────────────
# The fault DP is a bitmap; each bit corresponds to one error code.
# TODO: confirm bit positions against actual device – these are estimated.
FAULT_E1_LOW_VOLTAGE        = 0x01   # E1 – Low input voltage
FAULT_E2_FAN                = 0x02   # E2 – Fan fault
FAULT_E3_VOLTAGE_INSTABILITY= 0x04   # E3 – Compressor starting/stopping frequently
FAULT_E4_LOW_COMPRESSOR_RPM = 0x08   # E4 – Low rotational speed of compressor
FAULT_E5_CHIP_OVERHEAT      = 0x10   # E5 – Overheating of controller chip
FAULT_E6_TEMP_SENSOR        = 0x20   # E6 – Temperature sensor wire disconnected

FAULT_DESCRIPTIONS: dict[int, str] = {
    FAULT_E1_LOW_VOLTAGE:         "E1: Low input voltage",
    FAULT_E2_FAN:                 "E2: Fan fault",
    FAULT_E3_VOLTAGE_INSTABILITY: "E3: Compressor voltage instability",
    FAULT_E4_LOW_COMPRESSOR_RPM:  "E4: Low compressor speed",
    FAULT_E5_CHIP_OVERHEAT:       "E5: Controller chip overheating",
    FAULT_E6_TEMP_SENSOR:         "E6: Temperature sensor disconnected",
}

# ── Battery protection levels ─────────────────────────────────────────────────
BATTERY_PROT_HIGH   = "h"   # H: 12V off@11.3V on@12.5V / 24V off@24.6V on@26V
BATTERY_PROT_MEDIUM = "m"   # M: 12V off@10.1V on@11.4V / 24V off@22.3V on@23.7V
BATTERY_PROT_LOW    = "l"   # L: 12V off@9.6V  on@10.9V / 24V off@21.3V on@22.7V

# ── Config-entry keys ─────────────────────────────────────────────────────────
CONF_LOCAL_KEY = "local_key"
CONF_MODEL     = "model"
CONF_DEVICE_ID = "device_id"
CONF_UUID      = "uuid"

# ── Product info (from APK reverse-engineering) ───────────────────────────────
TUYA_PRODUCT_ID = "fwfjgdzs1ri0swej"
TUYA_UI_ID      = "000001pth8"
