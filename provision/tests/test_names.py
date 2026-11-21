import json
import os
import sqlite3
import unittest

import wsgi_helper
from wsgi_helper import call

import names


class RulesTest(unittest.TestCase):
    def test_title_limits(self):
        self.assertIsNone(names.check_title(""))
        self.assertIsNone(names.check_title("x" * names.TITLE_MAX))
        self.assertIsNotNone(names.check_title("x" * (names.TITLE_MAX + 1)))
        self.assertIsNotNone(names.check_title("a\x00b"))

    def test_title_precedence_and_fallback(self):
        self.assertEqual(names.resolve_title("Field", "Env"), "Field")
        self.assertEqual(names.resolve_title("", "Env"), "Env")
        self.assertEqual(names.resolve_title(None, None), names.DEFAULT_TITLE)
        self.assertEqual(names.resolve_title("x" * 99, "Env"), "Env")

    def test_radio_name_limits_are_bytes_not_characters(self):
        self.assertIsNone(names.check_radio_name("a" * names.RADIO_NAME_MAX))
        self.assertIsNotNone(names.check_radio_name("a" * (names.RADIO_NAME_MAX + 1)))
        self.assertIsNotNone(names.check_radio_name("é" * 16))
        self.assertIsNotNone(names.check_radio_name("   "))
        self.assertIsNotNone(names.check_radio_name("bad\nname"))


class WebTest(unittest.TestCase):
    cfg_path = os.path.join(wsgi_helper.APP_DIR, "config.json")

    def assertRedirect(self, status, headers, target):
        self.assertTrue(status.startswith("30"), status)
        self.assertTrue(headers["Location"].endswith(target), headers.get("Location"))

    def setUp(self):
        if os.path.exists(self.cfg_path):
            os.remove(self.cfg_path)

    def test_saved_title_shows_in_header_and_form(self):
        status, headers, _ = call("/config", {"title": "Café Node"})
        self.assertRedirect(status, headers, "/config")
        self.assertEqual(json.load(open(self.cfg_path, encoding="utf-8"))["title"], "Café Node")
        for path in ("/", "/config", "/about"):
            self.assertIn("<span class=title>Café Node</span>", call(path)[2], path)
        self.assertIn('value="Café Node"', call("/config")[2])

    def test_blank_title_falls_back_to_default(self):
        call("/config", {"title": ""})
        self.assertIn(f"<span class=title>{names.DEFAULT_TITLE}</span>", call("/")[2])

    def test_title_is_escaped(self):
        call("/config", {"title": "<b>x</b>"})
        html = call("/")[2]
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", html)
        self.assertNotIn("<span class=title><b>", html)

    def test_overlong_title_is_rejected_and_not_saved(self):
        status, _, _ = call("/config", {"title": "x" * 40})
        self.assertTrue(status.startswith("400"), status)
        self.assertFalse(os.path.exists(self.cfg_path))

    def test_rename_queues_a_command_for_the_bot(self):
        status, headers, _ = call("/radio/name", {"name": "Hilltop 1"})
        self.assertRedirect(status, headers, "/config")
        db = sqlite3.connect(os.path.join(wsgi_helper.APP_DIR, "telemetry.sqlite"))
        row = db.execute("SELECT kind, arg FROM cmdq ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(row, ("set_name", "Hilltop 1"))

    def test_bad_rename_is_rejected_without_queuing(self):
        before = self._queued()
        for bad in ("", "  ", "x" * 32):
            self.assertTrue(call("/radio/name", {"name": bad})[0].startswith("400"), repr(bad))
        self.assertEqual(self._queued(), before)

    @staticmethod
    def _queued():
        db = sqlite3.connect(os.path.join(wsgi_helper.APP_DIR, "telemetry.sqlite"))
        try:
            return db.execute("SELECT COUNT(*) FROM cmdq").fetchone()[0]
        except sqlite3.OperationalError:
            return 0


if __name__ == "__main__":
    unittest.main()
