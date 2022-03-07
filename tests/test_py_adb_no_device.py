# encoding=utf-8
import unittest

import sys
sys.path.append('..')

from airtest.core.android.py_adb import ADB
from airtest.core.error import AdbError, DeviceConnectionError


class TestADBWithoutDevice(unittest.TestCase):
    adb = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.adb = ADB()
        cls.adb.start_server()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.adb.disconnect()
        cls.adb.kill_server()

    def test_version(self):
        self.assertIsInstance(self.adb.version(), int)

    def test_cmd(self):
        with self.assertRaises(AdbError):
            self.adb.run_cmd("wtf")

    def test_devices(self):
        all_devices = self.adb.devices()
        self.assertIsInstance(all_devices, list)

    def test_wait_for_device(self):
        with self.assertRaises(DeviceConnectionError):
            self.adb.wait_for_device(timeout=2)


if __name__ == '__main__':
    unittest.main()
