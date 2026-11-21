import http.client
import socket
import threading
import unittest

import wsgi_helper  # noqa: F401  (scratch app dir)
import web


class ServerTest(unittest.TestCase):
    def serve(self, factory=web.make_web_server):
        srv = factory(0, "127.0.0.1")
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return srv.server_address[1]

    @staticmethod
    def fetch(port, timeout=3):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        c.request("GET", "/about")
        return c.getresponse().status

    def test_idle_connection_does_not_block_other_clients(self):
        port = self.serve()
        idle = socket.create_connection(("127.0.0.1", port))   # a browser pre-connect: connects, sends nothing
        self.addCleanup(idle.close)
        self.assertEqual(self.fetch(port), 200)
        self.assertEqual(self.fetch(port), 200)

    def test_idle_connection_is_dropped_after_the_timeout(self):
        old = web._Handler.timeout
        web._Handler.timeout = 1
        self.addCleanup(setattr, web._Handler, "timeout", old)
        port = self.serve()
        idle = socket.create_connection(("127.0.0.1", port))
        self.addCleanup(idle.close)
        idle.settimeout(4)
        self.assertEqual(idle.recv(1), b"")     # server closed it

    def test_requests_are_served_concurrently(self):
        port = self.serve()
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.fetch(port))) for _ in range(8)]
        [t.start() for t in threads]
        [t.join(10) for t in threads]
        self.assertEqual(results, [200] * 8)


if __name__ == "__main__":
    unittest.main()
