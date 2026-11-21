#!/usr/bin/env python3
"""
Mesh Stack - passive TCP<->serial bridge for the radio.

Lets the official MeshCore app (or meshcore-cli -t <host> -p <port>) connect
to the T114 over the network instead of USB/BLE, when the radio is on the Pi
and not physically plugged into your phone/PC.

This does NOT speak the companion protocol - it just forwards raw bytes both
ways between a TCP socket and the serial device, exactly like the wire would.
No contacts/channels ownership, no state - unlike RemoteTerm-style tools,
it's a dumb pipe, so it doesn't care which radio is plugged in.

Exclusive with sms-meshbot: both want the same serial port. This service is
disabled by default - stop sms-meshbot before starting this, and vice versa:

    sudo systemctl stop sms-meshbot  && sudo systemctl start sms-bridge
    sudo systemctl stop sms-bridge   && sudo systemctl start sms-meshbot

Then in the app: connect via Network/TCP to <this Pi's address> : 5000.
"""
import asyncio
import os

SERIAL = os.environ.get("SMS_SERIAL", "/dev/serial0")   # P1-header UART; /dev/ttyACM0 on USB
BAUD = int(os.environ.get("SMS_BAUD", "115200"))
PORT = int(os.environ.get("SMS_BRIDGE_PORT", "5000"))
RETRY_SEC = 5


async def pump(reader, writer, label):
    try:
        while True:
            data = await reader.read(4096)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass
    finally:
        print(f"[bridge] {label} closed", flush=True)


_client_lock = asyncio.Lock()


async def handle_client(client_reader, client_writer, serial):
    peer = client_writer.get_extra_info("peername")
    if _client_lock.locked():
        print(f"[bridge] rejecting {peer}: one client already connected", flush=True)
        client_writer.close()
        return
    async with _client_lock:
        print(f"[bridge] client connected: {peer}", flush=True)
        ser_reader, ser_writer = serial
        to_serial = asyncio.create_task(pump(client_reader, ser_writer, "tcp->serial"))
        to_client = asyncio.create_task(pump(ser_reader, client_writer, "serial->tcp"))
        await asyncio.wait([to_serial, to_client], return_when=asyncio.FIRST_COMPLETED)
        to_serial.cancel()
        to_client.cancel()
        client_writer.close()
        print(f"[bridge] client disconnected: {peer}", flush=True)


async def open_serial_pair():
    """pyserial-asyncio keeps this project dependency-light; use a thread-backed
    reader/writer pair instead of pulling in another package."""
    import serial  # pyserial, already a meshcore-cli dependency

    ser = serial.Serial(SERIAL, BAUD, timeout=0)
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)

    class SerialWriter:
        def write(self, data):
            ser.write(data)

        async def drain(self):
            await asyncio.sleep(0)

        def close(self):
            pass

    async def poll():
        while True:
            try:
                n = ser.in_waiting
                if n:
                    reader.feed_data(ser.read(n))
                else:
                    await asyncio.sleep(0.02)
            except Exception as e:
                reader.feed_eof()
                print(f"[bridge] serial read error: {e}", flush=True)
                return

    asyncio.ensure_future(poll())
    return reader, SerialWriter(), ser


async def main():
    print(f"[bridge] waiting for {SERIAL}...", flush=True)
    while not os.path.exists(SERIAL):
        await asyncio.sleep(RETRY_SEC)

    ser_reader, ser_writer, ser_handle = await open_serial_pair()
    server = await asyncio.start_server(
        lambda r, w: handle_client(r, w, (ser_reader, ser_writer)),
        "0.0.0.0", PORT,
    )
    print(f"[bridge] serving {SERIAL} on :{PORT} - point the app at this host, port {PORT}",
          flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
