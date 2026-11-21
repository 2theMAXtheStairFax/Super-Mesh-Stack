#!/usr/bin/env python3
"""
Radio multiplexer for the T114 companion link.

Owns the serial port so meshbot (loopback) and the MeshCore app (TCP, tailnet
only) can share the radio. The companion protocol is one command -> one reply
(GET_CONTACTS streams several), so commands are serialized and each reply goes
back to whoever asked. Push frames (>= 0x80) go to everyone. The radio's
offline message queue pops on read, so messages are fanned out to every client
instead of racing for them.
"""
import asyncio
import ipaddress
import os
from collections import deque

CMD_GET_CONTACTS = 0x04
CMD_SYNC_NEXT = 0x0A
RESP_ERR = 0x01
RESP_END_CONTACTS = 0x04
CMD_TELEMETRY_REQ = 0x27   # without a contact key it is answered by a push only
PUSH_TELEMETRY = 0x8B
MSG_FRAMES = frozenset((0x07, 0x08, 0x10, 0x11, 0x1B))
PUSH_MIN = 0x80
TO_RADIO, FROM_RADIO = b"\x3c", b"\x3e"
MAX_FRAME = 512
IDLE_TIMEOUT = 6.0        # per command, restarted by every frame of a streamed reply
CLIENT_BACKLOG = 262144   # unsent bytes before a stuck client is dropped
QUEUE_LIMIT = 64
MSG_BACKLOG = 200
ready = asyncio.Event()   # set while the radio is open and the local port is listening
ALLOWED = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))


def log(msg):
    print(f"[mux] {msg}", flush=True)


def frame(marker, payload):
    return marker + len(payload).to_bytes(2, "little") + payload


class FrameParser:
    def __init__(self, marker):
        self.marker = marker
        self.buf = bytearray()

    def feed(self, data):
        self.buf += data
        out = []
        while True:
            i = self.buf.find(self.marker)
            if i < 0:
                self.buf.clear()
                break
            del self.buf[:i]
            if len(self.buf) < 3:
                break
            n = int.from_bytes(self.buf[1:3], "little")
            if not 0 < n <= MAX_FRAME:
                del self.buf[:1]
                continue
            if len(self.buf) < 3 + n:
                break
            out.append(bytes(self.buf[3:3 + n]))
            del self.buf[:3 + n]
        return out


def peer_allowed(host, extra=()):
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or any(ip in n for n in ALLOWED + tuple(extra))


class Client:
    def __init__(self, writer, label):
        self.writer = writer
        self.label = label
        self.msgs = deque(maxlen=MSG_BACKLOG)
        self.closed = False

    def send(self, payload):
        if self.closed:
            return
        t = self.writer.transport
        if t is None or t.is_closing() or t.get_write_buffer_size() > CLIENT_BACKLOG:
            self.close()
            return
        self.writer.write(frame(FROM_RADIO, payload))

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.writer.close()
            except OSError:
                pass


class _Inflight:
    def __init__(self, client, cmd):
        self.client = client      # None: the mux's own query, frames kept in .frames
        self.cmd = cmd
        self.until_push = None    # completes on this push code instead of a direct reply
        self.frames = []
        self.complete = False
        self.wake = asyncio.Event()


class RadioMux:
    def __init__(self, radio_reader, radio_writer, idle_timeout=IDLE_TIMEOUT):
        self.r = radio_reader
        self.w = radio_writer
        self.idle = idle_timeout
        self.clients = set()
        self.cmds = asyncio.Queue()
        self.inflight = None

    async def run(self):
        """Pump the radio until it drops, then close every client."""
        disp = asyncio.create_task(self._dispatch())
        parser = FrameParser(FROM_RADIO)
        try:
            while True:
                data = await self.r.read(4096)
                if not data:
                    break
                for p in parser.feed(data):
                    self._on_radio_frame(p)
        finally:
            disp.cancel()
            for c in list(self.clients):
                c.close()

    def _on_radio_frame(self, p):
        code = p[0]
        if code >= PUSH_MIN:
            for c in list(self.clients):
                c.send(p)
            fl = self.inflight
            if fl and code == fl.until_push:
                fl.complete = True
                fl.wake.set()
            return
        fl = self.inflight
        if fl is None:
            if code in MSG_FRAMES:
                self._fan_out(p)
            return
        if fl.client is None:
            fl.frames.append(p)
        else:
            fl.client.send(p)
        if fl.cmd != CMD_GET_CONTACTS or code in (RESP_END_CONTACTS, RESP_ERR):
            fl.complete = True
        fl.wake.set()

    def _fan_out(self, p):
        for c in self.clients:
            c.msgs.append(p)

    async def _exchange(self, payload, client):
        fl = _Inflight(client, payload[0])
        if payload[0] == CMD_TELEMETRY_REQ and len(payload) == 4:
            fl.until_push = PUSH_TELEMETRY
        self.inflight = fl
        self.w.write(frame(TO_RADIO, payload))
        try:
            while not fl.complete:
                fl.wake.clear()
                try:
                    await asyncio.wait_for(fl.wake.wait(), self.idle)
                except asyncio.TimeoutError:
                    log(f"no reply to cmd 0x{payload[0]:02x} in {self.idle:g}s")
                    break
        finally:
            self.inflight = None
        return fl

    async def _sync(self, client):
        if not client.msgs:
            fl = await self._exchange(bytes([CMD_SYNC_NEXT]), None)
            if fl.frames and fl.frames[0][0] in MSG_FRAMES:
                self._fan_out(fl.frames[0])
            elif fl.frames:
                client.send(fl.frames[0])
                return
        if client.msgs:
            client.send(client.msgs.popleft())

    async def _dispatch(self):
        while True:
            client, payload = await self.cmds.get()
            if client.closed:
                continue
            if payload == bytes([CMD_SYNC_NEXT]):
                await self._sync(client)
            else:
                await self._exchange(payload, client)

    async def serve_client(self, reader, writer, label):
        c = Client(writer, label)
        self.clients.add(c)
        log(f"client up: {label} ({len(self.clients)} connected)")
        parser = FrameParser(TO_RADIO)
        try:
            while not c.closed:
                data = await reader.read(4096)
                if not data:
                    break
                for p in parser.feed(data):
                    if self.cmds.qsize() >= QUEUE_LIMIT:
                        log(f"dropping {label}: command backlog")
                        return
                    self.cmds.put_nowait((c, p))
        except (ConnectionError, OSError):
            pass
        finally:
            self.clients.discard(c)
            c.close()
            log(f"client down: {label} ({len(self.clients)} connected)")


def _label(writer):
    peer = writer.get_extra_info("peername")
    return f"{peer[0]}:{peer[1]}" if peer else "?"


async def open_serial(path, baud):
    import serial_asyncio_fast as sa
    r, w = await sa.open_serial_connection(url=path, baudrate=baud)
    try:
        w.transport.serial.rts = False
    except AttributeError:
        pass
    return r, w


async def supervise(path, baud, local_port, remote_port, extra_allow=(), retry=5.0):
    """Run forever: open the radio, serve clients, reopen when the link drops."""
    extra = tuple(ipaddress.ip_network(n) for n in extra_allow)
    while True:
        if not os.path.exists(path):
            await asyncio.sleep(retry)
            continue
        servers = []
        try:
            r, w = await open_serial(path, baud)
            mux = RadioMux(r, w)

            async def remote(rd, wr):
                peer = wr.get_extra_info("peername")
                if not peer or not peer_allowed(peer[0], extra):
                    log(f"refused {peer}")
                    wr.close()
                    return
                await mux.serve_client(rd, wr, _label(wr))

            async def local(rd, wr):
                await mux.serve_client(rd, wr, "local")

            servers.append(await asyncio.start_server(local, "127.0.0.1", local_port))
            if remote_port:
                servers.append(await asyncio.start_server(remote, "0.0.0.0", remote_port))
            log(f"radio {path} up; local :{local_port}, app :{remote_port or 'off'}")
            ready.set()
            await mux.run()
            log("radio link dropped")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log(f"{type(e).__name__}: {e}")
        finally:
            ready.clear()
            for s in servers:
                s.close()
            try:
                w.close()
            except (NameError, OSError):
                pass
        await asyncio.sleep(retry)
