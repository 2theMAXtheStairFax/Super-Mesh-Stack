"""Radio multiplexer tests against a fake companion radio. Run: python -m unittest provision/tests/test_mux.py"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import mux  # noqa: E402


class FakeRadio:
    """Replies like the companion firmware: one frame per command, GET_CONTACTS streams."""

    def __init__(self):
        self.queue = []
        self.seen = []
        self.writers = []
        self.contact_gap = 0.0

    async def handle(self, reader, writer):
        self.writers.append(writer)
        parser = mux.FrameParser(mux.TO_RADIO)
        while True:
            data = await reader.read(4096)
            if not data:
                return
            for p in parser.feed(data):
                self.seen.append(p)
                await self.reply(writer, p)

    def out(self, writer, payload):
        writer.write(mux.frame(mux.FROM_RADIO, payload))

    async def reply(self, writer, p):
        cmd = p[0]
        if cmd == 0x01:
            self.out(writer, b"\x05info")
        elif cmd == 0x04:
            self.out(writer, b"\x02\x03\x00\x00\x00")
            for i in range(3):
                await asyncio.sleep(self.contact_gap)
                self.out(writer, bytes([0x03, i]))
            self.out(writer, b"\x04\x00\x00\x00\x00")
        elif cmd == 0x0A:
            self.out(writer, self.queue.pop(0) if self.queue else b"\x0a")
        elif cmd == 0x20:
            self.out(writer, b"\x00" + p[1:])
        elif cmd == 0x27 and len(p) == 4:
            self.out(writer, b"\x8b\x00tele")

    def push(self, payload):
        for w in self.writers:
            self.out(w, payload)


class Harness:
    async def __aenter__(self):
        self.radio = FakeRadio()
        self.radio_srv = await asyncio.start_server(self.radio.handle, "127.0.0.1", 0)
        rport = self.radio_srv.sockets[0].getsockname()[1]
        r, w = await asyncio.open_connection("127.0.0.1", rport)
        self.mux = mux.RadioMux(r, w, idle_timeout=0.4)
        self.run = asyncio.create_task(self.mux.run())
        self.srv = await asyncio.start_server(
            lambda rd, wr: self.mux.serve_client(rd, wr, "t"), "127.0.0.1", 0)
        self.port = self.srv.sockets[0].getsockname()[1]
        self.conns = []
        return self

    async def __aexit__(self, *exc):
        for _, w, _ in self.conns:
            w.close()
        self.run.cancel()
        self.srv.close()
        self.radio_srv.close()

    async def client(self):
        r, w = await asyncio.open_connection("127.0.0.1", self.port)
        c = (r, w, mux.FrameParser(mux.FROM_RADIO))
        self.conns.append(c)
        while len(self.mux.clients) < len(self.conns):
            await asyncio.sleep(0.01)
        return c

    @staticmethod
    def send(c, payload, chunk=None):
        raw = mux.frame(mux.TO_RADIO, payload)
        if chunk:
            async def dribble():
                for i in range(0, len(raw), chunk):
                    c[1].write(raw[i:i + chunk])
                    await asyncio.sleep(0.005)
            return asyncio.create_task(dribble())
        c[1].write(raw)

    @staticmethod
    async def frames(c, n, timeout=2.0):
        out = []
        async def go():
            while len(out) < n:
                out.extend(c[2].feed(await c[0].read(4096)))
        await asyncio.wait_for(go(), timeout)
        return out


def run(coro):
    return asyncio.run(coro)


class MuxTests(unittest.TestCase):
    def test_replies_go_to_the_asker(self):
        async def t():
            async with Harness() as h:
                a, b = await h.client(), await h.client()
                h.send(a, b"\x20A")
                h.send(b, b"\x20B")
                self.assertEqual(await h.frames(a, 1), [b"\x00A"])
                self.assertEqual(await h.frames(b, 1), [b"\x00B"])
        run(t())

    def test_streamed_contacts_stay_with_requester_and_serialize(self):
        async def t():
            async with Harness() as h:
                h.radio.contact_gap = 0.02
                a, b = await h.client(), await h.client()
                h.send(a, b"\x04")
                h.send(b, b"\x01")
                got_a = await h.frames(a, 5)
                self.assertEqual([f[0] for f in got_a], [2, 3, 3, 3, 4])
                self.assertEqual(await h.frames(b, 1), [b"\x05info"])
                self.assertEqual([p[0] for p in h.radio.seen], [4, 1])
        run(t())

    def test_push_frames_reach_everyone(self):
        async def t():
            async with Harness() as h:
                a, b = await h.client(), await h.client()
                h.radio.push(b"\x83")
                self.assertEqual(await h.frames(a, 1), [b"\x83"])
                self.assertEqual(await h.frames(b, 1), [b"\x83"])
        run(t())

    def test_message_reaches_both_clients_once_each(self):
        async def t():
            async with Harness() as h:
                a, b = await h.client(), await h.client()
                h.radio.queue.append(b"\x08hello")
                h.send(a, b"\x0a")
                self.assertEqual(await h.frames(a, 1), [b"\x08hello"])
                h.send(b, b"\x0a")
                self.assertEqual(await h.frames(b, 1), [b"\x08hello"])
                h.send(a, b"\x0a")
                self.assertEqual(await h.frames(a, 1), [b"\x0a"])
                h.send(b, b"\x0a")
                self.assertEqual(await h.frames(b, 1), [b"\x0a"])
                self.assertEqual(len(h.radio.seen), 3)
        run(t())

    def test_unanswered_command_does_not_wedge_the_queue(self):
        async def t():
            async with Harness() as h:
                a = await h.client()
                h.send(a, b"\x7f")
                h.send(a, b"\x20Z")
                self.assertEqual(await h.frames(a, 1), [b"\x00Z"])
        run(t())

    def test_self_telemetry_completes_on_its_push(self):
        async def t():
            async with Harness() as h:
                a, b = await h.client(), await h.client()
                t0 = asyncio.get_running_loop().time()
                h.send(a, b"\x27\x00\x00\x00")
                h.send(b, b"\x20Q")
                self.assertEqual(await h.frames(b, 2), [b"\x8b\x00tele", b"\x00Q"])
                self.assertLess(asyncio.get_running_loop().time() - t0, 0.3)
        run(t())

    def test_fragmented_client_frames(self):
        async def t():
            async with Harness() as h:
                a = await h.client()
                await h.send(a, b"\x20split", chunk=1)
                self.assertEqual(await h.frames(a, 1), [b"\x00split"])
        run(t())

    def test_radio_drop_closes_clients(self):
        async def t():
            async with Harness() as h:
                a = await h.client()
                for w in h.radio.writers:
                    w.close()
                await asyncio.wait_for(h.run, 2)
                self.assertEqual(await asyncio.wait_for(a[0].read(10), 2), b"")
        run(t())

    def test_peer_allowlist(self):
        self.assertTrue(mux.peer_allowed("127.0.0.1"))
        self.assertTrue(mux.peer_allowed("100.64.1.2"))
        self.assertTrue(mux.peer_allowed("fd7a:115c:a1e0::1"))
        self.assertFalse(mux.peer_allowed("192.168.50.20"))
        self.assertFalse(mux.peer_allowed("8.8.8.8"))
        self.assertFalse(mux.peer_allowed("not-an-ip"))
        import ipaddress
        self.assertTrue(mux.peer_allowed("192.168.50.20", (ipaddress.ip_network("192.168.50.0/24"),)))


if __name__ == "__main__":
    unittest.main()
