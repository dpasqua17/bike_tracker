"""
BLE Heart Rate Service client.
Spec: Bluetooth SIG Heart Rate Service 0x180D / Heart Rate Measurement 0x2A37
"""

import asyncio
import logging
from typing import Awaitable, Callable, Optional

from bleak import BleakClient, BleakScanner

log = logging.getLogger(__name__)

HEART_RATE_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"
HEART_RATE_MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb"


def parse_heart_rate_measurement(data: bytes) -> Optional[int]:
    if len(data) < 2:
        return None
    flags = data[0]
    if flags & 0x01:
        if len(data) < 3:
            return None
        return int.from_bytes(data[1:3], "little")
    return data[1]


class HeartRateClient:
    def __init__(
        self,
        on_data: Callable[[int], Awaitable[None]],
        on_connect: Optional[Callable[[str], None]] = None,
        on_disconnect: Optional[Callable[[], None]] = None,
    ):
        self.on_data = on_data
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self._client: Optional[BleakClient] = None
        self.connected = False
        self.device_name: Optional[str] = None

    async def scan(
        self,
        *,
        timeout: float = 8.0,
        name: Optional[str] = None,
        aliases: Optional[list[str]] = None,
    ) -> list[dict]:
        aliases = aliases or []
        names = [candidate.lower() for candidate in [name, *aliases] if candidate]
        devices = await BleakScanner.discover(timeout=timeout)
        found: list[dict] = []
        for device in devices:
            device_name = (device.name or "Unknown").strip()
            address = getattr(device, "address", None)
            if not address:
                continue
            if names and not any(alias in device_name.lower() for alias in names):
                continue
            found.append({"name": device_name, "address": address})
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

            has_hr_service = any(
                str(service.uuid).lower() == HEART_RATE_SERVICE
                for service in self._client.services
            )
            if not has_hr_service:
                await self._client.disconnect()
                self.connected = False
                return False

            for svc in self._client.services:
                for char in svc.characteristics:
                    if "2a00" in str(char.uuid).lower():
                        try:
                            name_bytes = await self._client.read_gatt_char(char.uuid)
                            self.device_name = name_bytes.decode("utf-8", errors="replace")
                        except Exception:
                            pass

            if self.on_connect:
                self.on_connect(self.device_name or address)
            return True
        except Exception:
            log.exception("Heart-rate connection failed")
            self.connected = False
            return False

    async def start_streaming(self):
        if not self._client or not self.connected:
            raise RuntimeError("Heart-rate client not connected")

        async def _dispatch(data: bytearray):
            hr = parse_heart_rate_measurement(bytes(data))
            if hr is None:
                return
            try:
                await self.on_data(hr)
            except Exception:
                log.exception("Failed processing heart-rate notification")

        def handler(sender, data: bytearray):
            asyncio.create_task(_dispatch(data))

        await self._client.start_notify(HEART_RATE_MEASUREMENT, handler)

    async def disconnect(self):
        if self._client and self.connected:
            try:
                await self._client.stop_notify(HEART_RATE_MEASUREMENT)
            except Exception:
                pass
            await self._client.disconnect()
        self.connected = False

    def _on_ble_disconnect(self, client):
        self.connected = False
        if self.on_disconnect:
            self.on_disconnect()
