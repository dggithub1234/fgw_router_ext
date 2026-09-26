"""Support for Altice / MEO FiberGateway routers and extenders using modern entities."""

import asyncio
from datetime import timedelta
import os
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
from homeassistant.helpers import entity_registry as er

from .const import HARDCODED_SCAN_INTERVAL, HARDCODED_TRACK_NEW_DEVICES
from .router import fetch_fgw_data

_LOGGER = logging.getLogger(__name__)


def load_manual_macs(config_dir: str) -> dict[str, str]:
    """Synchronous helper to read manual MAC mapping file."""
    file_path = os.path.join(config_dir, "manual_macs.txt")
    mapping = {}
    if not os.path.exists(file_path):
        return mapping
        
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                cleaned = line.strip()
                if not cleaned or cleaned.startswith("#") or "=" not in cleaned:
                    continue
                mac, name = cleaned.split("=", 1)
                mapping[mac.strip().upper()] = name.strip()
    except Exception as err:
        _LOGGER.error("Error reading manual_macs.txt: %s", err)
    return mapping

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
    
    # CHANGE THIS: Point to the root HA config folder instead of the integration folder
    #current_dir = os.path.dirname(__file__)
    #manual_mappings = await hass.async_add_executor_job(load_manual_macs, current_dir)
    config_dir = hass.config.config_dir
    manual_mappings = await hass.async_add_executor_job(load_manual_macs, config_dir)

    
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

    # Track what we have added to Home Assistant during this runtime session
    tracked_macs: set[str] = set()

    # Seed ONLY the manual MACs on startup (ignoring the historical entity registry)
    for manual_mac in manual_mappings:
        tracked_macs.add(manual_mac)

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
            enabled_by_default = HARDCODED_TRACK_NEW_DEVICES
            custom_name = manual_mappings.get(upper_mac)
            entities.append(FGWScannerEntity(coordinator, upper_mac, enabled_by_default, custom_name))
            tracked_macs.add(upper_mac)

        if entities:
            async_add_entities(entities)

    entry.async_on_unload(coordinator.async_add_listener(async_discover_devices))
    
    # Immediately spin up ONLY the manually configured entities on startup
    if tracked_macs:
        initial_entities = [
            FGWScannerEntity(coordinator, mac, True, manual_mappings.get(mac)) 
            for mac in tracked_macs
        ]
        async_add_entities(initial_entities)

    # Discover and add any active devices that are online right now
    async_discover_devices()


class FGWScannerEntity(ScannerEntity):
    """Representation of a device tracked via the modern FiberGateway integration."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: DataUpdateCoordinator, mac: str, enabled_by_default: bool = True, custom_name: str | None = None) -> None:
        """Initialize the tracker entity."""
        self.coordinator = coordinator
        self._mac = mac.upper()
        self._attr_unique_id = f"fgw_{self._mac.replace(':', '').replace('-', '').lower()}"
        
        # Use custom name if provided in file, otherwise fallback to default string
        if custom_name:
            self._attr_name = custom_name
        else:
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
