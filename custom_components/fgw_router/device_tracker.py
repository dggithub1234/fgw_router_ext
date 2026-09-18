"""Raw Telnet connection client for Altice / MEO FiberGateway."""

import asyncio
import logging
import re

_LOGGER = logging.getLogger(__name__)

# Your highly optimized regex pattern for strict true/false tracking and alphanumeric/dot ports
_DHCP_REGEX = re.compile(
    r"(?P<mac>([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}).*?\|\s*(?P<port>[a-z0-9\.]+)\s*\|\s*(?P<active>true|false)",
    re.IGNORECASE,
)


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
    """Retrieve and parse active connected devices from the FGW DHCP table."""
    devices = set()
    
    try:
        connect = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(connect, timeout=15)
    except Exception as err:
        _LOGGER.error("Unable to open TCP connection to FiberGateway at %s:%s - %s", host, port, err)
        return devices

    try:
        # Step 1: Handle Authentication Exchange
        await _read_until(reader, b"login:")
        writer.write(f"{username}\r\n".encode("ascii"))
        await writer.drain()
        
        await _read_until(reader, b"password:")
        writer.write(f"{password}\r\n".encode("ascii"))
        await writer.drain()
        
        # Wait for the baseline shell prompt to clear
        await _read_until(reader, b"cli> ")

        # Step 2: Query the DHCP Lease Table
        writer.write(b"lan/dhcp/show\r\n")
        await writer.drain()
        
        # Read the full data stream until the router displays the next 'cli> ' prompt completely
        output = await _read_until(reader, b"cli> ")
        
        # Step 3: Close the terminal session cleanly
        writer.write(b"quit\r\n")
        await writer.drain()
        
    except Exception as err:
        _LOGGER.error("Telnet communication failure with FiberGateway: %s", err)
        return devices
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

    # Step 4: Parse Results
    decoded = output.decode("utf-8", errors="ignore")
    _LOGGER.debug("Parsed Data Table Response: %s", decoded)

    for match in _DHCP_REGEX.finditer(decoded):
        mac = match.group("mac").upper()
        active_val = match.group("active").lower()
        if active_val == "true":
            devices.add(mac)

    _LOGGER.debug("DHCP sweep complete. Found %d active devices.", len(devices))
    return devices
