"""Support for Altice / MEO FiberGateway routers and extenders."""

import logging
import re
import telnetlib
from contextlib import closing

import voluptuous as vol

from homeassistant.components.device_tracker import (
    DOMAIN,
    PLATFORM_SCHEMA,
    DeviceScanner,
)
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_PASSWORD, CONF_USERNAME
import homeassistant.helpers.config_validation as cv

_LOGGER = logging.getLogger(__name__)

# Regex to match MAC addresses and connection status from DHCP leases
_DHCP_REGEX = re.compile(
    r"(?P<mac>([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}).*?\|\s*(?P<port>[a-z0-9\.]+)\s*\|\s*(?P<active>true|false)",
    re.IGNORECASE,
)

# Regex for legacy wireless command
_WIFI_REGEX = re.compile(r"(?P<mac>([0-9A-F]{2}[:-]){5}[0-9A-F]{2})\s*\|\s*Yes", re.IGNORECASE)

# Configuration schema
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_PORT): cv.port,
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
    }
)


def get_scanner(hass, config):
    """Validate configuration and return FGW scanner."""
    scanner = FGWDeviceScanner(config[DOMAIN])
    return scanner if scanner.success_init else None


class FGWDeviceScanner(DeviceScanner):
    """Scanner for devices connected to an Altice / MEO FiberGateway router."""

    def __init__(self, config):
        """Initialize the scanner."""
        self.host = config[CONF_HOST]
        self.port = config[CONF_PORT]
        self.username = config[CONF_USERNAME]
        self.password = config[CONF_PASSWORD]
        self.interfaces = [0, 1]
        self.last_results = []
        self.success_init = self._test_connection()

    def _test_connection(self):
        """Check router accessibility."""
        return self._fetch_fgw_data() is not None

    def scan_devices(self):
        """Scan and return list of connected device MAC addresses."""
        if self._update_info():
            return self.last_results
        return []

    def get_device_name(self, device):
        """Return the name of a device (not available)."""
        return None

    def _update_info(self):
        """Refresh device list."""
        if not self.success_init:
            _LOGGER.warning("FGW scanner initialization failed.")
            return False

        _LOGGER.debug("Fetching DHCP lease data from FGW router.")
        data = self._fetch_fgw_data()
        if not data:
            _LOGGER.error("Failed to fetch device list from FGW router.")
            return False

        self.last_results = data
        return True

    def _fetch_fgw_data(self):
        """Retrieve and parse connected devices from FGW router."""
        try:
            with closing(telnetlib.Telnet(self.host, self.port, timeout=30)) as telnet:
                telnet.read_until(b"Login: ", timeout=30)
                telnet.write(f"{self.username}\r\n".encode("ascii"))

                telnet.read_until(b"Password: ", timeout=30)
                telnet.write(f"{self.password}\r\n".encode("ascii"))

                telnet.read_until(b"cli> ", timeout=30)

                # Prefer the newer DHCP lease command
                telnet.write(b"lan/dhcp/show\r\n")
                output = telnet.read_until(b"cli> ", timeout=30)
                telnet.write(b"quit\r\n")

        except (EOFError, ConnectionRefusedError) as err:
            _LOGGER.exception("Telnet connection error: %s", err)
            return None
        except Exception as err:
            _LOGGER.exception("Unexpected error communicating with FGW router: %s", err)
            return None

        decoded = output.decode("utf-8", errors="ignore")

        # Try to parse DHCP lease table first
        devices = []
        for match in _DHCP_REGEX.finditer(decoded):
            mac = match.group("mac").upper()
            active = match.group("active").lower() == "true"
            port = match.group("port")
            if active:
                devices.append(mac)
                _LOGGER.debug("Found active device %s on port %s", mac, port)

        if devices:
            _LOGGER.info("Discovered %d active devices (including extender connections).", len(devices))
            return devices

        # Fallback: use legacy wireless commands if DHCP parsing failed
        _LOGGER.warning("DHCP table empty, falling back to wireless station info.")
        all_lines = []
        try:
            with closing(telnetlib.Telnet(self.host, self.port, timeout=30)) as telnet:
                telnet.read_until(b"Login: ", timeout=30)
                telnet.write(f"{self.username}\r\n".encode("ascii"))
                telnet.read_until(b"Password: ", timeout=30)
                telnet.write(f"{self.password}\r\n".encode("ascii"))
                telnet.read_until(b"cli> ", timeout=30)

                for idx in self.interfaces:
                    cmd = f"wireless/show-stationinfo --wifi-index={idx}\r\n"
                    telnet.write(cmd.encode("ascii"))
                    all_lines.extend(telnet.read_until(b"cli> ", timeout=30).split(b"\r\n"))
                telnet.write(b"quit\r\n")

        except Exception as err:
            _LOGGER.error("Fallback wireless scan failed: %s", err)
            return None

        for line in all_lines:
            match = _WIFI_REGEX.search(line.decode("utf-8", errors="ignore"))
            if match:
                devices.append(match.group("mac").upper())

        _LOGGER.info("Discovered %d devices via wireless fallback.", len(devices))
        return devices or None
