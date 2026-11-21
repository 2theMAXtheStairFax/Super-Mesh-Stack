import json
import os
import random
import re
import unittest

import wsgi_helper
from wsgi_helper import call

import colors


class ColorMathTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(colors.parse(" #AbCdEf "), "#abcdef")
        for bad in ("", None, "red", "#abc", "#12345g", "#1234567", "123456"):
            self.assertIsNone(colors.parse(bad), bad)

    def test_resolve_prefers_config_then_env(self):
        self.assertEqual(colors.resolve("#111111", "#222222"), "#111111")
        self.assertEqual(colors.resolve("junk", "#222222"), "#222222")
        self.assertIsNone(colors.resolve("", ""))

    def test_text_on_a_color_is_readable(self):
        rng = random.Random(7)
        for _ in range(500):
            rgb = tuple(rng.randrange(256) for _ in range(3))
            self.assertGreaterEqual(colors.contrast(rgb, colors.on_rgb(rgb)), 4.5, rgb)
        self.assertEqual(colors.on_rgb((255, 204, 0)), (0, 0, 0))
        self.assertEqual(colors.on_rgb((200, 20, 20)), (255, 255, 255))

    def test_ink_always_reads_on_the_page_background(self):
        rng = random.Random(11)
        for _ in range(300):
            h = colors.to_hex(tuple(rng.randrange(256) for _ in range(3)))
            for bg in (colors.DARK_BG, colors.LIGHT_BG):
                ink = colors.readable(h, bg)
                self.assertGreaterEqual(colors.contrast(colors.to_rgb(ink), colors.to_rgb(bg)), 4.5, (h, bg))

    def test_readable_leaves_good_colors_alone(self):
        self.assertEqual(colors.readable("#ffcc00", colors.DARK_BG), "#ffcc00")

    def test_default_theme_is_empty(self):
        self.assertEqual(colors.theme_css(None, None), "")


class WebColorTest(unittest.TestCase):
    cfg_path = os.path.join(wsgi_helper.APP_DIR, "config.json")

    def setUp(self):
        if os.path.exists(self.cfg_path):
            os.remove(self.cfg_path)

    def saved(self):
        return json.load(open(self.cfg_path))

    def test_picked_colors_persist_and_theme_every_page(self):
        status, _, _ = call("/config", {"color_main": "#3366CC", "color_accent": "#00ffaa"})
        self.assertTrue(status.startswith("30"), status)
        self.assertEqual((self.saved()["color_main"], self.saved()["color_accent"]), ("#3366cc", "#00ffaa"))
        for path in ("/", "/about", "/config"):
            html = call(path)[2]
            self.assertIn("--red:#3366cc", html, path)
            self.assertIn("--yellow:#00ffaa", html, path)
        self.assertIn('name=color_main value="#3366cc"', call("/config")[2])

    def test_pale_main_color_gets_dark_button_text(self):
        call("/config", {"color_main": "#ffee00"})
        self.assertIn("--on-red:#000000", call("/")[2])
        call("/config", {"color_main": "#102030"})
        self.assertIn("--on-red:#ffffff", call("/")[2])

    def test_dark_pick_stays_readable_as_link_text(self):
        call("/config", {"color_main": "#000033"})
        html = call("/")[2]
        dark_ink = re.search(r"--red-ink:(#[0-9a-f]{6})", html).group(1)
        self.assertGreaterEqual(colors.contrast(colors.to_rgb(dark_ink), colors.to_rgb(colors.DARK_BG)), 4.5)

    def test_defaults_are_not_stored_so_the_builtin_look_stays(self):
        call("/config", {"color_main": colors.DEFAULT_MAIN, "color_accent": colors.DEFAULT_ACCENT})
        self.assertEqual((self.saved()["color_main"], self.saved()["color_accent"]), ("", ""))
        self.assertEqual(call("/")[2].count(":root{--red:"), 1)    # only the built-in theme, no override block

    def test_reset_button_clears_custom_colors(self):
        call("/config", {"color_main": "#3366cc", "color_accent": "#00ffaa"})
        call("/config", {"color_main": "#3366cc", "color_accent": "#00ffaa", "reset_colors": "1"})
        self.assertEqual((self.saved()["color_main"], self.saved()["color_accent"]), ("", ""))
        self.assertNotIn("--red:#3366cc", call("/")[2])

    def test_invalid_color_is_rejected_and_nothing_saved(self):
        for bad in ("red", "#12345", "javascript:x", "#12345g"):
            status = call("/config", {"color_main": bad})[0]
            self.assertTrue(status.startswith("400"), bad)
        self.assertFalse(os.path.exists(self.cfg_path))

    def test_color_cannot_inject_css(self):
        status = call("/config", {"color_main": "#ff0000;}body{display:none"})[0]
        self.assertTrue(status.startswith("400"), status)
        self.assertFalse(os.path.exists(self.cfg_path))
        self.assertNotIn("body{display:none", call("/")[2])


if __name__ == "__main__":
    unittest.main()
