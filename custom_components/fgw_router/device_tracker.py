"""Support for Altice / MEO FiberGateway routers and extenders using modern entities."""

import asyncio
from datetime import timedelta
import logging

from homeassistant.components.device_tracker import ScannerEntity, SourceType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_PASSWORD, CONF_USERNAME

# Import network worker from internal router client
from .router import fetch_fgw_data

_LOGGER = logging.getLogger(__name__)


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
