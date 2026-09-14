"""Config flow for Altice / MEO FiberGateway integration."""

import logging
from typing import Any
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult

from .router import fetch_fgw_data

_LOGGER = logging.getLogger(__name__)
DOMAIN = "fgw_router_ext"


class FiberGatewayConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Altice / MEO FiberGateway."""

    VERSION = 1

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
                if result is not None:
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
            }
        )

        return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)
