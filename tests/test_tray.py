"""无桌面操作的回归检查：python -m unittest discover -s tests -v。"""
import ctypes
import gc
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

# 使用独立用户目录和离屏 Qt，不注册快捷键、不修改自启动、不操作真实窗口。
os.environ["QT_QPA_PLATFORM"] = "offscreen"
_DATA = tempfile.TemporaryDirectory(prefix="snapshot-tray-test-")
with patch.dict(os.environ, {"APPDATA": _DATA.name}):
    _SOURCE = Path(__file__).resolve().parents[1] / "screenshot_tool.py"
    _SPEC = importlib.util.spec_from_file_location("snapshot_under_test", _SOURCE)
    snapshot = importlib.util.module_from_spec(_SPEC)
    _SPEC.loader.exec_module(snapshot)

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QCloseEvent, QIcon
from PyQt5.QtWidgets import QApplication, QMainWindow, QSystemTrayIcon

APP = QApplication.instance() or QApplication(["snapshot-tray-test"])
APP.setQuitOnLastWindowClosed(False)
assert APP.platformName() == "offscreen", "禁止测试操作真实桌面"


class TrayWindow(snapshot.ScreenSnapApp):
    """仅构建真实窗口和托盘方法，跳过截图、快捷键和用户配置初始化。"""
    def __init__(self):
        QMainWindow.__init__(self)
        self.tray_icon = None
        self._tray_message_shown = False
        self._suppress_next_tray_message = True
        self._closing_via_tray_exit = False
        self._force_exit_once = False
        self._exiting = False
        self.close_behavior = "tray"
        self.workspace_page = Mock()
        self.workspace_page.get_dirty_tabs.return_value = []
        self._clear_overlays = Mock()
        self._teardown_hotkeys = Mock()
        self._setup_tray_icon()


class TrayTests(unittest.TestCase):
    def setUp(self):
        self.available = patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=True)
        self.available.start()
        self.window = TrayWindow()

    def tearDown(self):
        self.window._cleanup_before_exit()
        self.window.hide()
        self.window.deleteLater()
        QApplication.processEvents()
        self.available.stop()

    def test_startup_and_normal_minimize_keep_tray(self):
        self.assertTrue(self.window.tray_icon.isVisible())
        self.window.show()
        self.window.showMinimized()
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window.isMinimized())
        self.assertTrue(self.window.tray_icon.isVisible())

    def test_close_retains_taskbar_and_tray(self):
        self.window.show()
        event = QCloseEvent()
        self.window.closeEvent(event)
        self.assertFalse(event.isAccepted())
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window.isMinimized())
        self.assertTrue(self.window.tray_icon.isVisible())
        self.assertEqual(self.window.windowType(), Qt.Window)

    def test_tray_click_restores_minimized_window_and_keeps_icon(self):
        self.window._minimize_to_tray()
        self.window._on_tray_icon_activated(QSystemTrayIcon.Trigger)
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window.isMinimized())
        self.assertTrue(self.window.tray_icon.isVisible())

    def test_delayed_shell_start_does_not_lose_window_or_icon(self):
        self.window.tray_icon.hide()
        self.window.tray_icon.deleteLater()
        self.window.tray_icon = None
        with patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=False):
            event = QCloseEvent()
            self.window.closeEvent(event)
            self.assertFalse(event.isAccepted())
            self.assertTrue(self.window.isVisible())
            self.assertTrue(self.window.isMinimized())
            self.assertTrue(self.window.tray_icon.isVisible())
        self.window._restore_from_tray()
        self.assertFalse(self.window.isMinimized())

    def test_invalid_tray_image_preserves_taskbar(self):
        self.window.tray_icon.setIcon(QIcon())
        self.window._minimize_to_tray()
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window.isMinimized())

    def test_canceled_exit_retains_tray(self):
        self.window._minimize_to_tray()
        self.window._handle_unsaved_before_exit = Mock(return_value=False)
        self.window._exit_from_tray()
        self.assertFalse(self.window._closing_via_tray_exit)
        self.assertTrue(self.window.tray_icon.isVisible())
        self.assertTrue(self.window.isVisible())

    def test_menu_survives_garbage_collection_and_setup_is_idempotent(self):
        tray = self.window.tray_icon
        gc.collect()
        self.assertEqual([a.text() for a in tray.contextMenu().actions()], ["显示窗口", "退出"])
        self.window._setup_tray_icon()
        self.assertIs(self.window.tray_icon, tray)


class StartupTests(unittest.TestCase):
    def test_full_minimized_startup_has_original_icon_and_both_entries(self):
        config = json.loads((_SOURCE.parent / "config.json").read_text(encoding="utf-8"))
        with patch.object(snapshot, "load_config", return_value=config), \
                patch.object(snapshot, "save_config"), \
                patch.object(snapshot.ScreenSnapApp, "_sync_autostart_entry"), \
                patch.object(snapshot.ScreenSnapApp, "_register_all_hotkeys"), \
                patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=True):
            window = snapshot.ScreenSnapApp(start_minimized=True)
            try:
                QApplication.processEvents()
                self.assertTrue(window.isVisible())
                self.assertTrue(window.isMinimized())
                self.assertTrue(window.tray_icon.isVisible())
                self.assertFalse(window.windowIcon().pixmap(32, 32).isNull())
            finally:
                window._cleanup_before_exit()
                window.hide()
                window.deleteLater()
                QApplication.processEvents()


class IconTests(unittest.TestCase):
    def setUp(self):
        snapshot._APP_ICON = None

    def tearDown(self):
        snapshot._APP_ICON = None

    def test_original_icon_survives_changed_working_directory(self):
        previous = os.getcwd()
        try:
            os.chdir(_DATA.name)
            icon = snapshot.get_app_icon()
        finally:
            os.chdir(previous)
        self.assertFalse(icon.pixmap(32, 32).isNull())
        self.assertIn(32, [size.width() for size in icon.availableSizes()])
        self.assertEqual(icon.pixmap(32, 32).toImage(), snapshot.QPixmap(str(_SOURCE.parent / "favicon/favicon-32x32.png")).toImage())

    def test_missing_packaged_resources_fall_back_to_program_directory(self):
        with patch.object(snapshot, "RESOURCE_DIR", _DATA.name):
            self.assertFalse(snapshot.get_app_icon().pixmap(32, 32).isNull())

    def test_missing_all_resources_uses_visible_standard_icon(self):
        with patch.object(snapshot, "RESOURCE_DIR", _DATA.name), patch.object(snapshot, "BASE_DIR", _DATA.name):
            self.assertFalse(snapshot.get_app_icon().pixmap(32, 32).isNull())

    def test_corrupt_ico_uses_visible_standard_icon(self):
        with tempfile.TemporaryDirectory(dir=_DATA.name) as temp_dir:
            icons = Path(temp_dir) / "favicon"
            icons.mkdir()
            (icons / "favicon.ico").write_bytes(b"broken icon")
            with patch.object(snapshot, "RESOURCE_DIR", temp_dir), patch.object(snapshot, "BASE_DIR", temp_dir):
                self.assertFalse(snapshot.get_app_icon().pixmap(32, 32).isNull())

    def test_ico_only_distribution_still_works(self):
        with tempfile.TemporaryDirectory(dir=_DATA.name) as temp_dir:
            icons = Path(temp_dir) / "favicon"
            icons.mkdir()
            (icons / "favicon.ico").write_bytes((_SOURCE.parent / "favicon/favicon.ico").read_bytes())
            with patch.object(snapshot, "RESOURCE_DIR", temp_dir), patch.object(snapshot, "BASE_DIR", temp_dir):
                self.assertFalse(snapshot.get_app_icon().pixmap(16, 16).isNull())

    @unittest.skipUnless(sys.platform == "win32", "Windows 任务栏接口")
    def test_taskbar_has_independent_application_id(self):
        snapshot._set_taskbar_app_id()
        shell32 = ctypes.WinDLL("shell32")
        get_id = shell32.GetCurrentProcessExplicitAppUserModelID
        get_id.argtypes = (ctypes.POINTER(ctypes.c_void_p),)
        get_id.restype = ctypes.c_long
        app_id = ctypes.c_void_p()
        self.assertEqual(get_id(ctypes.byref(app_id)), 0)
        try:
            self.assertEqual(ctypes.wstring_at(app_id), "CTK.Snapshot")
        finally:
            free = ctypes.WinDLL("ole32").CoTaskMemFree
            free.argtypes = (ctypes.c_void_p,)
            free.restype = None
            free(app_id)


if __name__ == "__main__":
    unittest.main()
