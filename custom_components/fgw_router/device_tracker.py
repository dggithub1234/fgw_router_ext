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

# Reuse your configuration constants
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_PASSWORD, CONF_USERNAME

_LOGGER = logging.getLogger(__name__)

_DHCP_REGEX = re.compile(
    r"(?P<mac>([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}).*?\|\s*(?P<port>[a-z0-9\.]+)\s*\|\s*(?P<active>true|false)",
    re.IGNORECASE,
)
_WIFI_REGEX = re.compile(r"(?P<mac>([0-9A-F]{2}[:-]){5}[0-9A-F]{2})\s*\|\s*Yes", re.IGNORECASE)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up FiberGateway modern device tracker entities from a config entry."""
    
    # Define the fetch function wrapped by the coordinator
    async def async_update_router_data() -> set[str]:
        try:
            return await fetch_fgw_data(
                entry.data[CONF_HOST],
                entry.data[CONF_PORT],
                entry.data[CONF_USERNAME],
                entry.data[CONF_PASSWORD]
            )
        except Exception as err:
            raise UpdateFailed(f"Error communicating with FGW router: {err}")

    # Create the coordinator to poll the router every 30 seconds
    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"FGW Router Tracker {entry.data[CONF_HOST]}",
        update_method=async_update_router_data,
        update_interval=timedelta(seconds=30),
    )

    # First fetch before adding entities
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

    # Watch for newly discovered network items on future polls
    entry.async_on_unload(coordinator.async_add_listener(async_discover_devices))
    
    # Initialize already active ones
    async_discover_devices()


class FGWScannerEntity(ScannerEntity):
    """Representation of a device tracked via the modern FiberGateway integration."""

    _attr_has_entity_name = True
    _attr_should_poll = False  # Controlled entirely via Coordinator updates

    def __init__(self, coordinator: DataUpdateCoordinator, mac: str) -> None:
        """Initialize the tracker entity."""
        self.coordinator = coordinator
        self._mac = mac
        self._attr_unique_id = f"fgw_{mac.replace(':', '').lower()}"
        # Entity fallback name if no manual name override exists
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


# --- Raw Connection Logic Restructured safely for Async Core ---

async def _async_telnet_command(reader, writer, command_bytes, expect_bytes):
    """Helper to send a command and wait for a response sequence."""
    writer.write(command_bytes)
    await writer.drain()
    
    buffer = bytearray()
    while expect_bytes not in buffer:
        chunk = await asyncio.wait_for(reader.read(1024), timeout=10)
        if not chunk:
            break
        buffer.extend(chunk)
    return bytes(buffer)


async def fetch_fgw_data(host, port, username, password) -> set[str]:
    """Retrieve and parse connected devices from FGW router asynchronously."""
    devices = set()
    try:
        connect = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(connect, timeout=10)
    except Exception as err:
        _LOGGER.error("Failed to establish TCP connection to %s:%s: %s", host, port, err)
        return devices

    try:
        await _async_telnet_command(reader, writer, b"", b"Login: ")
        await _async_telnet_command(reader, writer, f"{username}\r\n".encode("ascii"), b"Password: ")
        output = await _async_telnet_command(reader, writer, f"{password}\r\n".encode("ascii"), b"cli> ")

        # Fetch primary DHCP list
        output = await _async_telnet_command(reader, writer, b"lan/dhcp/show\r\n", b"cli> ")
        
        writer.write(b"quit\r\n")
        await writer.drain()
    except Exception as err:
        _LOGGER.error("Telnet communication error: %s", err)
        return devices
    finally:
        writer.close()
        await writer.wait_closed()

    decoded = output.decode("utf-8", errors="ignore")

    for match in _DHCP_REGEX.finditer(decoded):
        mac = match.group("mac").upper()
        if match.group("active").lower() == "true":
            devices.add(mac)

    if devices:
        return devices

    # Fallback to wireless station commands if DHCP parse yields nothing
    _LOGGER.warning("DHCP table empty, dropping into legacy wifi station check.")
    all_lines = []
    try:
        connect = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(connect, timeout=10)
        
        await _async_telnet_command(reader, writer, b"", b"Login: ")
        await _async_telnet_command(reader, writer, f"{username}\r\n".encode("ascii"), b"Password: ")
        await _async_telnet_command(reader, writer, f"{password}\r\n".encode("ascii"), b"cli> ")

        for idx in [0, 1]:  # interfaces
            cmd = f"wireless/show-stationinfo --wifi-index={idx}\r\n"
            out = await _async_telnet_command(reader, writer, cmd.encode("ascii"), b"cli> ")
            all_lines.extend(out.split(b"\r\n"))
            
        writer.write(b"quit\r\n")
        await writer.drain()
    except Exception as err:
        _LOGGER.error("Fallback scan broke: %s", err)
        return devices
    finally:
        writer.close()
        await writer.wait_closed()

    for line in all_lines:
        match = _WIFI_REGEX.search(line.decode("utf-8", errors="ignore"))
        if match:
            devices.add(match.group("mac").upper())

    return devices
