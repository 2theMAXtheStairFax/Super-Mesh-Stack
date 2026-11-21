import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
os.environ["SMS_DIR"] = tempfile.mkdtemp()

import about  # noqa: E402
import web  # noqa: E402


def get(path):
    out = {}
    env = {"REQUEST_METHOD": "GET", "PATH_INFO": path, "SERVER_NAME": "t", "SERVER_PORT": "80",
           "wsgi.input": io.BytesIO(), "wsgi.errors": sys.stderr, "wsgi.url_scheme": "http",
           "wsgi.version": (1, 0), "wsgi.multithread": False, "wsgi.multiprocess": False,
           "wsgi.run_once": True}
    body = b"".join(web.app(env, lambda status, headers, exc=None: out.update(status=status)))
    return out["status"], body.decode()


class AboutTest(unittest.TestCase):
    def test_page_lists_version_and_every_credit(self):
        status, html = get("/about")
        self.assertTrue(status.startswith("200"))
        self.assertIn(about.VERSION, html)
        for name, _, _, url in about.CREDITS:
            self.assertIn(name.replace("&", "&amp;"), html)
            self.assertIn(url, html)

    def test_tab_is_in_the_nav(self):
        self.assertIn('href="/about"', get("/about")[1])

    def test_credit_entries_are_well_formed(self):
        for name, role, lic, url in about.CREDITS:
            self.assertTrue(name and role, name)
            self.assertTrue(url.startswith("https://"), name)

    def test_external_links_do_not_leak_the_opener(self):
        html = get("/about")[1]
        self.assertEqual(html.count("target=_blank"), html.count("noopener noreferrer"))


if __name__ == "__main__":
    unittest.main()
