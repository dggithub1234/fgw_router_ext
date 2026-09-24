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

# Import the new hardcoded variables directly from const.py
from .const import HARDCODED_SCAN_INTERVAL, HARDCODED_TRACK_NEW_DEVICES
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
    
    async def async_update_router_data() -> set[str]:
        try:
            data = await fetch_fgw_data(host, port, username, password)
            if data is None:
                _LOGGER.debug("Router returned no active devices; treating as empty set.")
                return set()
            return {mac.upper() for mac in data}
        except Exception as err:
            raise UpdateFailed(f"Communication issue with FGW router: {err}")

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"FGW Router Tracker {host}",
        update_method=async_update_router_data,
        # Strictly use your file-defined scan interval
        update_interval=timedelta(seconds=HARDCODED_SCAN_INTERVAL),
    )

    await coordinator.async_config_entry_first_refresh()
    tracked_macs: set[str] = set()

    @callback
    def async_discover_devices() -> None:
        """Dynamically add entities if new MACs appear in the router table."""
        if not coordinator.data:
            return
            
        active_macs = {mac.upper() for mac in coordinator.data}
        new_macs = active_macs - tracked_macs
        if not new_macs:
            return

        entities = []
        for mac in new_macs:
            upper_mac = mac.upper()
            # Dynamic settings check removed; relies strictly on file constant
            enabled_by_default = HARDCODED_TRACK_NEW_DEVICES
            entities.append(FGWScannerEntity(coordinator, upper_mac, enabled_by_default))
            tracked_macs.add(upper_mac)

        async_add_entities(entities)

    # Listeners removed since we no longer respond to live options modifications
    entry.async_on_unload(coordinator.async_add_listener(async_discover_devices))
    
    async_discover_devices()


class FGWScannerEntity(ScannerEntity):
    """Representation of a device tracked via the modern FiberGateway integration."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: DataUpdateCoordinator, mac: str, enabled_by_default: bool = True) -> None:
        """Initialize the tracker entity."""
        self.coordinator = coordinator
        self._mac = mac.upper()
        self._attr_unique_id = f"fgw_{self._mac.replace(':', '').replace('-', '').lower()}"
        self._attr_name = f"Device {self._mac}"
        self._attr_entity_registry_enabled_default = enabled_by_default

    @property
    def available(self) -> bool:
        """Return True if the entity is available to process state updates."""
        return self.coordinator.last_update_success

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
        if not self.coordinator.data:
            return False
        return self._mac in self.coordinator.data

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates safely."""
        @callback
        def async_update_state() -> None:
            """Write state safely during coordinator refresh intervals."""
            self.async_write_ha_state()

        self.async_on_remove(self.coordinator.async_add_listener(async_update_state))
