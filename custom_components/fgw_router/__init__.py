"""The fgw_router_ext component."""
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Only forwarding the device tracker platform for this integration
PLATFORMS: list[Platform] = [Platform.DEVICE_TRACKER]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Altice / MEO FiberGateway from a config entry."""
    _LOGGER.debug("Setting up FiberGateway config entry: %s", entry.entry_id)

    # Forward the configuration entry setup to the device_tracker.py platform
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a FiberGateway config entry when deleted or disabled."""
    _LOGGER.debug("Unloading FiberGateway config entry: %s", entry.entry_id)
    
    # Safely tear down active tracking entities
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    return unload_ok
