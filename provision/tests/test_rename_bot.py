import asyncio
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
os.environ.setdefault("SMS_DIR", tempfile.mkdtemp())

import meshbot  # noqa: E402
from meshcore import EventType  # noqa: E402


class FakeCommands:
    def __init__(self, reply):
        self.reply, self.sent = reply, []

    async def set_name(self, name):
        self.sent.append(name)
        return types.SimpleNamespace(type=self.reply, payload={"reason": "nope"})


class RenameTest(unittest.TestCase):
    def setUp(self):
        self._mc, self._name, self._wd = meshbot._mc, meshbot._node_name, meshbot._wd
        meshbot._node_name = "old"
        meshbot._wd = types.SimpleNamespace(my_name="old")

    def tearDown(self):
        meshbot._mc, meshbot._node_name, meshbot._wd = self._mc, self._name, self._wd

    def run_rename(self, reply, name):
        cmds = FakeCommands(reply)
        meshbot._mc = types.SimpleNamespace(commands=cmds)
        return asyncio.run(meshbot.set_radio_name(name)), cmds

    def test_success_updates_the_name_used_for_self_detection(self):
        note, cmds = self.run_rename(EventType.OK, "  Hilltop 1 ")
        self.assertEqual(cmds.sent, ["Hilltop 1"])
        self.assertEqual((meshbot._node_name, meshbot._wd.my_name), ("Hilltop 1", "Hilltop 1"))
        self.assertIn("Hilltop 1", note)

    def test_radio_refusal_keeps_the_old_name(self):
        note, _ = self.run_rename(EventType.ERROR, "New")
        self.assertTrue(note.startswith("refused"))
        self.assertEqual((meshbot._node_name, meshbot._wd.my_name), ("old", "old"))

    def test_invalid_names_never_reach_the_radio(self):
        for bad in ("", "x" * 32, "a\nb"):
            note, cmds = self.run_rename(EventType.OK, bad)
            self.assertEqual(cmds.sent, [], repr(bad))
            self.assertEqual(meshbot._node_name, "old")


if __name__ == "__main__":
    unittest.main()
