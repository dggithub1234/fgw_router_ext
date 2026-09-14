"""Support for Altice / MEO FiberGateway routers and extenders using modern entities."""

import asyncio
from datetime import timedelta
import logging
import re
from typing import Any

from homeassistant.components.device_tracker import (
    DOMAIN as DEVICE_TRACKER_DOMAIN,
    ScannerEntity,
    SourceType,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from homeassistant.const import CONF_HOST, CONF_PORT, CONF_PASSWORD, CONF_USERNAME

_LOGGER = logging.getLogger(__name__)

# Resilient parsing matching both standard formats
_DHCP_REGEX = re.compile(
    r"(?P<mac>([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}).*?\|\s*(?P<port>[a-zA-Z0-9\.\-_ ]+?)\s*\|\s*(?P<active>true|false|active|yes|1)",
    re.IGNORECASE,
)
_WIFI_REGEX = re.compile(r"(?P<mac>([0-9A-F]{2}[:-]){5}[0-9A-F]{2})\s*\|\s*(Yes|Active|1)", re.IGNORECASE)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up FiberGateway modern device tracker entities from a config entry."""
    
    async def async_update_router_data() -> set[str]:
        try:
            return await fetch_fgw_data(
                entry.data[CONF_HOST],
                entry.data[CONF_PORT],
                entry.data[CONF_USERNAME],
                entry.data[CONF_PASSWORD]
            )
        except Exception as err:
            raise UpdateFailed(f"Communication issue with FGW router: {err}")

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"FGW Router Tracker {entry.data[CONF_HOST]}",
        update_method=async_update_router_data,
        update_interval=timedelta(seconds=30),
    )

    # Fetch initial state
    await coordinator.async_config_entry_first_refresh()

    tracked_macs: set[str] = set()

    @callback
    def async_discover_devices() -> None:
        """Dynamically add entities if new MACs appear in the router table."""
        active_macs = coordinator.data
        new_macs = active_macs - tracked_macs
        if not new_macs:
            return

        entities = []
        for mac in new_macs:
            entities.append(FGWScannerEntity(coordinator, mac))
            tracked_macs.add(mac)

        async_add_entities(entities)

    entry.async_on_unload(coordinator.async_add_listener(async_discover_devices))
    async_discover_devices()


class FGWScannerEntity(ScannerEntity):
    """Representation of a device tracked via the modern FiberGateway integration."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: DataUpdateCoordinator, mac: str) -> None:
        """Initialize the tracker entity."""
        self.coordinator = coordinator
        self._mac = mac
        self._attr_unique_id = f"fgw_{mac.replace(':', '').replace('-', '').lower()}"
        self._attr_name = f"Device {mac}"

    @property
    def source_type(self) -> SourceType:
        """Return the tracking source type."""
        return SourceType.ROUTER

    @property
    def mac_address(self) -> str:
        """Return the mac address of the device."""
        return self._mac

    @property
    def is_connected(self) -> bool:
        """Return true if the device is currently active on the router."""
        return self._mac in self.coordinator.data

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates."""
        self.async_on_unload(self.coordinator.async_add_listener(self.async_write_ha_state))


async def _read_until(reader, expect_bytes, timeout=30):
    """Helper to strictly read from stream until expected sequence is hit."""
    buffer = bytearray()
    while expect_bytes not in buffer:
        try:
            chunk = await asyncio.wait_for(reader.read(1024), timeout=timeout)
            if not chunk:
                break
            buffer.extend(chunk)
        except asyncio.TimeoutError:
            _LOGGER.error(
                "Telnet stream timed out waiting for sequence: %s. Current Buffer: %s",
                expect_bytes,
                buffer.decode("utf-8", errors="ignore")
            )
            raise
    return bytes(buffer)


async def fetch_fgw_data(host, port, username, password) -> set[str]:
    """Retrieve and parse connected devices from FGW router asynchronously."""
    devices = set()
    
    try:
        connect = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(connect, timeout=15)
    except Exception as err:
        _LOGGER.error("Unable to open TCP connection to FiberGateway at %s:%s - %s", host, port, err)
        return devices

    try:
        # Step 1: Wait for lowercase login prompt from Yocto build
        await _read_until(reader, b"login:")
        
        # Step 2: Write username and wait for lowercase password prompt
        writer.write(f"{username}\r\n".encode("ascii"))
        await writer.drain()
        await _read_until(reader, b"password:")
        
        # Step 3: Write password and wait for cli prompt
        writer.write(f"{password}\r\n".encode("ascii"))
        await writer.drain()
        await _read_until(reader, b"cli> ")

        # Step 4: Write command to retrieve leases
        writer.write(b"lan/dhcp/show\r\n")
        await writer.drain()
        output = await _read_until(reader, b"cli> ")
        
        # Step 5: Quit gracefully
        writer.write(b"quit\r\n")
        await writer.drain()
        
    except Exception as err:
        _LOGGER.error("Telnet execution broke during router conversation exchange: %s", err)
        return devices
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

    decoded = output.decode("utf-8", errors="ignore")
    _LOGGER.debug("Parsed Data Table Response: %s", decoded)

    for match in _DHCP_REGEX.finditer(decoded):
        mac = match.group("mac").upper()
        active_val = match.group("active").lower()
        if active_val in ("true", "active", "yes", "1"):
            devices.add(mac)

    if devices:
        return devices

    # Fallback to wireless station list if DHCP parsing is empty
    _LOGGER.warning("DHCP table empty, dropping into legacy wifi station check.")
    all_lines = []
    try:
        connect = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(connect, timeout=15)
        
        await _read_until(reader, b"login:")
        writer.write(f"{username}\r\n".encode("ascii"))
        await writer.drain()
        
        await _read_until(reader, b"password:")
        writer.write(f"{password}\r\n".encode("ascii"))
        await writer.drain()
        await _read_until(reader, b"cli> ")

        for idx in:
            cmd = f"wireless/show-stationinfo --wifi-index={idx}\r\n"
            writer.write(cmd.encode("ascii"))
            await writer.drain()
            out = await _read_until(reader, b"cli> ")
            all_lines.extend(out.split(b"\r\n"))
            
        writer.write(b"quit\r\n")
        await writer.drain()
    except Exception as err:
        _LOGGER.error("Fallback wireless communication failed: %s", err)
        return devices
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

    for line in all_lines:
        match = _WIFI_REGEX.search(line.decode("utf-8", errors="ignore"))
        if match:
            devices.add(match.group("mac").upper())

    return devices
