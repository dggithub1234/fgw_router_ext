"""Support for Altice / MEO FiberGateway routers and extenders using modern entities."""

import asyncio
from datetime import timedelta
import logging

from homeassistant.components.device_tracker import ScannerEntity, SourceType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.const import (
    CONF_HOST, 
    CONF_PORT, 
    CONF_PASSWORD, 
    CONF_USERNAME,
)

from .const import CONF_TRACK_NEW_DEVICES, CONF_SCAN_INTERVAL
from .router import fetch_fgw_data

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up FiberGateway modern device tracker entities from a config entry."""
    
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, entry.data.get(CONF_SCAN_INTERVAL, 60))
    
    async def async_update_router_data() -> set[str]:
        try:
            data = await fetch_fgw_data(host, port, username, password)
            if data is None:
                raise UpdateFailed("Router returned empty or invalid device table")
            return data
        except Exception as err:
            raise UpdateFailed(f"Communication issue with FGW router: {err}")

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"FGW Router Tracker {host}",
        update_method=async_update_router_data,
        update_interval=timedelta(seconds=scan_interval),
    )

    await coordinator.async_config_entry_first_refresh()
    tracked_macs: set[str] = set()

    @callback
    def async_discover_devices() -> None:
        """Dynamically add entities if new MACs appear in the router table."""
        active_macs = coordinator.data
        if not active_macs:
            return
            
        new_macs = active_macs - tracked_macs
        if not new_macs:
            return

        track_new_devices = entry.options.get(CONF_TRACK_NEW_DEVICES, entry.data.get(CONF_TRACK_NEW_DEVICES, True))

        entities = []
        for mac in new_macs:
            enabled_by_default = track_new_devices
            entities.append(FGWScannerEntity(coordinator, mac, enabled_by_default))
            tracked_macs.add(mac)

        async_add_entities(entities)

    @callback
    def async_update_options(change_entry: ConfigEntry) -> None:
        """Update options dynamically when modified by user in frontend panels."""
        # Fixed signature: Home Assistant update listeners pass EXACTLY ONE parameter (the updated config entry)
        new_interval = change_entry.options.get(CONF_SCAN_INTERVAL, change_entry.data.get(CONF_SCAN_INTERVAL, 60))
        coordinator.update_interval = timedelta(seconds=new_interval)
        _LOGGER.debug("DataUpdateCoordinator interval dynamically updated to %s seconds", new_interval)

    # Correct listener attachment hooks
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    entry.async_on_unload(coordinator.async_add_listener(async_discover_devices))
    
    async_discover_devices()


class FGWScannerEntity(ScannerEntity):
    """Representation of a device tracked via the modern FiberGateway integration."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: DataUpdateCoordinator, mac: str, enabled_by_default: bool = True) -> None:
        """Initialize the tracker entity."""
        self.coordinator = coordinator
        self._mac = mac
        self._attr_unique_id = f"fgw_{mac.replace(':', '').replace('-', '').lower()}"
        self._attr_name = f"Device {mac}"
        self._attr_entity_registry_enabled_default = enabled_by_default

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
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))
