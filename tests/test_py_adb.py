# encoding=utf-8
import os
import unittest
from types import GeneratorType
from six import text_type

import sys
sys.path.append('..')

from airtest.core.android.py_adb import ADB
from testconf import IMG, PKG


class TestADBWithDeviceBase(unittest.TestCase):
    adb = None
    serial = None

    @classmethod
    def setUpClass(cls) -> None:
        adb = ADB()
        devices = adb.devices()
        if not devices:
            raise RuntimeError("At lease one adb device required")
        cls.serial = devices[0][0]
        cls.adb = adb
        cls.adb.connect(cls.serial)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.adb.disconnect()
        cls.adb.kill_server()


class TestADBWithDevice(TestADBWithDeviceBase):

    def test_get_status(self):
        self.assertEqual(self.adb.get_status(), self.adb.status_device)

    def test_shell_pwd(self):
        output = self.adb.shell("pwd")
        self.assertEqual(output.strip(), "/")
        self.assertIsInstance(output, text_type)

    def test_shell(self):
        output = self.adb.shell("time")
        self.assertIsInstance(output, text_type)

    def test_getprop(self):
        output = self.adb.getprop("wifi.interface")
        self.assertIsInstance(output, text_type)

    def test_sdk_version(self):
        output = self.adb.sdk_version
        self.assertIsInstance(output, int)

    def test_exists_file(self):
        self.assertTrue(self.adb.exists_file("/"))

    def test_push(self):
        tmp_dir = "/data/local/tmp"
        img_name = os.path.basename(IMG)
        tmp_img_path = tmp_dir + "/" + img_name
        self.adb.push(IMG, tmp_img_path)
        self.assertTrue(self.adb.exists_file(tmp_img_path))
        tmp_files = self.adb.shell("ls " + tmp_dir)
        self.assertIn(img_name, tmp_files, "The `%s` file not in /data/local/tmp!" % img_name)

        self.adb.pull(tmp_img_path, "./" + img_name)
        self.assertTrue(os.path.exists(img_name))
        os.remove(img_name)

        self.adb.shell('rm ' + tmp_img_path)
        self.assertFalse(self.adb.exists_file(tmp_img_path))

    def test_install(self):
        # output = self.adb.install_app(APK)
        # self.assertIn(PKG, self.adb.list_app())
        self.adb.uninstall_app(PKG)
        self.assertNotIn(PKG, self.adb.list_app())

    def test_ip(self):
        ip = self.adb.get_ip_address()
        if ip:
            self.assertEqual(len(ip.split('.')), 4)

    def test_gateway(self):
        gateway = self.adb.get_gateway_address()
        if gateway:
            self.assertEqual(len(gateway.split('.')), 4)


class TestADBWithDeviceLogcat(TestADBWithDeviceBase):

    def test_logcat(self):
        rs = self.adb.logcat()
        line_cnt = 0
        for line in rs:
            self.assertIsInstance(line, str)
            line_cnt += 1
            if line_cnt > 3:
                break
        self.assertGreater(line_cnt, 0)


class TestADBWithDeviceForwards(TestADBWithDeviceBase):

    def test_get_forwards(self):
        self.adb.remove_forward()
        self.adb.forward(local='tcp:6100', remote="tcp:7100")

        forwards = self.adb.get_forwards()
        self.assertIsInstance(forwards, GeneratorType)

        forwards = list(forwards)
        self.assertEqual(len(forwards), 1)
        sn, local, remote = forwards[0]
        self.assertEqual(sn, self.adb.serial)
        self.assertEqual(local, 'tcp:6100')
        self.assertEqual(remote, 'tcp:7100')

    def test_remove_forward(self):
        self.adb.remove_forward()
        self.assertEqual(len(list(self.adb.get_forwards())), 0)

        # set a remote and remove it
        self.adb.forward(local='tcp:6100', remote="tcp:7100")
        self.adb.remove_forward(local='tcp:6100')
        self.assertEqual(len(list(self.adb.get_forwards())), 0)

    def test_cleanup_forwards(self):
        """
        Test that all forward ports have been removed
        测试所有forward的端口号都被remove了
        """
        for port in ['tcp:10010', 'tcp:10020', 'tcp:10030']:
            self.adb.forward(port, port)
        self.adb.remove_forward()
        self.assertEqual(len(list(self.adb.get_forwards())), 0)


if __name__ == '__main__':
    unittest.main()
