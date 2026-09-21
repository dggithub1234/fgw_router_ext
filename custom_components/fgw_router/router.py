"""Raw Telnet connection client for Altice / MEO FiberGateway."""

import asyncio
import logging
import re

_LOGGER = logging.getLogger(__name__)
# Updated to gracefully bypass the IP and expiration columns used by newer FGW firmwares

_DHCP_REGEX = re.compile(
    r"\|\s*[^\|]*\s*\|\s*(?P<mac>([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2})\s*\|\s*[^\|]+\s*\|\s*[^\|]+\s*\|\s*(?P<port>[a-z0-9\._\-]+)\s*\|\s*(?P<active>TRUE|FALSE|true|false)",
)

#_DHCP_REGEX = re.compile(
#    r"\|\s*(?P<mac>([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2})\s*\|[^\|]+\|[^\|]+\|\s*(?P<port>[a-z0-9\._\-]+)\s*\|\s*(?P<active>true|false)",
#    re.IGNORECASE,
#)

_WIFI_REGEX = re.compile(r"(?P<mac>([0-9A-F]{2}[:-]){5}[0-9A-F]{2})\s*\|\s*Yes", re.IGNORECASE)

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
            # CRITICAL DEBUGGING LINE:
            _LOGGER.warning(
                "Telnet timeout reached while waiting for %s. Raw buffer contents so far:\n%s",
                expect_bytes,
                buffer.decode("utf-8", errors="ignore")
            )
            break
    return bytes(buffer)

async def fetch_fgw_data(host, port, username, password) -> set[str]:
    """Retrieve and parse connected devices from FGW router reliably using chained commands."""
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
        
        # Step 3: Write password and wait for the initial CLI prompt
        writer.write(f"{password}\r\n".encode("ascii"))
        await writer.drain()
        await _read_until(reader, b"cli> ")

        # FIX: Chain terminal settings and the DHCP query together using semicolon separation
        # This executes both actions sequentially inside the router shell using ONE single write/drain payload.
        chained_command = b"system/terminal/pagesize --size=0; lan/dhcp/show\r\n"
        writer.write(chained_command)
        await writer.drain()
        
        # Step 4: Capture everything until the CLI command prompt finishes executing
        output = await _read_until(reader, b"/cli> ")
        
        # Step 5: Quit cleanly
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
        if active_val == "true":
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

        # Apply the same command chaining fix to the legacy wireless station loop
        # Runs index 0 and index 1 back-to-back without breaking the stream protocol
        wifi_command = b"wireless/show-stationinfo --wifi-index=0; wireless/show-stationinfo --wifi-index=1\r\n"
        writer.write(wifi_command)
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
