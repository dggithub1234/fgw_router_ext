"""Config flow for Altice / MEO FiberGateway integration."""

import logging
from typing import Any
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.const import (
    CONF_HOST, 
    CONF_PASSWORD, 
    CONF_PORT, 
    CONF_USERNAME,
)
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN, CONF_TRACK_NEW_DEVICES, CONF_SCAN_INTERVAL
from .router import fetch_fgw_data

_LOGGER = logging.getLogger(__name__)


class FiberGatewayConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Altice / MEO FiberGateway."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return FiberGatewayOptionsFlowHandler()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial user form configuration step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                result = await fetch_fgw_data(
                    user_input[CONF_HOST],
                    user_input[CONF_PORT],
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                )
                if result:
                    return self.async_create_entry(
                        title=f"FiberGateway ({user_input[CONF_HOST]})", 
                        data=user_input
                    )
                errors["base"] = "cannot_connect"
            except Exception as err:
                _LOGGER.error("Failed to connect to FiberGateway: %s", err)
                errors["base"] = "unknown"

        data_schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=23): int,
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_SCAN_INTERVAL, default=60): vol.All(vol.Coerce(int), vol.Range(min=10)),
                vol.Required(CONF_TRACK_NEW_DEVICES, default=True): bool,
            }
        )

        return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)


class FiberGatewayOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options for the Altice / MEO FiberGateway integration."""

    # Note: __init__ is completely removed to prevent read-only property setter conflicts.

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Access the automatically managed 'self.config_entry' context property safely
        current_entry = self.config_entry

        options_schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL, 
                    default=current_entry.options.get(
                        CONF_SCAN_INTERVAL, current_entry.data.get(CONF_SCAN_INTERVAL, 60)
                    )
                ): vol.All(vol.Coerce(int), vol.Range(min=10)),
                vol.Required(
                    CONF_TRACK_NEW_DEVICES, 
                    default=current_entry.options.get(
                        CONF_TRACK_NEW_DEVICES, current_entry.data.get(CONF_TRACK_NEW_DEVICES, True)
                    )
                ): bool,
            }
        )

        return self.async_show_form(step_id="init", data_schema=options_schema)
