import os
import re
import shutil
import subprocess
import tempfile
import unittest

import wsgi_helper  # noqa: F401  (scratch app dir)
from wsgi_helper import call
import web

M10 = "Heltec_t114_companion_radio_ble_usb-1.18.0-sfm-portrait.uf2"
L76K = "Heltec_t114_companion_radio_ble_usb-1.18.0-sfm-l76k.uf2"
OTHER = ["Heltec_t114_companion_radio_usb-v1.18.0-sfm-batt.uf2", "Heltec_t114_without_display_companion_radio_usb-v1.18.0-sfm-batt.uf2"]
FLASH_SH = os.path.join(os.path.dirname(__file__), "..", "app", "flash.sh")


def make_dir(files):
    """files: {name: age_in_seconds}; smaller age = newer."""
    d = tempfile.mkdtemp()
    for name, age in files.items():
        p = os.path.join(d, name)
        open(p, "wb").close()
        t = 1_700_000_000 - age
        os.utime(p, (t, t))
    return d


class WebSelectTest(unittest.TestCase):
    def use(self, files):
        old = web.FW_DIR
        web.FW_DIR = make_dir(files)
        self.addCleanup(setattr, web, "FW_DIR", old)

    def test_each_variant_gets_its_own_file_even_when_the_other_is_newer(self):
        self.use({M10: 100, L76K: 1, OTHER[0]: 1})
        self.assertEqual(web.bundled_fw("sfm"), M10)
        self.assertEqual(web.bundled_fw("l76k"), L76K)

    def test_newest_of_a_variant_wins(self):
        newer = "Heltec_t114_companion_radio_ble_usb-1.19.0-sfm-l76k.uf2"
        self.use({L76K: 500, newer: 5, M10: 1})
        self.assertEqual(web.bundled_fw("l76k"), newer)

    def test_missing_variant_is_none_not_the_other_one(self):
        self.use({M10: 1})
        self.assertIsNone(web.bundled_fw("l76k"))
        self.use({L76K: 1})
        self.assertIsNone(web.bundled_fw("sfm"))

    def test_flash_page_offers_both_builds(self):
        self.use({M10: 1, L76K: 1})
        html = call("/flash")[2]
        self.assertRegex(html, r"<option value=sfm>Patched build, M10 GPS \(%s\)" % re.escape(M10))
        self.assertRegex(html, r"<option value=l76k>Patched build, L76K GPS \(%s\)" % re.escape(L76K))

    def test_flash_run_accepts_only_known_targets(self):
        started = []
        old = web.subprocess.Popen
        web.subprocess.Popen = lambda cmd, **kw: started.append(cmd)
        self.addCleanup(setattr, web.subprocess, "Popen", old)
        for which in ("sfm", "l76k", "stock"):
            status = call("/flash/run", {"method": "usb", "which": which})[0]
            self.assertTrue(status.startswith("30"), which)
        self.assertEqual([c[-1] for c in started], ["sfm", "l76k", "stock"])
        for bad in ("m10", "../../x", "sfm; reboot"):
            self.assertTrue(call("/flash/run", {"method": "usb", "which": bad})[0].startswith("400"), bad)
        self.assertEqual(len(started), 3)


@unittest.skipUnless(shutil.which("bash"), "needs bash")
class ShellSelectTest(unittest.TestCase):
    func = None

    @classmethod
    def setUpClass(cls):
        src = open(FLASH_SH, encoding="utf-8").read().replace("\r\n", "\n")
        cls.func = re.search(r"^bundled\(\) \{.*?^\}", src, re.S | re.M).group(0)

    def pick(self, files, variant):
        d = make_dir(files).replace("\\", "/")
        script = f'FW_DIR="{d}"\n{self.func}\nbundled {variant}'
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True).stdout.strip()
        return os.path.basename(out)

    def test_sfm_skips_a_newer_l76k_build(self):
        self.assertEqual(self.pick({M10: 100, L76K: 1}, "sfm"), M10)

    def test_l76k_picks_the_l76k_build(self):
        self.assertEqual(self.pick({M10: 1, L76K: 100}, "l76k"), L76K)

    def test_other_environments_are_ignored(self):
        self.assertEqual(self.pick({OTHER[0]: 1, OTHER[1]: 1, M10: 50}, "sfm"), M10)
        self.assertEqual(self.pick({OTHER[0]: 1, M10: 50}, "l76k"), "")

    def test_nothing_bundled_gives_nothing(self):
        self.assertEqual(self.pick({}, "sfm"), "")


if __name__ == "__main__":
    unittest.main()
