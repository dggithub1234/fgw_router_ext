"""Raw Telnet connection client for Altice / MEO FiberGateway."""

import asyncio
import logging
import re

_LOGGER = logging.getLogger(__name__)

_DHCP_REGEX = re.compile(
    r"(?P<mac>([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}).*?\|\s*(?P<port>[a-zA-Z0-9\.\-_ ]+?)\s*\|\s*(?P<active>true|false|active|yes|1)",
    re.IGNORECASE,
)
_WIFI_REGEX = re.compile(r"(?P<mac>([0-9A-F]{2}[:-]){5}[0-9A-F]{2})\s*\|\s*(Yes|Active|1)", re.IGNORECASE)


async def _read_until(reader, expect_bytes, timeout=30):
    """Helper to strictly read from stream until expected sequence is hit (case-insensitive)."""
    buffer = bytearray()
    expect_lower = expect_bytes.lower()
    
    while True:
        if expect_lower in buffer.lower():
            break
            
        try:
            chunk = await asyncio.wait_for(reader.read(1024), timeout=timeout)
            if not chunk:
                break
            buffer.extend(chunk)
        except asyncio.TimeoutError:
            _LOGGER.error(
                "Telnet stream timed out waiting for sequence: %s. Current Buffer: %s",
                expect_bytes,
                buffer.decode("utf-8", errors="ignore")
            )
            raise
    return bytes(buffer)


async def fetch_fgw_data(host, port, username, password) -> set[str]:
    """Retrieve and parse connected devices from FGW router asynchronously."""
    devices = set()
    
    try:
        connect = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(connect, timeout=15)
    except Exception as err:
        _LOGGER.error("Unable to open TCP connection to FiberGateway at %s:%s - %s", host, port, err)
        return devices

    try:
        # Step 1: Wait for login prompt
        await _read_until(reader, b"login:")
        
        # Step 2: Write username and wait for password prompt
        writer.write(f"{username}\r\n".encode("ascii"))
        await writer.drain()
        await _read_until(reader, b"password:")
        
        # Step 3: Write password and wait for cli prompt
        writer.write(f"{password}\r\n".encode("ascii"))
        await writer.drain()
        await _read_until(reader, b"cli> ")

        # Step 4: Write command to retrieve leases
        writer.write(b"lan/dhcp/show\r\n")
        await writer.drain()
        output = await _read_until(reader, b"cli> ")
        
        # Step 5: Quit gracefully
        writer.write(b"quit\r\n")
        await writer.drain()
        
    except Exception as err:
        _LOGGER.exception("Telnet execution broke during router conversation exchange: %s", err)
        return devices
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

    decoded = output.decode("utf-8", errors="ignore")
    _LOGGER.debug("Parsed Data Table Response: %s", decoded)

    for match in _DHCP_REGEX.finditer(decoded):
        mac = match.group("mac").upper()
        active_val = match.group("active").lower()
        if active_val in ("true", "active", "yes", "1"):
            devices.add(mac)

    if devices:
        return devices

    _LOGGER.warning("DHCP table empty, dropping into legacy wifi station check.")
    all_lines = []
    try:
        connect = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(connect, timeout=15)
        
        await _read_until(reader, b"login:")
        writer.write(f"{username}\r\n".encode("ascii"))
        await writer.drain()
        
        await _read_until(reader, b"password:")
        writer.write(f"{password}\r\n".encode("ascii"))
        await writer.drain()
        await _read_until(reader, b"cli> ")

        # SYNTAX ERROR REMOVED: Loop interfaces fixed to standard [0, 1]
        for idx in:
            cmd = f"wireless/show-stationinfo --wifi-index={idx}\r\n"
            writer.write(cmd.encode("ascii"))
            await writer.drain()
            out = await _read_until(reader, b"cli> ")
            all_lines.extend(out.split(b"\r\n"))
            
        writer.write(b"quit\r\n")
        await writer.drain()
    except Exception as err:
        _LOGGER.error("Fallback wireless communication failed: %s", err)
        return devices
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

    for line in all_lines:
        match = _WIFI_REGEX.search(line.decode("utf-8", errors="ignore"))
        if match:
            devices.add(match.group("mac").upper())

    return devices
