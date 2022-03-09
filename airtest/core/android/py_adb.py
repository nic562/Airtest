# -*- coding: utf-8 -*-
import os
import re
import sys
import time
import random
import warnings
import threading
from six import PY3
from six.moves import reduce

from ppadb.client import Client as AdbClient
from ppadb.device import Device
from ppadb import InstallError

from airtest.core.android.constant import IP_PATTERN, SDK_VERISON_ANDROID7
from airtest.core.error import AdbError, AdbShellError, AirtestError, DeviceConnectionError
from airtest.utils.compat import decode_path
from airtest.utils.logger import get_logger
from airtest.utils.retry import retries
from airtest.utils.snippet import get_std_encoding, reg_cleanup, split_cmd

LOGGING = get_logger(__name__)


class MyIO(object):
    def __init__(self, socket):
        self.socket = socket
        self.fd = self.socket.makefile(encoding='utf8')
        self.stdout = self.fd

    @staticmethod
    def _log_warn_not_implement(func_name):
        LOGGING.warn(f'MyIO.{func_name} is not implement! Maybe something wrong!')

    def poll(self):
        try:
            self.socket.sendall(b'')
        except Exception as e:
            LOGGING.error(f'Checking connection error: {e}')
            self.kill()
            return False

    def communicate(self):
        self._log_warn_not_implement('communicate')

    def wait(self):
        self._log_warn_not_implement('wait')

    def kill(self):
        try:
            self.fd.close()
            self.socket.close()
        except Exception as e:
            LOGGING.warn('Close Connection error: %s', e)


class ADB(object):
    """adb client object class
    Base on pure-python-adb
    see: https://github.com/Swind/pure-python-adb
    """

    _instances = []
    status_device = "device"
    status_offline = "offline"
    SHELL_ENCODING = "utf-8"

    def __init__(self, serial=None, server_addr=None, display_id=None):
        self.serial = serial
        self.start_server()
        if server_addr:
            host, port = server_addr.split(':')
            self.adb_client = AdbClient(host=host, port=port)
        else:
            self.adb_client = AdbClient()
        self.host = self.adb_client.host
        self._dev = None
        self.connect(serial)
        self._sdk_version = None
        self._line_breaker = None
        self._display_info = {}
        self._display_info_lock = threading.Lock()
        self.__class__._instances.append(self)

    def get_device(self) -> Device:
        if self._dev:
            return self._dev
        raise RuntimeError("No device is connected! Please call `connect` first!")

    def start_server(self):
        """
        Perform `adb start-server` command to start the adb server

        Returns:
            None
        """
        os.system('adb start-server')

    def kill_server(self):
        """
        Perform `adb kill-server` command to kill the adb server

        Returns:
            None

        """
        return self.adb_client.kill()

    def version(self):
        """
        Perform `adb version` command and return the command output

        Returns:
            command output

        """
        return self.adb_client.version()

    @staticmethod
    def format_cmd(cmd):
        cmd_list = split_cmd(cmd)

        if not PY3:
            cmd_list = [c.encode(get_std_encoding(sys.stdin)) for c in cmd_list]
        return ' '.join(cmd_list)

    def run_cmd(self, cmd: str):
        try:
            return self.adb_client._execute_cmd(f'host:{cmd}')
        except RuntimeError as e:
            raise AdbError("adb execute error:", e)

    def start_shell(self, cmd, timeout_ms=None):
        def handler(connection):
            return MyIO(connection.socket)

        return self.shell(cmd, timeout_ms, handler=handler)

    def raw_shell(self, cmd, timeout_ms=None, ensure_unicode=True):
        rs = self.shell(cmd, timeout_ms, ensure_unicode=False)
        if ensure_unicode:
            return rs
        return str(rs)

    def shell(self, cmd, timeout_ms=None, clean_wrap=False, handler=None, ensure_unicode=True):
        """
        执行命令
        :param cmd: 命令内容
        :param timeout_ms: 命令执行超时时间
        :param clean_wrap: 是否清理结果换行
        :param handler: 结果处理函数
        :param ensure_unicode: decode/encode unicode True or False, default is True
            def func(connection):
                try:
                    while True:
                        d = connection.read(1024)
                        if not d:
                            break
                        print(d.decode('utf-8'))
                finally:
                    connection.close()
        :return:
        """
        _cmd = self.format_cmd(cmd)
        LOGGING.debug(f'adb shell {_cmd}')
        rs = self.get_device().shell(_cmd, handler=handler, timeout=timeout_ms,
                                     decode=ensure_unicode and 'utf8' or None)
        if clean_wrap and isinstance(rs, str):
            rs = rs.strip()
        return rs

    def devices(self, state=None, return_obj=False):
        rs = self.adb_client.devices(state)
        if return_obj:
            return rs
        return [(x.serial, x.get_state()) for x in rs]

    def connect(self, serial=None):
        s = serial or self.serial
        if s:
            self._dev = self.adb_client.device(s)
            if not self._dev:
                raise RuntimeError(f'No device found for [{serial}]')
            self.serial = s
        return self._dev

    def disconnect(self):
        if self._dev:
            self._dev = None

    def get_status(self):
        return self.get_device().get_state()

    def wait_for_device(self, timeout=5):
        while timeout:
            ds = self.adb_client.devices()
            if ds:
                return
            timeout -= 1
            time.sleep(1)
        raise DeviceConnectionError('device not ready')

    def keyevent(self, key_name):
        return self.get_device().input_keyevent(key_name.upper())

    def getprop(self, key, strip=True):
        ps = self.get_device().get_properties()
        return ps[key]

    @property
    @retries(max_tries=3)
    def sdk_version(self):
        """
        Get the SDK version from the device

        Returns:
            SDK version
        """
        if self._sdk_version is None:
            self._sdk_version = int(self.getprop('ro.build.version.sdk'))
        return self._sdk_version

    def push(self, local, remote):
        """
        Perform `adb push` command

        Args:
            local: local file to be copied to the device
            remote: destination on the device where the file will be copied

        Returns:
            None

        """
        self.get_device().push(local, remote)

    def pull(self, remote, local):
        """
        Perform `adb pull` command
        Args:
            remote: remote file to be downloaded from the device
            local: local destination where the file will be downloaded from the device

        Returns:
            None
        """
        self.get_device().pull(remote, local)

    def forward(self, local, remote, no_rebind=True):
        """
        Perform `adb forward` command

        Args:
            local: local tcp port to be forwarded
            remote: tcp port of the device where the local tcp port will be forwarded
            no_rebind: True or False

        Returns:
            None

        """
        self.get_device().forward(local, remote, no_rebind)

    def get_forwards(self):
        """
        Perform `adb forward --list`command

        Yields:
            serial number, local tcp port, remote tcp port

        Returns:
            None

        """
        fs = self.get_device().list_forward()
        for local, remote in fs.items():
            yield self.serial, local, remote

    @classmethod
    def get_available_forward_local(cls):
        """
        Generate a pseudo random number between 11111 and 20000 that will be used as local forward port

        Returns:
            integer between 11111 and 20000

        Note:
            use `forward --no-rebind` to check if port is available
        """
        return random.randint(11111, 20000)

    @retries(3)
    def setup_forward(self, device_port, no_rebind=True):
        """
        Generate pseudo random local port and check if the port is available.

        Args:
            device_port: it can be string or the value of the `function(localport)`,
                         e.g. `"tcp:5001"` or `"localabstract:{}".format`
            no_rebind: adb forward --no-rebind option

        Returns:
            local port and device port

        """
        local_port = self.get_available_forward_local()
        if callable(device_port):
            device_port = device_port(local_port)
        self.forward("tcp:%s" % local_port, device_port, no_rebind=no_rebind)
        return local_port, device_port

    def remove_forward(self, local=None):
        """
        Perform `adb forward --remove` command

        Args:
            local: local tcp port

        Returns:
            None

        """
        if local:
            self.get_device().killforward(local)
        else:
            self.get_device().killforward_all()

    def install_app(self, filepath, replace=False, **kv):
        if isinstance(filepath, str):
            filepath = decode_path(filepath)

        if not os.path.isfile(filepath):
            raise RuntimeError("file: %s does not exists" % (repr(filepath)))
        try:
            self.get_device().install(filepath, reinstall=replace)
        except InstallError as e:
            raise AdbShellError("Installation Failure", e)

    def uninstall_app(self, package):
        return self.get_device().uninstall(package)

    def snapshot(self):
        """
        Take the screenshot of the device display

        Returns:
            command output (stdout)

        """
        return self.get_device().screencap()

    def touch(self, tuple_xy):
        x, y = tuple_xy
        self.get_device().input_tap(x, y)

    def swipe(self, tuple_x0y0, tuple_x1y1, duration=500):
        """
        Perform user input (swipe screen) from start point (x,y) to end point (x,y)

        Args:
            tuple_x0y0: start point coordinates (x, y)
            tuple_x1y1: end point coordinates (x, y)
            duration: time interval for action, default 500

        Raises:
            AirtestError: if SDK version is not supported

        Returns:
            None

        """
        # prot python 3
        x0, y0 = tuple_x0y0
        x1, y1 = tuple_x1y1
        self.get_device().input_swipe(x0, y0, x1, y1, duration=duration)

    def logcat(self, grep_str="", extra_args="", read_timeout=10):

        def handler(connection):
            sk = connection.socket
            try:
                with sk.makefile(encoding='utf8') as file_obj:
                    while True:
                        d = file_obj.readline()
                        data = d.strip()
                        if not data:
                            break
                        if grep_str:
                            if data.find(grep_str):
                                yield data
                            else:
                                continue
                        else:
                            yield data
            finally:
                sk.close()

        return self.shell('logcat', timeout_ms=read_timeout, handler=handler)

    def exists_file(self, filepath):
        """
        Check if the file exits on the device

        Args:
            filepath: path to the file

        Returns:
            True or False if file found or not

        """
        try:
            out = self.shell(["ls", filepath])
        except AdbShellError:
            return False
        else:
            return not ("No such file or directory" in out)

    def file_size(self, filepath):
        """
        Get the file size

        Args:
            filepath: path to the file

        Returns:
            The file size

        Raises:
            AdbShellError if no such file
        """
        out = self.shell(["ls", "-l", filepath])
        file_size = int(out.split()[4])
        return file_size

    @property
    def line_breaker(self):
        """
        Set carriage return and line break property for various platforms and SDK versions

        Returns:
            carriage return and line break string

        """
        if not self._line_breaker:
            if self.sdk_version >= SDK_VERISON_ANDROID7:
                line_breaker = os.linesep
            else:
                line_breaker = '\r' + os.linesep
            self._line_breaker = line_breaker.encode("ascii")
        return self._line_breaker

    @property
    def display_info(self):
        """
        Set device display properties (orientation, rotation and max values for x and y coordinates)

        Notes:
        if there is a lock screen detected, the function tries to unlock the device first

        Returns:
            device screen properties

        """
        self._display_info_lock.acquire()
        if not self._display_info:
            self._display_info = self.get_display_info()
        self._display_info_lock.release()
        return self._display_info

    def get_display_info(self):
        """
        Get information about device physical display (orientation, rotation and max values for x and y coordinates)

        Returns:
            device screen properties
            e.g {
                'width': 1440,
                'height': 2960,
                'density': 4.0,
                'orientation': 3,
                'rotation': 270,
                'max_x': 4095,
                'max_y': 4095
            }

        """
        display_info = self.getPhysicalDisplayInfo()
        orientation = self.getDisplayOrientation()
        max_x, max_y = self.getMaxXY()
        display_info.update({
            "orientation": orientation,
            "rotation": orientation * 90,
            "max_x": max_x,
            "max_y": max_y,
        })
        return display_info

    def getMaxXY(self):
        """
        Get device display maximum values for x and y coordinates

        Returns:
            max x and max y coordinates

        """
        ret = self.shell('getevent -p').split('\n')
        max_x, max_y = None, None
        for i in ret:
            if i.find("0035") != -1:
                patten = re.compile(r'max [0-9]+')
                ret = patten.search(i)
                if ret:
                    max_x = int(ret.group(0).split()[1])

            if i.find("0036") != -1:
                patten = re.compile(r'max [0-9]+')
                ret = patten.search(i)
                if ret:
                    max_y = int(ret.group(0).split()[1])
        return max_x, max_y

    def getRestrictedScreen(self):
        """
        Get value for mRestrictedScreen (without black border / virtual keyboard)`

        Returns:
            screen resolution mRestrictedScreen value as tuple (x, y)

        """
        # get the effective screen resolution of the device
        result = None
        # get the corresponding mRestrictedScreen parameters according to the device serial number
        dumpsys_info = self.shell("dumpsys window")
        match = re.search(r'mRestrictedScreen=.+', dumpsys_info)
        if match:
            infoline = match.group(0).strip()  # like 'mRestrictedScreen=(0,0) 720x1184'
            resolution = infoline.split(" ")[1].split("x")
            if isinstance(resolution, list) and len(resolution) == 2:
                result = int(str(resolution[0])), int(str(resolution[1]))

        return result

    def getPhysicalDisplayInfo(self):
        """
        Get value for display dimension and density from `mPhysicalDisplayInfo` value obtained from `dumpsys` command.

        Returns:
            physical display info for dimension and density

        """
        # use adb shell wm size
        displayInfo = {}
        wm_size = self.get_device().wm_size()
        if wm_size:
            displayInfo = dict((k, int(v)) for k, v in wm_size._asdict().items())
            displayInfo['density'] = self._getDisplayDensity(strip=True)
            return displayInfo

        phyDispRE = re.compile(
            '.*PhysicalDisplayInfo{(?P<width>\d+) x (?P<height>\d+), .*, density (?P<density>[\d.]+).*')
        out = self.shell('dumpsys display')
        m = phyDispRE.search(out)
        if m:
            for prop in ['width', 'height']:
                displayInfo[prop] = int(m.group(prop))
            for prop in ['density']:
                # In mPhysicalDisplayInfo density is already a factor, no need to calculate
                displayInfo[prop] = float(m.group(prop))
            return displayInfo

        # This could also be mSystem or mOverscanScreen
        phyDispRE = re.compile('\s*mUnrestrictedScreen=\((?P<x>\d+),(?P<y>\d+)\) (?P<width>\d+)x(?P<height>\d+)')
        # This is known to work on older versions (i.e. API 10) where mrestrictedScreen is not available
        dispWHRE = re.compile('\s*DisplayWidth=(?P<width>\d+) *DisplayHeight=(?P<height>\d+)')
        out = self.shell('dumpsys window')
        m = phyDispRE.search(out, 0)
        if not m:
            m = dispWHRE.search(out, 0)
        if m:
            for prop in ['width', 'height']:
                displayInfo[prop] = int(m.group(prop))
            for prop in ['density']:
                d = self._getDisplayDensity(strip=True)
                if d:
                    displayInfo[prop] = d
                else:
                    # No available density information
                    displayInfo[prop] = -1.0
            return displayInfo

        # gets C{mPhysicalDisplayInfo} values from dumpsys. This is a method to obtain display dimensions and density
        phyDispRE = re.compile('Physical size: (?P<width>\d+)x(?P<height>\d+).*Physical density: (?P<density>\d+)',
                               re.S)
        m = phyDispRE.search(self.shell('wm size; wm density'))
        if m:
            for prop in ['width', 'height']:
                displayInfo[prop] = int(m.group(prop))
            for prop in ['density']:
                displayInfo[prop] = float(m.group(prop))
            return displayInfo

        return displayInfo

    def _getDisplayDensity(self, strip=True):
        return self.get_device().wm_density()

    def getDisplayOrientation(self):
        """
        Another way to get the display orientation, this works well for older devices (SDK version 15)

        Returns:
            display orientation information

        """
        # another way to get orientation, for old sumsung device(sdk version 15) from xiaoma
        SurfaceFlingerRE = re.compile('orientation=(\d+)')
        output = self.shell('dumpsys SurfaceFlinger')
        m = SurfaceFlingerRE.search(output)
        if m:
            return int(m.group(1))

        # Fallback method to obtain the orientation
        # See https://github.com/dtmilano/AndroidViewClient/issues/128
        surfaceOrientationRE = re.compile('SurfaceOrientation:\s+(\d+)')
        output = self.shell('dumpsys input')
        m = surfaceOrientationRE.search(output)
        if m:
            return int(m.group(1))

        # We couldn't obtain the orientation
        warnings.warn("Could not obtain the orientation, return 0")
        return 0

    def update_cur_display(self, display_info):
        """
        Some phones support resolution modification, try to get the modified resolution from dumpsys
        adb shell dumpsys window displays | find "cur="

        本方法虽然可以更好地获取到部分修改过分辨率的手机信息
        但是会因为cur=(\d+)x(\d+)的数值在不同设备上width和height的顺序可能不同，导致横竖屏识别出现问题
        airtest不再使用本方法作为通用的屏幕尺寸获取方法，但依然可用于部分设备获取当前被修改过的分辨率

        Examples:

            >>> # 部分三星和华为设备，若分辨率没有指定为最高，可能会导致点击偏移，可以用这个方式强制修改：
            >>> # For some Samsung and Huawei devices, if the resolution is not specified as the highest,
            >>> # it may cause click offset, which can be modified in this way:
            >>> dev = device()
            >>> info = dev.display_info
            >>> info2 = dev.adb.update_cur_display(info)
            >>> dev.display_info.update(info2)

        Args:
            display_info: the return of self.getPhysicalDisplayInfo()

        Returns:
            display_info

        """
        # adb shell dumpsys window displays | find "init="
        # 上面的命令行在dumpsys window里查找init=widthxheight，得到的结果是物理分辨率，且部分型号手机不止一个结果
        # 如果改为读取 cur=widthxheight 的数据，得到的是修改过分辨率手机的结果（例如三星S8）
        actual = self.shell("dumpsys window displays")
        arr = re.findall(r'cur=(\d+)x(\d+)', actual)
        if len(arr) > 0:
            # 强制设定宽度width为更小的数字、height为更大的数字，避免因为各手机厂商返回结果的顺序不同导致问题
            # Set the width to a smaller number and the height to a larger number
            width, height = min(list(map(int, arr[0]))), max(list(map(int, arr[0])))
            display_info['physical_width'] = display_info['width']
            display_info['physical_height'] = display_info['height']
            display_info['width'], display_info['height'] = width, height
        return display_info

    def get_top_activity(self):
        """
        Perform `adb shell dumpsys activity top` command search for the top activity

        Raises:
            AirtestError: if top activity cannot be obtained

        Returns:
            top activity as a tuple: (package_name, activity_name, pid)

        """
        return self.get_device().get_top_activity()

    def is_keyboard_shown(self):
        """
        Perform `adb shell dumpsys input_method` command and search for information if keyboard is shown

        Returns:
            True or False whether the keyboard is shown or not

        """
        dim = self.shell('dumpsys input_method')
        if dim:
            return "mInputShown=true" in dim
        return False

    def is_screenon(self):
        """
        Perform `adb shell dumpsys window policy` command and search for information if screen is turned on or off

        Raises:
            AirtestError: if screen state can't be detected

        Returns:
            True or False whether the screen is turned on or off

        """
        screenOnRE = re.compile('mScreenOnFully=(true|false)')
        m = screenOnRE.search(self.shell('dumpsys window policy'))
        if m:
            return m.group(1) == 'true'
        else:
            # MIUI11
            screenOnRE = re.compile('screenState=(SCREEN_STATE_ON|SCREEN_STATE_OFF)')
            m = screenOnRE.search(self.shell('dumpsys window policy'))
            if m:
                return m.group(1) == 'SCREEN_STATE_ON'
        raise AirtestError("Couldn't determine screen ON state")

    def is_locked(self):
        """
        Perform `adb shell dumpsys window policy` command and search for information if screen is locked or not

        Raises:
            AirtestError: if lock screen can't be detected

        Returns:
            True or False whether the screen is locked or not

        """
        lockScreenRE = re.compile('(?:mShowingLockscreen|isStatusBarKeyguard|showing)=(true|false)')
        m = lockScreenRE.search(self.shell('dumpsys window policy'))
        if not m:
            raise AirtestError("Couldn't determine screen lock state")
        return m.group(1) == 'true'

    def unlock(self):
        """
        Perform `adb shell input keyevent MENU` and `adb shell input keyevent BACK` commands to attempt
        to unlock the screen

        Returns:
            None

        Warnings:
            Might not work on all devices

        """
        self.shell('input keyevent MENU')
        self.shell('input keyevent BACK')

    PKG_VERSION_MATCHER = re.compile(r'versionCode=(\d+)')

    def get_package_version(self, package):
        """
        Perform `adb shell dumpsys package` and search for information about given package version

        Args:
            package: package name

        Returns:
            None if no info has been found, otherwise package version

        """
        package_info = self.shell(['dumpsys', 'package', package])
        matcher = self.PKG_VERSION_MATCHER.search(package_info)
        if matcher:
            return int(matcher.group(1))
        return None

    def get_package_version_name(self, package):
        return self.get_device().get_package_version_name(package)

    def list_app(self, third_only=False):
        """
        Perform `adb shell pm list packages` to print all packages, optionally only
          those whose package name contains the text in FILTER.

        Options
            -f: see their associated file
            -d: filter to only show disabled packages
            -e: filter to only show enabled packages
            -s: filter to only show system packages
            -3: filter to only show third party packages
            -i: see the installer for the packages
            -u: also include uninstalled packages


        Args:
            third_only: print only third party packages

        Returns:
            list of packages

        """
        cmd = ["pm", "list", "packages"]
        if third_only:
            cmd.append("-3")
        output = self.shell(cmd)
        packages = output.splitlines()
        # remove all empty string; "package:xxx" -> "xxx"
        packages = [p.split(":")[1] for p in packages if p]
        return packages

    def path_app(self, package):
        """
        Perform `adb shell pm path` command to print the path to the package

        Args:
            package: package name

        Raises:
            AdbShellError: if any adb error occurs
            AirtestError: if package is not found on the device

        Returns:
            path to the package

        """
        try:
            output = self.shell(['pm', 'path', package])
        except AdbShellError:
            output = ""
        if 'package:' not in output:
            raise AirtestError('package not found, output:[%s]' % output)
        return output.split("package:")[1].strip()

    def check_app(self, package):
        """
        Perform `adb shell dumpsys package` command and check if package exists on the device

        Args:
            package: package name

        Raises:
            AirtestError: if package is not found

        Returns:
            True if package has been found

        """
        output = self.shell(['dumpsys', 'package', package])
        pattern = r'Package\s+\[' + str(package) + r'\]'
        match = re.search(pattern, output)
        if match is None:
            raise AirtestError('package "{}" not found'.format(package))
        return True

    def start_app(self, package, activity=None):
        """
        Perform `adb shell monkey` commands to start the application, if `activity` argument is `None`, then
        `adb shell am start` command is used.

        Args:
            package: package name
            activity: activity name

        Returns:
            None

        """
        if not activity:
            self.shell(['monkey', '-p', package, '-c', 'android.intent.category.LAUNCHER', '1'])
        else:
            self.shell(['am', 'start', '-n', '%s/%s.%s' % (package, package, activity)])

    def start_app_timing(self, package, activity):
        """
        Start the application and activity, and measure time

        Args:
            package: package name
            activity: activity name

        Returns:
            app launch time

        """
        out = self.shell(['am', 'start', '-S', '-W', '%s/%s' % (package, activity),
                          '-c', 'android.intent.category.LAUNCHER', '-a', 'android.intent.action.MAIN'])
        if not re.search(r"Status:\s*ok", out):
            raise AirtestError("Starting App: %s/%s Failed!" % (package, activity))

        # matcher = re.search(r"TotalTime:\s*(\d+)", out)
        matcher = re.search(r"TotalTime:\s*(\d+)", out)
        if matcher:
            return int(matcher.group(1))
        else:
            return 0

    def stop_app(self, package):
        """
        Perform `adb shell am force-stop` command to force stop the application

        Args:
            package: package name

        Returns:
            None

        """
        self.shell(['am', 'force-stop', package])

    def clear_app(self, package):
        """
        Perform `adb shell pm clear` command to clear all application data

        Args:
            package: package name

        Returns:
            None

        """
        self.shell(['pm', 'clear', package])

    def text(self, content):
        """
        Use adb shell input for text input

        Args:
            content: text to input

        Returns:
            None
        """
        if content.isalpha():
            self.shell(["input", "text", content])
        else:
            # 如果同时包含了字母+数字，用input text整句输入可能会导致乱序
            for i in content:
                self.shell(["input", "keyevent", "KEYCODE_" + i.upper()])

    def get_ip_address_from_interface(self, interface):
        """Get device ip from target network interface."""
        # android >= 6.0: ip -f inet addr show {interface}
        try:
            res = self.shell('ip -f inet addr show {}'.format(interface))
        except AdbShellError:
            res = ''
        matcher = re.search(r"inet (\d+\.){3}\d+", res)
        if matcher:
            return matcher.group().split(" ")[-1]

        # android >= 6.0 backup method: ifconfig
        try:
            res = self.shell('ifconfig')
        except AdbShellError:
            res = ''
        matcher = re.search(interface + r'.*?inet addr:((\d+\.){3}\d+)', res, re.DOTALL)
        if matcher:
            return matcher.group(1)

        # android <= 6.0: netcfg
        try:
            res = self.shell('netcfg')
        except AdbShellError:
            res = ''
        matcher = re.search(interface + r'.* ((\d+\.){3}\d+)/\d+', res)
        if matcher:
            return matcher.group(1)

        # android <= 6.0 backup method: getprop dhcp.{}.ipaddress
        try:
            res = self.shell('getprop dhcp.{}.ipaddress'.format(interface))
        except AdbShellError:
            res = ''
        matcher = IP_PATTERN.search(res)
        if matcher:
            return matcher.group(0)

        # sorry, no more methods...
        return None

    def get_ip_address(self):
        """
        Perform several set of commands to obtain the IP address.

            * `adb shell netcfg | grep wlan0`
            * `adb shell ifconfig`
            * `adb getprop dhcp.wlan0.ipaddress`

        Returns:
            None if no IP address has been found, otherwise return the IP address

        """

        for i in ('eth0', 'eth1', 'wlan0'):
            ip = self.get_ip_address_from_interface(i)
            if ip and not ip.startswith('172.') and not ip.startswith('127.') and not ip.startswith('169.'):
                return ip

        return None

    def get_gateway_address(self):
        """
        Perform several set of commands to obtain the gateway address.
            * `adb getprop dhcp.wlan0.gateway`
            * `adb shell netcfg | grep wlan0`

        Returns:
            None if no gateway address has been found, otherwise return the gateway address

        """
        ip2int = lambda ip: reduce(lambda a, b: (a << 8) + b, map(int, ip.split('.')), 0)
        int2ip = lambda n: '.'.join([str(n >> (i << 3) & 0xFF) for i in range(0, 4)[::-1]])
        try:
            res = self.shell('getprop dhcp.wlan0.gateway')
        except AdbShellError:
            res = ''
        matcher = IP_PATTERN.search(res)
        if matcher:
            return matcher.group(0)
        ip = self.get_ip_address()
        if not ip:
            return None
        mask_len = self._get_subnet_mask_len()
        gateway = (ip2int(ip) & (((1 << mask_len) - 1) << (32 - mask_len))) + 1
        return int2ip(gateway)

    def _get_subnet_mask_len(self):
        """
        Perform `adb shell netcfg | grep wlan0` command to obtain mask length

        Returns:
            17 if mask length could not be detected, otherwise the mask length

        """
        try:
            res = self.shell('netcfg')
        except AdbShellError:
            pass
        else:
            matcher = re.search(r'wlan0.* (\d+\.){3}\d+/(\d+) ', res)
            if matcher:
                return int(matcher.group(2))
        # 获取不到网段长度就默认取17
        print('[iputils WARNING] fail to get subnet mask len. use 17 as default.')
        return 17

    def get_memory(self):
        res = self.shell("dumpsys meminfo")
        pat = re.compile(r".*Total RAM:\s+(\S+)\s+", re.DOTALL)
        _str = pat.match(res).group(1)
        if ',' in _str:
            _list = _str.split(',')
            _num = int(_list[0])
            _num = round(_num + (float(_list[1]) / 1000.0))
        else:
            _num = round(float(_str) / 1000.0 / 1000.0)
        res = str(_num) + 'G'
        return res

    def get_storage(self):
        res = self.shell("df /data")
        pat = re.compile(r".*\/data\s+(\S+)", re.DOTALL)
        if pat.match(res):
            _str = pat.match(res).group(1)
        else:
            pat = re.compile(r".*\s+(\S+)\s+\S+\s+\S+\s+\S+\s+\/data", re.DOTALL)
            _str = pat.match(res).group(1)
        if 'G' in _str:
            _num = round(float(_str[:-1]))
        elif 'M' in _str:
            _num = round(float(_str[:-1]) / 1000.0)
        else:
            _num = round(float(_str) / 1000.0 / 1000.0)
        if _num > 64:
            res = '128G'
        elif _num > 32:
            res = '64G'
        elif _num > 16:
            res = '32G'
        elif _num > 8:
            res = '16G'
        else:
            res = '8G'
        return res

    def get_cpuinfo(self):
        res = self.shell("cat /proc/cpuinfo").strip()
        cpuNum = res.count("processor")
        pat = re.compile(r'Hardware\s+:\s+(\w+.*)')
        m = pat.match(res)
        if not m:
            pat = re.compile(r'Processor\s+:\s+(\w+.*)')
            m = pat.match(res)
        cpuName = m.group(1).replace('\r', '')
        return dict(cpuNum=cpuNum, cpuName=cpuName)

    def get_cpufreq(self):
        res = self.shell("cat /sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq")
        num = round(float(res) / 1000 / 1000, 1)
        res = str(num) + 'GHz'
        return res.strip()

    def get_cpuabi(self):
        res = self.shell("getprop ro.product.cpu.abi")
        return res.strip()

    def get_gpu(self):
        res = self.shell("dumpsys SurfaceFlinger")
        pat = re.compile(r'GLES:\s+(.*)')
        m = pat.search(res)
        if not m:
            return None
        _list = m.group(1).split(',')
        gpuModel = ""
        opengl = ""
        if len(_list) > 0:
            gpuModel = _list[1].strip()
        if len(_list) > 1:
            m2 = re.search(r'(\S+\s+\S+\s+\S+).*', _list[2])
            if m2:
                opengl = m2.group(1)
        return dict(gpuModel=gpuModel, opengl=opengl)

    def get_model(self):
        return self.getprop("ro.product.model")

    def get_manufacturer(self):
        return self.getprop("ro.product.manufacturer")

    def get_device_info(self):
        """
        Get android device information, including: memory/storage/display/cpu/gpu/model/manufacturer...

        Returns:
            Dict of info

        """
        handlers = {
            "platform": "Android",
            "serialno": self.serial,
            "memory": self.get_memory,
            "storage": self.get_storage,
            "display": self.getPhysicalDisplayInfo,
            "cpuinfo": self.get_cpuinfo,
            "cpufreq": self.get_cpufreq,
            "cpuabi": self.get_cpuabi,
            "sdkversion": self.sdk_version,
            "gpu": self.get_gpu,
            "model": self.get_model,
            "manufacturer": self.get_manufacturer,
            # "battery": getBatteryCapacity
        }
        ret = {}
        for k, v in handlers.items():
            if callable(v):
                try:
                    value = v()
                except Exception:
                    value = None
                ret[k] = value
            else:
                ret[k] = v
        return ret

    def get_display_of_all_screen(self, info):
        """
        Perform `adb shell dumpsys window windows` commands to get window display of application.

        Args:
            info: device screen properties

        Returns:
            None if adb command failed to run, otherwise return device screen properties(portrait mode)
            eg. (offset_x, offset_y, screen_width, screen_height)

        """
        output = self.shell("dumpsys window windows")
        windows = output.split("Window #")
        offsetx, offsety, width, height = 0, 0, info['width'], info['height']
        package = self._search_for_current_package(output)
        if package:
            for w in windows:
                if "package=%s" % package in w:
                    arr = re.findall(r'Frames: containing=\[(\d+\.?\d*),(\d+\.?\d*)]\[(\d+\.?\d*),(\d+\.?\d*)]', w)
                    if len(arr) >= 1 and len(arr[0]) == 4:
                        offsetx, offsety, width, height = float(arr[0][0]), float(arr[0][1]), float(arr[0][2]), float(
                            arr[0][3])
                        if info["orientation"] in [1, 3]:
                            offsetx, offsety, width, height = offsety, offsetx, height, width
                        width, height = width - offsetx, height - offsety
        return {
            "offset_x": offsetx,
            "offset_y": offsety,
            "offset_width": width,
            "offset_height": height,
        }

    def _search_for_current_package(self, ret):
        """
        Search for current app package name from the output of command "adb shell dumpsys window windows"

        Returns:
            package name if exists else ""
        """
        try:
            packageRE = re.compile('\s*mCurrentFocus=Window{.* ([A-Za-z0-9_.]+)/[A-Za-z0-9_.]+}')
            m = packageRE.findall(ret)
            if m:
                return m[-1]
            else:
                return self.get_top_activity()[0]
        except Exception as e:
            print("[Error] Cannot get current top activity")
        return ""


def cleanup_adb_forward():
    for adb in ADB._instances:
        try:
            adb.remove_forward()
        except RuntimeError:
            continue


reg_cleanup(cleanup_adb_forward)
