# encoding=utf-8
import unittest

import sys
sys.path.append('..')

from airtest.core.android.py_adb import ADB, AdbError, DeviceConnectionError


class TestADBWithoutDevice(unittest.TestCase):
    adb = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.adb = ADB(auto_connect=False)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.adb.disconnect()

    def test_start_server(self):
        self.adb.start_server()

    def test_stop_server(self):
        self.adb.kill_server()

    def test_version(self):
        self.assertIn("1.0.40", self.adb.version())

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
