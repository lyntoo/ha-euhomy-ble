"""
Tuya BLE protocol v3 implementation for Euhomy devices.

Correct protocol (based on PlusPlus-ua/ha_tuya_ble community reverse-engineering):

  Wire packet format (per BLE notification/write):
    Packet 0:  [pack_int(0)] [pack_int(total_encrypted_len)] [proto_ver<<4] [data_chunk]
    Packet N:  [pack_int(N)] [data_chunk]

  Encrypted payload structure:
    [security_flag (1B)] [iv (16B)] [AES-128-CBC(key, iv, inner)]
    inner = [seq(4B)][response_to(4B)][code(2B)][len(2B)][data][CRC16(2B)][zero-pad to 16B]

  Keys:
    local_key_6  = local_key_str[:6].encode("ascii")
    login_key    = MD5(local_key_6)                        — used for DEVICE_INFO only
    session_key  = MD5(local_key_6 + srand_from_device)   — used for everything else

  Mandatory connection sequence:
    1. BLE connect + subscribe notify
    2. Send FUN_SENDER_DEVICE_INFO (code=0x0000, login_key, security_flag=0x04)
       → Device responds: extract srand (bytes[6:12]) → derive session_key
    3. Send FUN_SENDER_PAIR (code=0x0001, session_key, security_flag=0x05)
       payload = uuid(N) + local_key_6(6) + device_id(N), zero-padded to 44 bytes
       → Device responds: 0=ok, 2=already paired (both acceptable)
    4. Device is now paired; send FUN_SENDER_DEVICE_STATUS to get current DPs

  DP commands:
    FUN_SENDER_DPS           = 0x0002  (write DPs)
    FUN_SENDER_DEVICE_STATUS = 0x0003  (query all DPs)

  Device → phone notifications:
    FUN_RECEIVE_DP           = 0x8001  (DP push, must ACK)
    FUN_RECEIVE_TIME_DP      = 0x8003  (DP with timestamp, must ACK)
    FUN_RECEIVE_SIGN_DP      = 0x8004  (signed DP, must ACK)
    FUN_RECEIVE_TIME1_REQ    = 0x8011  (device asks for ms-timestamp)
    FUN_RECEIVE_TIME2_REQ    = 0x8012  (device asks for struct time)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import struct
import time
from dataclasses import dataclass
from typing import Any, Callable

from bleak.backends.device import BLEDevice

try:
    from bleak_retry_connector import (
        BleakClientWithServiceCache,
        establish_connection,
    )
    _HAS_RETRY = True
except ImportError:
    from bleak import BleakClient as BleakClientWithServiceCache  # type: ignore[assignment]
    _HAS_RETRY = False

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend
    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False

from .const import (
    BLE_MTU,
    DP_TYPE_BITMAP,
    DP_TYPE_BOOL,
    DP_TYPE_ENUM,
    DP_TYPE_INT,
    DP_TYPE_RAW,
    DP_TYPE_STRING,
    TUYA_BLE_NOTIFY_CHAR_UUID,
    TUYA_BLE_WRITE_CHAR_UUID,
)

_LOGGER = logging.getLogger(__name__)

RESPONSE_TIMEOUT = 30.0
PROTOCOL_VERSION = 2

# Tuya BLE v3 command codes
_CMD_DEVICE_INFO    = 0x0000
_CMD_PAIR           = 0x0001
_CMD_SEND_DPS       = 0x0002
_CMD_DEVICE_STATUS  = 0x0003
_CMD_RECEIVE_DP     = 0x8001
_CMD_RECEIVE_TIME_DP    = 0x8003
_CMD_RECEIVE_SIGN_DP    = 0x8004
_CMD_RECEIVE_SIGN_TIME_DP = 0x8005
_CMD_TIME1_REQ      = 0x8011
_CMD_TIME2_REQ      = 0x8012


# ── Data structure ─────────────────────────────────────────────────────────────

@dataclass
class TuyaDP:
    """A single Tuya Data Point."""
    dp_id: int
    dp_type: int
    value: Any


# ── AES-CBC helpers ────────────────────────────────────────────────────────────

def _aes_cbc_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    if not _HAS_CRYPTO:
        raise RuntimeError("cryptography package not available")
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    enc = cipher.encryptor()
    return enc.update(data) + enc.finalize()


def _aes_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    if not _HAS_CRYPTO:
        raise RuntimeError("cryptography package not available")
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    dec = cipher.decryptor()
    return dec.update(data) + dec.finalize()


# ── CRC16 ──────────────────────────────────────────────────────────────────────

def _crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte & 0xFF
        for _ in range(8):
            tmp = crc & 1
            crc >>= 1
            if tmp:
                crc ^= 0xA001
    return crc


# ── Variable-length integer encoding ──────────────────────────────────────────

def _pack_int(value: int) -> bytes:
    result = bytearray()
    while True:
        curr = value & 0x7F
        value >>= 7
        if value:
            curr |= 0x80
        result.append(curr)
        if not value:
            break
    return bytes(result)


def _unpack_int(data: bytes, pos: int) -> tuple[int, int]:
    result = 0
    for offset in range(5):
        if pos + offset >= len(data):
            raise ValueError("Truncated variable-length integer")
        curr = data[pos + offset]
        result |= (curr & 0x7F) << (offset * 7)
        if not (curr & 0x80):
            return result, pos + offset + 1
    raise ValueError("Variable-length integer too long")


# ── Packet builder ─────────────────────────────────────────────────────────────

def _build_packets(
    seq_num: int,
    code: int,
    data: bytes,
    login_key: bytes,
    session_key: bytes | None,
    response_to: int = 0,
    mtu: int = BLE_MTU,
) -> list[bytes]:
    """Build wire-format BLE packets for one Tuya BLE v3 message."""
    # Select key and security_flag
    if code == _CMD_DEVICE_INFO:
        key = login_key
        security_flag = b"\x04"
    else:
        key = session_key if session_key else login_key
        security_flag = b"\x05"

    # Build inner payload
    iv = secrets.token_bytes(16)
    inner = bytearray()
    inner += struct.pack(">IIHH", seq_num, response_to, code, len(data))
    inner += data
    inner += struct.pack(">H", _crc16(inner))
    # Zero-pad to 16-byte boundary
    if len(inner) % 16:
        inner += b"\x00" * (16 - len(inner) % 16)

    encrypted = security_flag + iv + _aes_cbc_encrypt(key, iv, bytes(inner))

    # Fragment into MTU-sized chunks
    packets: list[bytes] = []
    packet_num = 0
    pos = 0
    total_len = len(encrypted)

    while pos < total_len:
        header = bytearray(_pack_int(packet_num))
        if packet_num == 0:
            header += _pack_int(total_len)
            header += bytes([PROTOCOL_VERSION << 4])
        chunk_size = mtu - len(header)
        chunk = encrypted[pos: pos + chunk_size]
        packets.append(bytes(header) + chunk)
        pos += len(chunk)
        packet_num += 1

    return packets


# ── DP codec ───────────────────────────────────────────────────────────────────

def encode_dp(dp: TuyaDP) -> bytes:
    """Serialise one DP for writing to device: [id(1)][type(1)][len(1)][value].

    Confirmed: device uses 1-byte length in BOTH directions (send and receive).
    Result=1 (error) was observed when using 2-byte length (>BBH).
    Result=0 (accepted) with 1-byte length (>BBB).
    """
    if dp.dp_type == DP_TYPE_BOOL:
        v = bytes([1 if dp.value else 0])
    elif dp.dp_type == DP_TYPE_INT:
        v = struct.pack(">i", int(dp.value))
    elif dp.dp_type in (DP_TYPE_STRING, DP_TYPE_ENUM):
        v = str(dp.value).encode()
    elif dp.dp_type == DP_TYPE_BITMAP:
        v = struct.pack(">I", int(dp.value))
    else:
        v = bytes(dp.value) if dp.value else b""
    return struct.pack(">BBB", dp.dp_id, dp.dp_type, len(v)) + v


def decode_dps(data: bytes) -> list[TuyaDP]:
    """Deserialise DPs from a v3 payload: [id(1)][type(1)][len(1)][value]."""
    dps: list[TuyaDP] = []
    pos = 0
    while pos + 3 <= len(data):
        dp_id   = data[pos]
        dp_type = data[pos + 1]
        dp_len  = data[pos + 2]  # 1-byte length in v3 (not 2!)
        v_bytes = data[pos + 3: pos + 3 + dp_len]
        pos += 3 + dp_len

        try:
            if dp_type == DP_TYPE_BOOL:
                value: Any = bool(v_bytes[0]) if v_bytes else False
            elif dp_type == DP_TYPE_INT:
                value = struct.unpack(">i", v_bytes.rjust(4, b"\x00"))[0]
            elif dp_type in (DP_TYPE_STRING, DP_TYPE_ENUM):
                value = v_bytes.decode(errors="replace")
            elif dp_type == DP_TYPE_BITMAP:
                value = int.from_bytes(v_bytes, "big")
            else:
                value = v_bytes
        except Exception as exc:
            _LOGGER.debug("DP %d decode error: %s", dp_id, exc)
            continue

        dps.append(TuyaDP(dp_id=dp_id, dp_type=dp_type, value=value))
    return dps


# ── Client ─────────────────────────────────────────────────────────────────────

class TuyaBLEClient:
    """
    Tuya BLE v3 client for Euhomy devices.

    Implements the full 2-step handshake (DEVICE_INFO → PAIR) required before
    any DP operations.
    """

    def __init__(
        self,
        ble_device: BLEDevice,
        local_key: str,
        device_id: str,
        uuid: str,
        on_dp_update: Callable[[list[TuyaDP]], None] | None = None,
        on_disconnect: Callable[[], None] | None = None,
    ) -> None:
        self._ble_device = ble_device
        self._device_id  = device_id
        self._uuid       = uuid

        # Key derivation (Tuya BLE v3)
        self._local_key_6: bytes = local_key[:6].encode("ascii")
        self._login_key:   bytes = hashlib.md5(self._local_key_6).digest()
        self._session_key: bytes | None = None

        self._on_dp_update    = on_dp_update
        self._on_disconnect_cb = on_disconnect

        self._client = None
        self._seq    = 0
        self._lock   = asyncio.Lock()
        self._connected = False
        self._is_paired = False

        # Reassembly state for incoming fragmented messages
        self._in_buf:             bytearray | None = None
        self._in_expected_packet: int = 0
        self._in_expected_len:    int = 0

        # Pending response futures keyed by seq_num of the sent packet
        self._pending: dict[int, asyncio.Future[int]] = {}

    # ── Public API ─────────────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        """True only after BLE connect + successful pairing handshake."""
        return self._connected and self._is_paired

    async def connect(self) -> bool:
        """Establish BLE connection and complete the Tuya pairing handshake."""
        async with self._lock:
            if self._connected and self._is_paired:
                return True

            # Reset state from any previous attempt
            self._seq = 0
            self._session_key = None
            self._is_paired = False
            for f in self._pending.values():
                if not f.done():
                    f.cancel()
            self._pending.clear()

            try:
                if _HAS_RETRY:
                    self._client = await establish_connection(
                        BleakClientWithServiceCache,
                        self._ble_device,
                        self._ble_device.address,
                        self._handle_disconnect,
                        use_services_cache=True,
                        ble_device_callback=lambda: self._ble_device,
                    )
                else:
                    from bleak import BleakClient  # noqa: PLC0415
                    self._client = BleakClient(
                        self._ble_device,
                        disconnected_callback=self._handle_disconnect,
                    )
                    await self._client.connect()

                self._connected = True
                await self._client.start_notify(
                    TUYA_BLE_NOTIFY_CHAR_UUID, self._notification_handler
                )

                _LOGGER.debug("BLE connected to %s; starting handshake", self._ble_device.address)

                # Step 1: DEVICE_INFO → derive session_key
                if not await self._handshake_device_info():
                    _LOGGER.error("DEVICE_INFO handshake failed")
                    return False

                # Step 2: PAIR
                if not await self._handshake_pair():
                    _LOGGER.error("PAIR handshake failed")
                    return False

                self._is_paired = True
                _LOGGER.info(
                    "Tuya BLE handshake complete — paired to %s",
                    self._ble_device.address,
                )

                # Query current DP state
                await self._send(_CMD_DEVICE_STATUS, b"")
                return True

            except Exception as exc:
                _LOGGER.error("BLE connection failed: %s", exc)
                self._connected = False
                self._is_paired = False
                return False

    async def disconnect(self) -> None:
        """Cleanly close the BLE connection."""
        self._connected = False
        self._is_paired = False
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None

    async def query_dps(self) -> None:
        """Ask the device to report all DP values."""
        await self._send(_CMD_DEVICE_STATUS, b"")

    async def publish_dp(self, dp: TuyaDP) -> None:
        """Write a single DP value to the device."""
        payload = encode_dp(dp)
        _LOGGER.debug(
            "publish_dp: id=%d type=%d value=%r payload=%s",
            dp.dp_id, dp.dp_type, dp.value, payload.hex(),
        )
        await self._send(_CMD_SEND_DPS, payload)

    async def heartbeat(self) -> None:
        """Keep-alive: query current state."""
        await self._send(_CMD_DEVICE_STATUS, b"")

    # ── Handshake helpers ──────────────────────────────────────────────────────

    async def _handshake_device_info(self) -> bool:
        seq = self._next_seq()
        future: asyncio.Future[int] = asyncio.get_event_loop().create_future()
        self._pending[seq] = future
        await self._send_raw(_CMD_DEVICE_INFO, b"", seq)
        try:
            await asyncio.wait_for(future, RESPONSE_TIMEOUT)
            return True
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout waiting for DEVICE_INFO response")
            self._pending.pop(seq, None)
            return False

    async def _handshake_pair(self) -> bool:
        seq = self._next_seq()
        future: asyncio.Future[int] = asyncio.get_event_loop().create_future()
        self._pending[seq] = future
        await self._send_raw(_CMD_PAIR, self._build_pair_payload(), seq)
        try:
            result = await asyncio.wait_for(future, RESPONSE_TIMEOUT)
            if result not in (0, 2):  # 2 = "already paired" is also OK
                _LOGGER.error("Pairing rejected by device, result=%d", result)
                return False
            return True
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout waiting for PAIR response")
            self._pending.pop(seq, None)
            return False

    def _build_pair_payload(self) -> bytes:
        """Build the 44-byte pairing payload: uuid + local_key_6 + device_id."""
        buf = bytearray()
        buf += self._uuid.encode()
        buf += self._local_key_6
        buf += self._device_id.encode()
        # Zero-pad to exactly 44 bytes
        if len(buf) < 44:
            buf += b"\x00" * (44 - len(buf))
        return bytes(buf[:44])

    # ── Internal send helpers ──────────────────────────────────────────────────

    def _next_seq(self) -> int:
        self._seq = (self._seq + 1) & 0xFFFFFFFF
        return self._seq

    async def _send(self, code: int, data: bytes) -> None:
        await self._send_raw(code, data, self._next_seq())

    async def _send_response(self, code: int, data: bytes, response_to: int) -> None:
        if self._client and self._connected:
            await self._send_raw(code, data, self._next_seq(), response_to=response_to)

    async def _send_raw(
        self, code: int, data: bytes, seq: int, response_to: int = 0
    ) -> None:
        if not self._client or not self._connected:
            raise ConnectionError("Not connected to device")
        packets = _build_packets(
            seq, code, data,
            login_key=self._login_key,
            session_key=self._session_key,
            response_to=response_to,
        )
        for pkt in packets:
            await self._client.write_gatt_char(
                TUYA_BLE_WRITE_CHAR_UUID, pkt, response=False
            )

    # ── Notification handler ───────────────────────────────────────────────────

    def _notification_handler(self, _sender: int, raw: bytearray) -> None:
        """Reassemble fragmented packets then parse."""
        data = bytes(raw)
        _LOGGER.debug("RAW NOTIFY (%d B): %s", len(data), data.hex())

        try:
            pos = 0
            packet_num, pos = _unpack_int(data, pos)

            if packet_num == 0:
                # First fragment: read total length and skip version byte
                self._in_expected_len, pos = _unpack_int(data, pos)
                pos += 1  # protocol_version byte
                self._in_buf = bytearray()
                self._in_expected_packet = 1
            elif packet_num == self._in_expected_packet:
                self._in_expected_packet += 1
            else:
                _LOGGER.warning(
                    "Unexpected packet_num %d (expected %d), dropping",
                    packet_num, self._in_expected_packet,
                )
                self._in_buf = None
                self._in_expected_packet = 0
                return

            if self._in_buf is None:
                return

            self._in_buf += data[pos:]

            if len(self._in_buf) >= self._in_expected_len:
                payload = bytes(self._in_buf[: self._in_expected_len])
                self._in_buf = None
                self._in_expected_packet = 0
                self._parse_input(payload)

        except Exception as exc:
            _LOGGER.warning("Notification handler error: %s", exc)
            self._in_buf = None
            self._in_expected_packet = 0

    def _parse_input(self, payload: bytes) -> None:
        """Decrypt and dispatch an assembled Tuya BLE message."""
        if len(payload) < 17:
            _LOGGER.warning("Payload too short (%d bytes)", len(payload))
            return

        security_flag = payload[0]
        iv            = payload[1:17]
        encrypted     = payload[17:]

        key = self._login_key if security_flag == 4 else (self._session_key or self._login_key)

        try:
            raw = _aes_cbc_decrypt(key, iv, encrypted)
        except Exception as exc:
            _LOGGER.warning("AES decrypt failed: %s", exc)
            return

        if len(raw) < 12:
            return

        seq_num, response_to, code, data_len = struct.unpack(">IIHH", raw[:12])
        data = raw[12: 12 + data_len]

        _LOGGER.debug(
            "Decoded: seq=%d resp_to=%d code=0x%04x data(%dB)=%s",
            seq_num, response_to, code, len(data), data.hex(),
        )

        self._dispatch(seq_num, response_to, code, data)

    def _dispatch(self, seq_num: int, response_to: int, code: int, data: bytes) -> None:
        """Route decoded messages to the right handler."""

        if code == _CMD_DEVICE_INFO:
            # Response from step-1 handshake — extract srand, derive session_key
            if len(data) >= 12:
                srand = data[6:12]
                self._session_key = hashlib.md5(self._local_key_6 + srand).digest()
                _LOGGER.debug("session_key derived (srand=%s)", srand.hex())
            future = self._pending.pop(response_to, None)
            if future and not future.done():
                future.set_result(0)

        elif code == _CMD_PAIR:
            # Response from step-2 handshake
            result = data[0] if data else 1
            _LOGGER.debug("PAIR response: result=%d", result)
            future = self._pending.pop(response_to, None)
            if future and not future.done():
                future.set_result(result)

        elif code == _CMD_SEND_DPS:
            # Device ACK for our DP write — data[0]=0 means ok, silence the log
            _LOGGER.debug("DP write ACK (resp_to=%d result=%d)", response_to, data[0] if data else -1)

        elif code == _CMD_DEVICE_STATUS:
            # ACK for our status query — data[0]=0 means ok
            future = self._pending.pop(response_to, None)
            if future and not future.done():
                future.set_result(data[0] if data else 0)

        elif code in (_CMD_RECEIVE_DP, _CMD_RECEIVE_TIME_DP,
                      _CMD_RECEIVE_SIGN_DP, _CMD_RECEIVE_SIGN_TIME_DP):
            # DP push from device — parse and ACK
            dp_data = data
            if code in (_CMD_RECEIVE_SIGN_DP, _CMD_RECEIVE_SIGN_TIME_DP):
                dp_data = data[3:]  # skip dp_seq(2B) + flags(1B)

            dps = decode_dps(dp_data)
            _LOGGER.debug("DP push: %s", [(d.dp_id, d.value) for d in dps])
            if dps and self._on_dp_update:
                self._on_dp_update(dps)

            # ACK the device
            ack_data = b""
            if code in (_CMD_RECEIVE_SIGN_DP, _CMD_RECEIVE_SIGN_TIME_DP) and len(data) >= 3:
                dp_seq = int.from_bytes(data[:2], "big")
                flags  = data[2]
                ack_data = struct.pack(">HBB", dp_seq, flags, 0)
            asyncio.create_task(self._send_response(code, ack_data, seq_num))

        elif code == _CMD_TIME1_REQ:
            # Device asks for millisecond timestamp
            ts = int(time.time_ns() // 1_000_000)
            tz = -int(time.timezone // 36)
            resp = str(ts).encode() + struct.pack(">h", tz)
            asyncio.create_task(self._send_response(code, resp, seq_num))

        elif code == _CMD_TIME2_REQ:
            # Device asks for local time struct
            t  = time.localtime()
            tz = -int(time.timezone // 36)
            resp = struct.pack(
                ">BBBBBBBh",
                t.tm_year % 100, t.tm_mon, t.tm_mday,
                t.tm_hour, t.tm_min, t.tm_sec,
                t.tm_wday, tz,
            )
            asyncio.create_task(self._send_response(code, resp, seq_num))

        else:
            _LOGGER.warning("=== Unhandled code 0x%04x data=%s ===", code, data.hex())

    def _handle_disconnect(self, _client: object) -> None:
        self._connected = False
        self._is_paired = False
        _LOGGER.warning("Device disconnected")
        # Cancel any pending futures
        for f in self._pending.values():
            if not f.done():
                f.cancel()
        self._pending.clear()
        if self._on_disconnect_cb:
            self._on_disconnect_cb()
