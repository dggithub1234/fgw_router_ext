"""Support for Altice / MEO FiberGateway routers and extenders using modern entities."""

import asyncio
from datetime import timedelta
import logging

from homeassistant.components.device_tracker import ScannerEntity, SourceType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
# IMPORT RestoreEntity to ensure states persist across Home Assistant reboots
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.const import (
    CONF_HOST, 
    CONF_PORT, 
    CONF_PASSWORD, 
    CONF_USERNAME,
)

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
        update_interval=timedelta(seconds=HARDCODED_SCAN_INTERVAL),
    )

    await coordinator.async_config_entry_first_refresh()
    tracked_macs: set[str] = set()

    # CRITICAL: Pull all previously registered entities from the Entity Registry
    # This guarantees that even if a phone is away for weeks and not in the router data,
    # its entity structure is loaded into Home Assistant at boot time.
    ent_reg = homeassistant.helpers.entity_registry.async_get(hass)
    restored_entities = []
    
    for entity_entry in homeassistant.helpers.entity_registry.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if entity_entry.domain == "device_tracker":
            # Extract original MAC address from unique_id or name safely
            # Assuming format: fgw_macaddresswithoutcolons
            uid = entity_entry.unique_id
            if uid.startswith("fgw_"):
                raw_mac = uid.replace("fgw_", "")
                # Reconstruct standardized colons formatting for internal match lookup
                mac_with_colons = ":".join(raw_mac[i:i+2].upper() for i in range(0, len(raw_mac), 2))
                
                if mac_with_colons not in tracked_macs:
                    restored_entities.append(FGWScannerEntity(coordinator, mac_with_colons, True))
                    tracked_macs.add(mac_with_colons)

    if restored_entities:
        async_add_entities(restored_entities)

    @callback
    def async_discover_devices() -> None:
        """Dynamically add brand new entities if new MACs appear in the router table."""
        if not coordinator.data:
            return
            
        active_macs = {mac.upper() for mac in coordinator.data}
        new_macs = active_macs - tracked_macs
        if not new_macs:
            return

        entities = []
        for mac in new_macs:
            upper_mac = mac.upper()
            enabled_by_default = HARDCODED_TRACK_NEW_DEVICES
            entities.append(FGWScannerEntity(coordinator, upper_mac, enabled_by_default))
            tracked_macs.add(upper_mac)

        async_add_entities(entities)

    entry.async_on_unload(coordinator.async_add_listener(async_discover_devices))
    async_discover_devices()


class FGWScannerEntity(ScannerEntity, RestoreEntity):
    """Representation of a device tracked via the modern FiberGateway integration with state restoration."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: DataUpdateCoordinator, mac: str, enabled_by_default: bool = True) -> None:
        """Initialize the tracker entity."""
        self.coordinator = coordinator
        self._mac = mac.upper()
        self._attr_unique_id = f"fgw_{self._mac.replace(':', '').replace('-', '').lower()}"
        self._attr_name = f"Device {self._mac}"
        self._attr_entity_registry_enabled_default = enabled_by_default
        self._restored_connected_state = False

    @property
    def available(self) -> bool:
        """Always return true to retain continuous tracker lifecycle profiles."""
        return True

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
        """Return true if the device is currently active on the router or falls back to last state."""
        if not self.coordinator.data:
            # Fall back to restore state data if router returns empty/failed update structures
            return self._restored_connected_state
        return self._mac in self.coordinator.data

    async def async_added_to_hass(self) -> None:
        """Subscribe to updates and gracefully restore historical state indexes on boot."""
        # Pull the last known state from home assistant local cache storage files
        last_state = await self.async_get_last_state()
        if last_state:
            # Home Assistant stores tracker boolean logic via "home" (True) or "not_home" (False) strings
            self._restored_connected_state = (last_state.state == "home")

        @callback
        def async_update_state() -> None:
            """Write state safely during coordinator refresh intervals."""
            if self.coordinator.data:
                self._restored_connected_state = (self._mac in self.coordinator.data)
            self.async_write_ha_state()

        self.async_on_remove(self.coordinator.async_add_listener(async_update_state))
