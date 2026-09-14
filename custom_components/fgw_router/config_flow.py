"""Config flow for Altice / MEO FiberGateway integration."""

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult

# Import the functional telnet checker from your device_tracker file
from .device_tracker import fetch_fgw_data

_LOGGER = logging.getLogger(__name__)

DOMAIN = "meo_fibergateway"  # Make sure this matches your folder name


class FiberGatewayConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Altice / MEO FiberGateway."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial user form configuration step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]

            # Validate the connection before creating the configuration entry
            try:
                result = await fetch_fgw_data(host, port, username, password)
                if result is not None:
                    # Connection succeeded! Create the entity registry mapping.
                    return self.async_create_entry(
                        title=f"FiberGateway ({host})", 
                        data=user_input
                    )
                
                errors["base"] = "cannot_connect"
            except Exception as err:  # pylint: disable=broad-except
                _LOGGER.error("Failed to connect to FiberGateway: %s", err)
                errors["base"] = "unknown"

        # Form schema shown to the user in the UI
        data_schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=23): int,
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )

        return self.async_show_form(
            step_id="user", 
            data_schema=data_schema, 
            errors=errors
        )
