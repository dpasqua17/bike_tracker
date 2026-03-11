"""
FTMS (Fitness Machine Service) BLE client.
Spec: Bluetooth SIG GATT 0x1826 / Indoor Bike Data 0x2ACC

Handles:
- Device scan + connect
- Indoor Bike Data characteristic notifications
- Fitness Machine Status
- Optional: Fitness Machine Control Point (resistance writes)
"""

import asyncio
import struct
import logging
from dataclasses import dataclass
from typing import Optional, Callable, Awaitable
from bleak import BleakScanner, BleakClient, BleakError

log = logging.getLogger(__name__)

# FTMS UUIDs (full 128-bit forms for bleak compatibility)
FTMS_SERVICE              = "00001826-0000-1000-8000-00805f9b34fb"
FITNESS_MACHINE_FEATURE   = "00002acc-0000-1000-8000-00805f9b34fb"
INDOOR_BIKE_DATA          = "00002ad2-0000-1000-8000-00805f9b34fb"
FITNESS_MACHINE_STATUS    = "00002ada-0000-1000-8000-00805f9b34fb"
MACHINE_CONTROL_POINT     = "00002ad9-0000-1000-8000-00805f9b34fb"
SUPPORTED_RESISTANCE      = "00002ad6-0000-1000-8000-00805f9b34fb"
INSTANTANEOUS_POWER       = "00002a63-0000-1000-8000-00805f9b34fb"

# Fallback short UUIDs (some devices expose these)
_SHORT = {
    "indoor_bike":  "2ad2",
    "feature":      "2acc",
    "status":       "2ada",
    "control":      "2ad9",
    "power":        "2a63",
}

_DEVICE_NAME_HINTS = ("bike", "cycle", "trainer", "joroto", "x4s", "gerato")


@dataclass
class BikeData:
    """Parsed snapshot from a single Indoor Bike Data notification."""
    timestamp: float = 0.0

    # Always present in FTMS Indoor Bike Data if flag bit set
    instantaneous_speed_kmh:   Optional[float] = None
    average_speed_kmh:         Optional[float] = None
    instantaneous_cadence_rpm: Optional[float] = None
    average_cadence_rpm:       Optional[float] = None
    total_distance_m:          Optional[int]   = None
    resistance_level:          Optional[int]   = None
    instantaneous_power_w:     Optional[int]   = None
    average_power_w:           Optional[int]   = None
    total_energy_kcal:         Optional[int]   = None
    heart_rate_bpm:            Optional[int]   = None
    metabolic_equivalent:      Optional[float] = None
    elapsed_time_s:            Optional[int]   = None
    remaining_time_s:          Optional[int]   = None

    @property
    def cadence(self) -> Optional[float]:
        return self.instantaneous_cadence_rpm

    @property
    def power(self) -> Optional[int]:
        return self.instantaneous_power_w

    @property
    def speed(self) -> Optional[float]:
        return self.instantaneous_speed_kmh

    @property
    def hr(self) -> Optional[int]:
        return self.heart_rate_bpm


def parse_indoor_bike_data(data: bytes, ts: float) -> BikeData:
    """
    Parse Indoor Bike Data (0x2ACC) per FTMS spec Table 4.9.
    Flags word tells us which optional fields are present.
    """
    bd = BikeData(timestamp=ts)
    if len(data) < 2:
        return bd

    offset = 0
    flags = struct.unpack_from('<H', data, offset)[0]
    offset += 2

    def read_u16() -> int:
        nonlocal offset
        val = struct.unpack_from('<H', data, offset)[0]
        offset += 2
        return val

    def read_s16() -> int:
        nonlocal offset
        val = struct.unpack_from('<h', data, offset)[0]
        offset += 2
        return val

    def read_u24() -> int:
        nonlocal offset
        b = data[offset:offset+3] + b'\x00'
        val = struct.unpack_from('<I', b)[0]
        offset += 3
        return val

    def read_u8() -> int:
        nonlocal offset
        val = data[offset]
        offset += 1
        return val

    # Bit 0: More Data (0 = instantaneous speed present)
    if not (flags & 0x0001):
        if offset + 2 <= len(data):
            bd.instantaneous_speed_kmh = read_u16() * 0.01  # unit: 1/100 km/h

    # Bit 1: Average Speed present
    if flags & 0x0002:
        if offset + 2 <= len(data):
            bd.average_speed_kmh = read_u16() * 0.01

    # Bit 2: Instantaneous Cadence present
    if flags & 0x0004:
        if offset + 2 <= len(data):
            bd.instantaneous_cadence_rpm = read_u16() * 0.5  # unit: 1/2 rpm

    # Bit 3: Average Cadence present
    if flags & 0x0008:
        if offset + 2 <= len(data):
            bd.average_cadence_rpm = read_u16() * 0.5

    # Bit 4: Total Distance present
    if flags & 0x0010:
        if offset + 3 <= len(data):
            bd.total_distance_m = read_u24()

    # Bit 5: Resistance Level present
    if flags & 0x0020:
        if offset + 2 <= len(data):
            bd.resistance_level = read_s16()

    # Bit 6: Instantaneous Power present
    if flags & 0x0040:
        if offset + 2 <= len(data):
            bd.instantaneous_power_w = read_s16()

    # Bit 7: Average Power present
    if flags & 0x0080:
        if offset + 2 <= len(data):
            bd.average_power_w = read_s16()

    # Bit 8: Expended Energy present (kcal total, kcal/hr, kcal/min)
    if flags & 0x0100:
        if offset + 2 <= len(data):
            bd.total_energy_kcal = read_u16()
        if offset + 2 <= len(data):
            read_u16()  # per hour (skip)
        if offset + 1 <= len(data):
            read_u8()   # per min (skip)

    # Bit 9: Heart Rate present
    if flags & 0x0200:
        if offset + 1 <= len(data):
            bd.heart_rate_bpm = read_u8()

    # Bit 10: Metabolic Equivalent
    if flags & 0x0400:
        if offset + 1 <= len(data):
            bd.metabolic_equivalent = read_u8() * 0.1

    # Bit 11: Elapsed Time
    if flags & 0x0800:
        if offset + 2 <= len(data):
            bd.elapsed_time_s = read_u16()

    # Bit 12: Remaining Time
    if flags & 0x1000:
        if offset + 2 <= len(data):
            bd.remaining_time_s = read_u16()

    return bd


class FTMSClient:
    """
    Async FTMS client. Usage:
        client = FTMSClient(on_data=my_callback)
        await client.scan_and_connect()
        # client runs until disconnect()
    """

    def __init__(
        self,
        on_data: Callable[[BikeData], Awaitable[None]],
        on_connect: Optional[Callable[[], None]] = None,
        on_disconnect: Optional[Callable[[], None]] = None,
        device_address: Optional[str] = None,
    ):
        self.on_data = on_data
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self._device_address = device_address
        self._client: Optional[BleakClient] = None
        self._running = False
        self.connected = False
        self.device_name: Optional[str] = None
        self._notify_uuid: Optional[str] = None

    async def scan(self, timeout: float = 10.0) -> list[dict]:
        """Scan for likely bike devices, preferring FTMS advertisements."""
        log.info("Scanning for FTMS devices...")
        ftms_devices = await self.scan_for_ftms(timeout=timeout)
        if ftms_devices:
            return ftms_devices

        log.info("No FTMS advertisements found, falling back to likely bike devices")
        found: list[dict] = []
        devices = await BleakScanner.discover(timeout=timeout)
        seen: set[str] = set()
        for d in devices:
            name = (d.name or "Unknown").strip()
            address = getattr(d, "address", None)
            if not address or address in seen:
                continue
            if any(hint in name.lower() for hint in _DEVICE_NAME_HINTS):
                log.info("Possible bike device found: %s [%s]", name, address)
                found.append({"name": name, "address": address})
                seen.add(address)
        return found

    async def find_preferred_device(
        self,
        *,
        name: Optional[str] = None,
        address: Optional[str] = None,
        aliases: Optional[list[str]] = None,
        timeout: float = 10.0,
    ) -> Optional[dict]:
        """Find a preferred bike by exact address or fuzzy name match."""
        aliases = aliases or []
        candidates = await self.scan(timeout=timeout)

        if address:
            for device in candidates:
                if device["address"].lower() == address.lower():
                    return device

        names = [n.lower() for n in [name, *aliases] if n]
        if names:
            for device in candidates:
                device_name = (device["name"] or "").lower()
                if any(n in device_name for n in names):
                    return device
        return None

    async def scan_for_ftms(self, timeout: float = 10.0) -> list[dict]:
        """Scan specifically for FTMS-advertising devices."""
        found: list[dict] = []
        seen: set[str] = set()

        def detection_cb(device, adv_data):
            uuids = [str(u).lower() for u in (adv_data.service_uuids or [])]
            if FTMS_SERVICE.lower() in uuids or "1826" in " ".join(uuids):
                if device.address in seen:
                    return
                seen.add(device.address)
                found.append({"name": device.name or "Unknown", "address": device.address})
                log.info(f"FTMS device found: {device.name} [{device.address}]")

        scanner = BleakScanner(detection_callback=detection_cb)
        await scanner.start()
        await asyncio.sleep(timeout)
        await scanner.stop()
        return found

    async def connect(self, address: str) -> bool:
        try:
            self._client = BleakClient(
                address,
                disconnected_callback=self._on_ble_disconnect,
            )
            await self._client.connect()
            self.connected = True
            self.device_name = address

            has_ftms_service = any(
                str(service.uuid).lower().startswith("00001826")
                or str(service.uuid).lower() == FTMS_SERVICE.lower()
                for service in self._client.services
            )
            if not has_ftms_service:
                log.error("Connected device does not expose FTMS service")
                await self._client.disconnect()
                self.connected = False
                return False

            # Try to get device name
            for svc in self._client.services:
                for char in svc.characteristics:
                    if "2a00" in str(char.uuid).lower():
                        try:
                            name_bytes = await self._client.read_gatt_char(char.uuid)
                            self.device_name = name_bytes.decode("utf-8", errors="replace")
                        except Exception:
                            pass

            log.info(f"Connected to {self.device_name}")
            if self.on_connect:
                self.on_connect()
            return True
        except Exception as e:
            log.exception("Connection failed")
            self.connected = False
            return False

    async def start_streaming(self):
        """Subscribe to Indoor Bike Data notifications."""
        if not self._client or not self.connected:
            raise RuntimeError("Not connected")

        self._running = True
        import time

        # Find the right characteristic UUID (handle short vs long)
        ibd_uuid = await self._resolve_uuid(INDOOR_BIKE_DATA, _SHORT["indoor_bike"])
        if not ibd_uuid:
            log.error("Indoor Bike Data characteristic not found — is this FTMS?")
            return

        async def _dispatch(data: bytearray):
            ts = time.time()
            bd = parse_indoor_bike_data(bytes(data), ts)
            try:
                await self.on_data(bd)
            except Exception:
                log.exception("Failed processing Indoor Bike Data notification")

        def handler(sender, data: bytearray):
            asyncio.create_task(_dispatch(data))

        await self._client.start_notify(ibd_uuid, handler)
        self._notify_uuid = str(ibd_uuid)
        log.info(f"Subscribed to Indoor Bike Data [{ibd_uuid}]")

        # Keep alive until disconnect
        while self._running and self.connected:
            await asyncio.sleep(0.5)

    async def disconnect(self):
        self._running = False
        if self._client and self.connected:
            if self._notify_uuid:
                try:
                    await self._client.stop_notify(self._notify_uuid)
                    log.info("Stopped Indoor Bike Data notifications [%s]", self._notify_uuid)
                except Exception:
                    log.exception("Failed stopping Indoor Bike Data notifications")
            try:
                await self._client.disconnect()
            finally:
                self._notify_uuid = None
        self.connected = False

    async def set_resistance(self, level: int):
        """Write resistance via FTMS Control Point (OpCode 0x04)."""
        if not config.ALLOW_BIKE_CONTROL_WRITES:
            log.warning("Resistance write blocked by config.ALLOW_BIKE_CONTROL_WRITES=False")
            return
        if not self._client or not self.connected:
            return
        cp_uuid = await self._resolve_uuid(MACHINE_CONTROL_POINT, _SHORT["control"])
        if not cp_uuid:
            return
        # Request control first (OpCode 0x00)
        await self._client.write_gatt_char(cp_uuid, bytes([0x00]), response=True)
        # Set target resistance (OpCode 0x04), value in 1/10 unitless
        payload = struct.pack('<Bh', 0x04, int(level * 10))
        await self._client.write_gatt_char(cp_uuid, payload, response=True)

    async def _resolve_uuid(self, long_uuid: str, short_suffix: str) -> Optional[str]:
        """Try long UUID first, fall back to short suffix match."""
        for svc in self._client.services:
            for char in svc.characteristics:
                u = str(char.uuid).lower()
                if u == long_uuid.lower() or u.startswith(f"0000{short_suffix}"):
                    return char.uuid
        return None

    def _on_ble_disconnect(self, client):
        log.warning("BLE disconnected")
        self.connected = False
        self._running = False
        if self.on_disconnect:
            self.on_disconnect()
