"""Raw Telnet connection client for Altice / MEO FiberGateway with auto-retry logic."""

import asyncio
import logging
import re

_LOGGER = logging.getLogger(__name__)

# Pattern configured to match MAC and Active state ignoring IP and Expiration columns
_DHCP_REGEX = re.compile(
    r"\|\s*[^\|]*\s*\|\s*(?P<mac>([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2})\s*\|\s*[^\|]+\s*\|\s*[^\|]+\s*\|\s*(?P<port>[a-z0-9\._\-]+)\s*\|\s*(?P<active>TRUE|FALSE|true|false)",
)


async def _read_until(reader, expect_bytes, timeout=15):
    """Helper to strictly read from stream until expected sequence is hit (case-insensitive)."""
    buffer = bytearray()
    expect_lower = expect_bytes.lower()
    
    while True:
        if expect_lower in buffer.lower():
            break
            
        try:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=timeout)
            if not chunk:
                _LOGGER.debug("Telnet stream closed prematurely by remote host.")
                break
            buffer.extend(chunk)
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "Telnet timeout reached while waiting for %s. Raw buffer contents so far:\n%s",
                expect_bytes,
                buffer.decode("utf-8", errors="ignore")
            )
            break
    return bytes(buffer)


async def _execute_dhcp_fetch(host, port, username, password) -> bytes | None:
    """Helper representing a single conversation sequence to fetch DHCP leases."""
    connect = asyncio.open_connection(host, port)
    reader, writer = await asyncio.wait_for(connect, timeout=15)

    try:
        # Step 1: Wait for login prompt
        await _read_until(reader, b"login:")
        await asyncio.sleep(0.2)  # Pacing pause
        
        # Step 2: Write username and wait for password prompt
        writer.write(f"{username}\r\n".encode("ascii"))
        await writer.drain()
        await _read_until(reader, b"password:")
        await asyncio.sleep(0.2)  # Pacing pause
        
        # Step 3: Write password and wait for cli prompt
        writer.write(f"{password}\r\n".encode("ascii"))
        await writer.drain()
        await _read_until(reader, b"cli> ")
        await asyncio.sleep(0.2)

        # Explicitly disable terminal paging for this session
        writer.write(b"system/terminal/pagesize --size=0\r\n")
        await writer.drain()
        await _read_until(reader, b"cli> ")
        await asyncio.sleep(0.2)
        
        # Step 4: Write command to retrieve leases
        writer.write(b"lan/dhcp/show\r\n")
        await writer.drain()
        output = await _read_until(reader, b"/cli> ")
        await asyncio.sleep(0.2)
        
        # Step 5: Quit gracefully
        writer.write(b"quit\r\n")
        await writer.drain()
        await asyncio.sleep(0.4)  # Allow the router time to process session exit
        return output
        
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def fetch_fgw_data(host, port, username, password) -> set[str]:
    """Retrieve and parse connected devices from FGW router with fallback retry loop."""
    devices = set()
    output = None
    max_attempts = 3

    # Primary Try Loop for DHCP Table
    for attempt in range(1, max_attempts + 1):
        try:
            _LOGGER.debug("Fetching FGW DHCP leases - Attempt %d of %d", attempt, max_attempts)
            output = await _execute_dhcp_fetch(host, port, username, password)
            if output:
                break
        except Exception as err:
            _LOGGER.debug("Telnet session tracking dropped on attempt %d: %s", attempt, err)
            if attempt < max_attempts:
                # Cool down to let the router reset its internal connection state table
                await asyncio.sleep(1.5)
            else:
                _LOGGER.warning("All %d DHCP fetch attempts failed due to connection drops.", max_attempts)

    if output:
        decoded = output.decode("utf-8", errors="ignore")
        _LOGGER.debug("Parsed Data Table Response: %s", decoded)

        for match in _DHCP_REGEX.finditer(decoded):
            mac = match.group("mac").upper()
            active_val = match.group("active").lower()
            if active_val == "true":
                devices.add(mac)

    return devices
