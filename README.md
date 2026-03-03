# Euhomy BLE — Home Assistant Custom Integration

Control your **Euhomy CFC-25 / CFC-18 portable car refrigerator** directly from Home Assistant via Bluetooth Low Energy (BLE), without any cloud dependency.

> Developed and reverse-engineered from scratch against a real CFC-25 (SKU: CF004-25BL-CAEH).
> The Euhomy app uses the **Tuya BLE protocol** underneath — this integration speaks it natively.

---

## Features

| Feature | Status |
|---|---|
| Current temperature (actual internal) | ✅ |
| Target temperature (setpoint) | ✅ Read + Write |
| Power on / standby | ✅ |
| Operating mode MAX / ECO | ✅ |
| Display unit °C / °F | ✅ |
| Battery voltage sensor | ✅ |
| Panel lock | ✅ |
| Battery protection level (Low / Medium / High) | ✅ |
| Fault / error codes sensor | ❌ Not available via BLE (display-only) |

---

## Supported Models

| Model | SKU | Status |
|---|---|---|
| CFC-25 | CF004-25BL-CAEH (CA) / CF004-25BL-USEH (US) | ✅ Tested |
| CFC-18 | CF004-18BL-USEH | 🔲 Untested (same protocol, should work) |

The fridge advertises over BLE as **"TY"** (generic Tuya name). Pairing is done by MAC address in the config flow.

---

## Requirements

- Home Assistant 2024.1 or newer
- A Bluetooth adapter visible to HA (built-in or USB dongle)
- The fridge within Bluetooth range (~10 m)
- Your device's **Local Key**, **Device ID**, and **UUID** (see below)

---

## How to get your Local Key, Device ID and UUID

The fridge uses the Tuya BLE protocol and requires a **local key** for encrypted communication. This key is tied to your specific device and does not change.

### Step 1 — Create a Tuya IoT Platform account

1. Go to [iot.tuya.com](https://iot.tuya.com) and sign up for a free account.
2. In the top menu, go to **Cloud → Development** and click **Create Cloud Project**.
3. Fill in any project name, choose **Smart Home** as the industry, **Smart Home** as the development method, and select a data center close to you (e.g. **Western America** for Canada/US).
4. On the next screen, under **API products**, make sure **IoT Core** and **Smart Home Scene Linkage** are selected, then click **Authorize**.

### Step 2 — Link your Euhomy app account

1. In your Cloud project, click the **Devices** tab, then **Link Tuya App Account**.
2. Open the **Euhomy Smart** app on your phone, go to **Me → Settings → Account and Security**.
3. Tap **Scan** (or use the QR code button) and scan the QR code shown on the Tuya IoT website.
4. Your devices will now appear in the **All Devices** list on the Tuya IoT platform.

### Step 3 — Get your credentials

1. In the **All Devices** list, find your Euhomy fridge.
2. Click on it and note the following values:
   - **Device ID** (shown as "Device ID" — a long alphanumeric string, e.g. `d4b9e7fgm2nkw3xp`)
   - **UUID** (shown as "UUID" — e.g. `3c7a2f5d8b4e9106`)
3. To get the **Local Key**:
   - Click the **Debug Device** button (or the device name) to open the device detail page.
   - Scroll down to find **Local Key** — it is a 16-character string (e.g. `a1b2c3d4e5f6g7h8`).

> **Keep your Local Key private** — it gives full local control of your device.
> You do not need a Tuya cloud connection after setup; the integration communicates directly via BLE.

---

## Installation

### Via HACS (recommended)

1. In HACS, click **Integrations → Custom repositories**.
2. Add `https://github.com/lyntoo/ha-euhomy-ble` with category **Integration**.
3. Find **Euhomy BLE** in the list and click **Download**.
4. Restart Home Assistant.

### Manual

1. Copy the `custom_components/euhomy_ble/` folder into your HA `config/custom_components/` directory.
2. Restart Home Assistant.

---

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **Euhomy BLE**.
3. Follow the prompts:
   - Select your fridge from the list of nearby BLE devices (look for the correct MAC address)
   - Enter your **Local Key**, **Device ID**, and **UUID**
   - Select your model (CFC-25 or CFC-18)

---

## Entities created

| Entity | Type | Description |
|---|---|---|
| Euhomy CFC-25 | Climate | Main control: power, setpoint, mode, current temp |
| Battery Voltage | Sensor | Car battery voltage in volts (e.g. 14.7 V) |
| Battery Protection | Select | Protection level: Low / Medium / High |
| Display Unit | Select | Switch fridge display between °C and °F |
| Panel Lock | Switch | Lock / unlock the physical panel buttons |

---

## DP Map (Tuya Data Points)

For developers and contributors — confirmed via live BLE captures:

| DP | Hex | Type | Role | Access |
|---|---|---|---|---|
| 101 | 0x65 | BOOL | Power on / standby | R/W |
| 102 | 0x66 | ENUM | Panel lock: 0x00=unlock, 0x01=lock | R/W |
| 103 | 0x67 | ENUM | Mode: 0x00=MAX, 0x01=ECO | R/W |
| 104 | 0x68 | ENUM | Battery protection: 0x00=Low, 0x01=Medium, 0x02=High | R/W |
| 105 | 0x69 | ENUM | Unit: 0x00=°C, 0x01=°F | R/W |
| 112 | 0x70 | INT | Actual internal temperature (°C) | R |
| 114 | 0x72 | INT | Setpoint / target temperature (°C) | R/W |
| 117 | 0x75 | INT | Actual temperature in °F (mirror) | R |
| 119 | 0x77 | INT | Setpoint in °F (mirror) | R |
| 122 | 0x7a | INT | Battery voltage in mV (÷1000 = V) | R |

---

## Error codes (display-only)

The fridge displays error codes on its physical front panel. These codes are **not transmitted via BLE** — no Data Point is pushed to Home Assistant when an error occurs. There is no way to create a HA sensor for fault states on this firmware.

| Code | Meaning |
|---|---|
| E1 | Battery overvoltage |
| E2 | Fan motor fault |
| E3 | Temperature instability |
| E4 | Compressor fault |
| E5 | PCB fault |
| E6 | Temperature sensor fault |

> Confirmed by live BLE captures with E6 (sensor fault) active: only the normal status DPs
> (temperature, battery, mode…) were received — no fault DP appeared at any point.

---

## Protocol notes

- BLE service UUID: `00001910-0000-1000-8000-00805f9b34fb`
- Encryption: **AES-128-CBC** (Tuya BLE v3, security level 5)
- Session key: `MD5(local_key[:6] + srand_from_device)`
- The fridge advertises as **"TY"** (generic Tuya name shared by all Tuya BLE devices) — filtering is done by MAC address

---

## Contributing

PRs welcome! All known DPs are now fully mapped. If you have a different Tuya BLE fridge model and want to add support, use the `euhomy_ble.scan_dps` HA service to trigger a full DP dump and share the logs.

---

## License

MIT
