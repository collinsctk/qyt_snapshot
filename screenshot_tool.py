import base64
import ctypes
from ctypes import wintypes
import hashlib
import html
import json
import math
import os
import re
import sys
import time
import tempfile
import logging
import atexit
import signal
import winreg
import threading  # 新增：用于异步 IO
from urllib.parse import urlparse
from datetime import datetime
from enum import Enum, auto
from contextlib import contextmanager

# Windows API 导入
try:
    import win32api
    import win32process
    import win32con
except ImportError:
    win32api = None
    win32process = None
    win32con = None

# region agent log helpers
# Debug Mode: 运行时证据日志（NDJSON），写入 Cursor 提供的固定路径
_AGENT_DEBUG_LOG_PATH = r"c:\Users\ThinkPad\CursorProjects\snapshot\.cursor\debug.log"
_AGENT_DEBUG_SESSION_ID = "debug-session"


def _agent_debug_log(*, hypothesisId: str, location: str, message: str, data=None, runId: str = "pre-fix"):
    """写入 NDJSON 调试日志（避免写入任何敏感信息/PII）。"""
    try:
        payload = {
            "timestamp": int(time.time() * 1000),
            "sessionId": _AGENT_DEBUG_SESSION_ID,
            "runId": runId,
            "hypothesisId": hypothesisId,
            "location": location,
            "message": message,
            "data": data or {},
        }
        with open(_AGENT_DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        # 调试日志绝不影响主流程
        pass

# endregion

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - optional dependency
    OpenAI = None

from PyQt5.QtCore import (
    QPoint,
    QRect,
    QPointF,
    QRectF,
    QLineF,
    Qt,
    pyqtSignal,
    QTimer,
    QUrl,
    QSize,
    QEvent,
    QBuffer,
    QByteArray,
    QIODevice,
    QThread,
    QMimeData,
    qInstallMessageHandler,
    QtMsgType,
)
from PyQt5.QtGui import (
    QColor,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QImage,
    QFont,
    QFontMetrics,
    QFontDatabase,
    QIcon,
    QBrush,
    QDesktopServices,
    QKeySequence,
    QCursor,
    QPolygonF,
)
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QColorDialog,
    QFileDialog,
    QLabel,
    QLineEdit,
    QInputDialog,
    QFontComboBox,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QDoubleSpinBox,
    QSlider,
    QShortcut,
    QGroupBox,
    QRadioButton,
    QListWidget,
    QTabWidget,
    QStackedWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
    QDialog,
    QHBoxLayout,
    QFrame,
    QSizePolicy,
    QCheckBox,
    QToolButton,
    QSystemTrayIcon,
    QMenu,
    QPlainTextEdit,
    QFormLayout,
    QSplitter,
)


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _determine_user_data_dir():
    candidates = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(os.path.join(appdata, "CTKSnapshot"))
    home = os.path.expanduser("~")
    if home:
        candidates.append(os.path.join(home, ".ctk_snapshot"))
    candidates.append(os.path.join(BASE_DIR, "user_data"))
    last_error = None
    for path in candidates:
        try:
            os.makedirs(path, exist_ok=True)
            return path, None
        except OSError as exc:
            last_error = exc
    return BASE_DIR, last_error


USER_DATA_DIR, _USER_DATA_ERROR = _determine_user_data_dir()
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
LEGACY_USER_CONFIG_FILE = os.path.join(BASE_DIR, "user.json")
USER_CONFIG_FILE = os.path.join(USER_DATA_DIR, "user.json")
DEFAULT_SAVE_DIR = os.path.join(USER_DATA_DIR, "screenshots")
LOG_FILE = os.path.join(USER_DATA_DIR, "snapshot.log")
ICON_PATH = os.path.join(BASE_DIR, "favicon", "favicon.ico")
_APP_ICON = None
CLASSIC_COLORS = [
    "#FF6B6B",
    "#FF9F43",
    "#FFD93D",
    "#1DD1A1",
    "#54A0FF",
    "#5F27CD",
    "#F368E0",
    "#00C7BE",
    "#576574",
]
PANEL_ACCENTS = {
    "marker": "#1AAE7F",
    "rectangle": "#5F27CD",
    "text": "#F7B500",
}

DEFAULT_MARKER_STYLE = {
    "fill": "#DC143C",
    "border": "#FFFFFFFF",
    "border_enabled": True,
    "size": 28,
    "font_ratio": 0.7,
}

DEFAULT_RECT_STYLE = {
    "fill": "#00000000",
    "border": "#FF7043",
    "border_enabled": True,
    "width": 3,
    "radius": 8,
}

DEFAULT_TEXT_STYLE = {
    "color": "#1e2433",
    "font": "Microsoft YaHei",
    "size": 18,
    "background": "transparent",
}

DEFAULT_IMAGE_BORDER_STYLE = {
    "color": "#FF7043",
    "width": 0,
}

_FONT_SUPPORT_CACHE = {}
_SAFE_FONT_FALLBACK = None


def _ensure_qt_plugins_path():
    """Ensure Qt platform plugins can be found when launching from source."""
    try:
        import PyQt5
        from PyQt5.QtCore import QLibraryInfo, QCoreApplication
    except Exception:
        return

    candidates = []
    built_in = QLibraryInfo.location(QLibraryInfo.PluginsPath)
    if built_in:
        candidates.append(built_in)
    pkg_plugins = os.path.join(os.path.dirname(getattr(PyQt5, "__file__", "")), "Qt", "plugins")
    candidates.append(pkg_plugins)

    valid_paths = []
    for path in candidates:
        platforms = os.path.join(path, "platforms")
        if os.path.isfile(os.path.join(platforms, "qwindows.dll")):
            valid_paths.append(path)
    if not valid_paths:
        return

    plugin_path = valid_paths[0]
    os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", plugin_path)
    os.environ.setdefault("QT_PLUGIN_PATH", plugin_path)
    existing_paths = QCoreApplication.libraryPaths()
    if plugin_path not in existing_paths:
        QCoreApplication.setLibraryPaths([plugin_path] + existing_paths)
    qt_bin = os.path.abspath(os.path.join(plugin_path, os.pardir, "bin"))
    if os.path.isdir(qt_bin) and qt_bin not in os.environ.get("PATH", ""):
        os.environ["PATH"] = qt_bin + os.pathsep + os.environ.get("PATH", "")


def _font_family_supported(family, db=None):
    key = (family or "").strip()
    if not key:
        return False
    if QApplication.instance() is None:
        return True
    cached = _FONT_SUPPORT_CACHE.get(key)
    if cached is not None:
        return cached
    db = db or QFontDatabase()
    if key not in db.families():
        _FONT_SUPPORT_CACHE[key] = False
        return False
    styles = db.styles(key)
    if not styles:
        _FONT_SUPPORT_CACHE[key] = False
        return False
    for style in styles:
        scalable = db.isSmoothlyScalable(key, style)
        if not scalable and hasattr(db, "isScalable"):
            scalable = db.isScalable(key, style)
        if scalable:
            _FONT_SUPPORT_CACHE[key] = True
            return True
    _FONT_SUPPORT_CACHE[key] = False
    return False


def _preferred_font_family():
    global _SAFE_FONT_FALLBACK
    if _SAFE_FONT_FALLBACK:
        return _SAFE_FONT_FALLBACK
    if QApplication.instance() is None:
        _SAFE_FONT_FALLBACK = DEFAULT_TEXT_STYLE["font"]
        return _SAFE_FONT_FALLBACK
    db = QFontDatabase()
    default = (DEFAULT_TEXT_STYLE["font"] or "").strip()
    if default and _font_family_supported(default, db=db):
        _SAFE_FONT_FALLBACK = default
        return _SAFE_FONT_FALLBACK
    for family in db.families():
        if _font_family_supported(family, db=db):
            _SAFE_FONT_FALLBACK = family
            return _SAFE_FONT_FALLBACK
    _SAFE_FONT_FALLBACK = default or "Arial"
    return _SAFE_FONT_FALLBACK


def _sanitize_font_family(family: str) -> str:
    family = (family or "").strip()
    if family and _font_family_supported(family):
        return family
    return _preferred_font_family()


def _setup_logging():
    try:
        os.makedirs(USER_DATA_DIR, exist_ok=True)
    except Exception:
        pass
    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    def _excepthook(exc_type, exc, tb):
        logging.exception("Uncaught exception", exc_info=(exc_type, exc, tb))
        try:
            sys.__excepthook__(exc_type, exc, tb)
        except Exception:
            pass

    def _qt_msg_handler(mode, context, message):
        try:
            level = {
                QtMsgType.QtDebugMsg: logging.DEBUG,
                QtMsgType.QtInfoMsg: logging.INFO,
                QtMsgType.QtWarningMsg: logging.WARNING,
                QtMsgType.QtCriticalMsg: logging.ERROR,
                QtMsgType.QtFatalMsg: logging.CRITICAL,
            }.get(mode, logging.INFO)
            logging.log(level, f"Qt: {message}")
        except Exception:
            pass

    sys.excepthook = _excepthook
    try:
        qInstallMessageHandler(_qt_msg_handler)
    except Exception:
        pass
    logging.info("Logging initialized")

    def _on_exit():
        logging.info("Process exiting")

    atexit.register(_on_exit)

def _shorten_label(text: str, max_len: int = None) -> str:
    if max_len is None:
        try:
            max_len = TAB_LABEL_MAX_LENGTH
        except NameError:
            max_len = 12
    if not text:
        return ""
    text = str(text)
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _dynamic_tab_label_length(tab_widget) -> int:
    """估算可用宽度，动态控制 tab 文本长度，尽量容纳 20 个。"""
    try:
        bar = tab_widget.tabBar()
        width = bar.width()
    except Exception:
        width = 0
    if not width:
        try:
            width = tab_widget.width()
        except Exception:
            width = 900
    target_tabs = 20
    avg_width = max(40, width / float(target_tabs))
    char_px = 7.5  # 粗略字符宽度
    max_len = int(avg_width / char_px)
    return max(6, min(20, max_len))

DEFAULT_IMAGE_QUALITY = 95
AI_TRANSLATION_PROMPT = (
    "请识别这张截图里的所有文字（保留原始顺序、标点和空格），并识别原文中的加粗/强调。"
    "请在输出里用 **粗体** 标记加粗内容，并在译文中对应位置保持相同的 **粗体**。"
    "段落划分请基于语义自然断句，不要仅按截图换行；同一语义段落内的换行可合并为一行。"
    "最后将识别结果和翻译合并到 JSON："
    "{\"original\": \"原文(保留**加粗**)\", \"translation\": \"翻译(同步**加粗**标记)\"}。"
)
DEFAULT_AI_SETTINGS = {
    "base_url": "https://aihubmix.com/v1",
    "api_key": "",
    "model": "gpt-4.1-nano",
}
TAB_LABEL_MAX_LENGTH = 12
SELECTION_HIT_MARGIN = 8
SELECTION_HANDLE_SIZE = 8
SELECTION_HANDLE_HIT_SIZE = 14
CHECKER_TILE_SIZE = 12
WAIT_OBJECT_0 = 0x00000000
WAIT_ABANDONED = 0x00000080
WAIT_TIMEOUT = 0x00000102
ERROR_ALREADY_EXISTS = 183
KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
USER32 = ctypes.WinDLL("user32", use_last_error=True)
KERNEL32.CreateMutexW.restype = wintypes.HANDLE
KERNEL32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
KERNEL32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
KERNEL32.ReleaseMutex.restype = wintypes.BOOL
KERNEL32.CloseHandle.argtypes = (wintypes.HANDLE,)
KERNEL32.CloseHandle.restype = wintypes.BOOL


def _build_mutex_name():
    exe_path = os.path.abspath(sys.argv[0] if sys.argv else __file__)
    digest = hashlib.sha1(exe_path.encode("utf-8")).hexdigest()
    return f"Local\\CTKSnapshot_{digest}"


class SingleInstanceGuard:
    def __init__(self):
        self._name = _build_mutex_name()
        self._handle = None
        self.already_running = False
        self._owns_mutex = False
        self._acquire()

    def _acquire(self):
        ctypes.set_last_error(0)
        handle = KERNEL32.CreateMutexW(None, False, self._name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._handle = handle
        wait_result = KERNEL32.WaitForSingleObject(self._handle, 0)
        if wait_result in (WAIT_OBJECT_0, WAIT_ABANDONED):
            self._owns_mutex = True
            self.already_running = False
        elif wait_result == WAIT_TIMEOUT:
            self.already_running = True
            self._owns_mutex = False
            KERNEL32.CloseHandle(self._handle)
            self._handle = None
        else:
            raise ctypes.WinError(ctypes.get_last_error() or 0)

    def release(self):
        if not self._handle:
            return
        if self._owns_mutex:
            KERNEL32.ReleaseMutex(self._handle)
        KERNEL32.CloseHandle(self._handle)
        self._handle = None

    def __del__(self):
        self.release()


def _notify_instance_running():
    logging.warning("Detected existing instance; exiting without starting UI.")
    flags = 0x00000030 | 0x00001000 | 0x00010000 | 0x00040000
    try:
        USER32.MessageBoxW(
            None,
            "程序不能多实例打开，本次启动已退出。",
            "CTK Snapshot",
            flags,
        )
    except Exception:
        print("程序不能多实例打开，本次启动已退出。", file=sys.stderr)


def _load_json_file(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError):
        return {}


def load_config():
    defaults = _load_json_file(CONFIG_FILE)
    overrides = _load_json_file(USER_CONFIG_FILE)
    if not overrides and os.path.exists(LEGACY_USER_CONFIG_FILE):
        overrides = _load_json_file(LEGACY_USER_CONFIG_FILE)
    config = {}
    if isinstance(defaults, dict):
        config.update(defaults)
    if isinstance(overrides, dict):
        config.update(overrides)
    return config


def _show_message_box(title, text, parent=None, critical=False):
    try:
        func = QMessageBox.critical if critical else QMessageBox.warning
        func(parent, title, text)
    except Exception:
        print(f"{title}: {text}", file=sys.stderr)


def save_config(data, parent=None):
    """异步保存配置文件，避免阻塞主线程"""
    def _do_save():
        try:
            os.makedirs(USER_DATA_DIR, exist_ok=True)
            with open(USER_CONFIG_FILE, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, ensure_ascii=False)
        except OSError as exc:
            logging.error(f"Failed to save config: {exc}")
            # 异步保存时如果出错，不弹出阻塞对话框，仅记录日志

    threading.Thread(target=_do_save, daemon=True).start()


def _normalized_ai_settings(settings):
    merged = dict(DEFAULT_AI_SETTINGS)
    if isinstance(settings, dict):
        for key in merged:
            value = settings.get(key)
            if value is None:
                continue
            merged[key] = str(value)
    return merged


def pixmap_to_png_bytes(pixmap: QPixmap) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    if not pixmap.save(buffer, "PNG"):
        raise ValueError("无法导出截图数据")
    data = bytes(buffer.data())
    buffer.close()
    return data


def _apply_bold_html(text: str) -> str:
    """Convert **bold** markers into <strong> after escaping."""
    if not text:
        return ""
    parts = []
    last = 0
    for match in re.finditer(r"\*\*(.+?)\*\*", text):
        parts.append(html.escape(text[last:match.start()]))
        parts.append(f"<strong>{html.escape(match.group(1))}</strong>")
        last = match.end()
    parts.append(html.escape(text[last:]))
    return "".join(parts)


def _markdownish_to_html(text: str) -> str:
    """Render简单 Markdown 风格文本为 HTML，保留列表与加粗。"""
    if not text:
        return ""
    # 预处理：压缩连续空行，去掉首尾空行
    raw_lines = text.splitlines()
    lines = []
    for line in raw_lines:
        if line.strip():
            lines.append(line)
        else:
            if lines and lines[-1] != "":
                lines.append("")
    if lines and lines[0] == "":
        lines = lines[1:]
    if lines and lines[-1] == "":
        lines = lines[:-1]

    html_lines = []
    in_list = False
    for raw in lines:
        stripped = raw.strip()
        is_bullet = bool(stripped.startswith("- ") or stripped.startswith("• "))
        if is_bullet:
            content = stripped[2:].lstrip()
            if not in_list:
                html_lines.append(
                    "<ul style=\"margin:4px 0 8px 0; padding-left:22px; list-style-position: outside; list-style-type: disc;\">"
                )
                in_list = True
            html_lines.append(f"<li style=\"margin:4px 0; padding-left:4px;\">&nbsp;{_apply_bold_html(content)}</li>")
            continue

        if in_list:
            html_lines.append("</ul>")
            in_list = False

        if not stripped:
            html_lines.append("<p style=\"margin:6px 0;\">&nbsp;</p>")
        else:
            html_lines.append(f"<p style=\"margin:2px 0 6px 0;\">{_apply_bold_html(raw)}</p>")

    if in_list:
        html_lines.append("</ul>")
    body = "\n".join(html_lines)
    return (
        "<html><head><meta charset=\"utf-8\"></head><body>"
        "<div style=\"font-family:'微软雅黑 Light','Microsoft YaHei Light','Microsoft YaHei',sans-serif;"
        " font-size:14px; line-height:1.55; color:#1e2433; white-space: normal;\">"
        f"{body}</div></body></html>"
    )


def _set_rich_clipboard(text: str):
    """Copy both富文本和纯文本，方便粘贴到 PPT。"""
    html_text = _markdownish_to_html(text)
    mime = QMimeData()
    if html_text:
        mime.setHtml(html_text)
    mime.setText(text or "")
    return _set_clipboard_mime_with_retry(mime)


def _set_clipboard_with_retry(setter, attempts=3, delay=0.12):
    last_exc = None
    for _ in range(max(1, attempts)):
        try:
            setter()
            return True
        except Exception as exc:
            last_exc = exc
            try:
                QApplication.processEvents()
            except Exception:
                pass
            time.sleep(delay)
    return False


def _set_clipboard_mime_with_retry(mime: QMimeData, attempts=3, delay=0.12):
    return _set_clipboard_with_retry(lambda: QApplication.clipboard().setMimeData(mime), attempts, delay)


def _set_clipboard_pixmap_with_retry(pixmap: QPixmap, attempts=3, delay=0.12):
    return _set_clipboard_with_retry(lambda: QApplication.clipboard().setPixmap(pixmap), attempts, delay)


def _set_clipboard_text_with_retry(text: str, attempts=3, delay=0.12):
    return _set_clipboard_with_retry(lambda: QApplication.clipboard().setText(text or ""), attempts, delay)


class AITranslationService:
    def __init__(self, settings):
        self.settings = _normalized_ai_settings(settings)
        self._client = None

    @staticmethod
    def is_configured(settings) -> bool:
        normalized = _normalized_ai_settings(settings or {})
        return all(normalized.get(key, "").strip() for key in ("base_url", "api_key", "model"))

    def _ensure_client(self):
        if OpenAI is None:
            raise RuntimeError("未安装 openai SDK，请先运行 pip install openai")
        if not self.is_configured(self.settings):
            raise ValueError("请先在系统设置中配置 AI 接口")
        if self._client is None:
            self._client = OpenAI(
                api_key=self.settings["api_key"].strip(),
                base_url=self.settings["base_url"].strip(),
            )
        return self._client

    def translate_image(self, image_bytes: bytes, prompt=AI_TRANSLATION_PROMPT):
        # region agent log
        # H7: 用户仅“处理图片”时是否被意外触发了 AI 识别/翻译（看起来像自动 OCR）
        try:
            base_url = (self.settings or {}).get("base_url", "")
            host = urlparse(base_url).netloc if base_url else ""
        except Exception:
            host = ""
        _agent_debug_log(
            hypothesisId="H7",
            location="screenshot_tool.py:AITranslationService.translate_image",
            message="translate_image called",
            data={
                "image_bytes_len": len(image_bytes) if image_bytes else 0,
                "model": (self.settings or {}).get("model", ""),
                "base_url_host": host,
            },
        )
        # endregion
        if not image_bytes:
            raise ValueError("截图内容为空，无法识别")
        client = self._ensure_client()
        base64_image = base64.b64encode(image_bytes).decode("ascii")
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{base64_image}",
                        },
                    },
                ],
            }
        ]
        response = client.chat.completions.create(
            model=self.settings["model"].strip(),
            messages=messages,
            stream=False,
        )
        text = self._extract_response_text(response)
        original, translation = self._parse_translation(text)
        if original and not translation:
            translation = self._translate_recognized_text(original)
        return original, translation, text

    def _translate_recognized_text(self, text):
        if not text:
            return ""
        client = self._ensure_client()
        prompt = (
            "下面是识别到的英文原文，请把它翻译为简洁流畅的中文，保留已有的 **加粗** 标记；若原文没有加粗则正常翻译。\n\n"
            + text.strip()
            + "\n\n只返回翻译文本，并保留 **加粗** 结构。"
        )
        response = client.chat.completions.create(
            model=self.settings["model"].strip(),
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            stream=False,
        )
        return (self._extract_response_text(response) or "").strip()

    def _extract_chat_response_text(self, response):
        choices = getattr(response, "choices", None)
        if not choices:
            return None
        try:
            first_choice = choices[0]
        except (KeyError, IndexError, TypeError):
            return None
        message = getattr(first_choice, "message", None)
        if message is None and isinstance(first_choice, dict):
            message = first_choice.get("message")
        if message is None:
            return None
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = []
            for part in content:
                if isinstance(part, dict):
                    text_value = part.get("text")
                else:
                    text_value = getattr(part, "text", None)
                if isinstance(text_value, str):
                    texts.append(text_value)
            if texts:
                return "\n".join(texts)
        return None

    def _extract_response_text(self, response) -> str:
        if response is None:
            return ""
        chat_text = self._extract_chat_response_text(response)
        if chat_text is not None:
            return chat_text
        text = getattr(response, "output_text", None)
        if text:
            return text
        for attr in ("text", "content"):
            maybe = getattr(response, attr, None)
            if isinstance(maybe, str):
                return maybe
        output = getattr(response, "output", None)
        chunks = []
        if isinstance(output, list):
            for block in output:
                block_content = getattr(block, "content", None)
                if block_content is None and isinstance(block, dict):
                    block_content = block.get("content")
                if isinstance(block_content, list):
                    for part in block_content:
                        text_value = getattr(part, "text", None)
                        if text_value is None and isinstance(part, dict):
                            text_value = part.get("text")
                        if isinstance(text_value, str):
                            chunks.append(text_value)
        if chunks:
            return "\n".join(chunks)
        return ""

    def _parse_translation(self, text: str):
        cleaned = (text or "").strip()
        data = self._try_extract_json(cleaned)
        if data:
            original = self._clean_text(str(data.get("original") or data.get("text") or ""))
            translation = self._clean_text(str(data.get("translation") or data.get("translated") or ""))
            return original, translation
        if "\n\n" in cleaned:
            original, translation = cleaned.split("\n\n", 1)
            return self._clean_text(original), self._clean_text(translation)
        if cleaned:
            return "", self._clean_text(cleaned)
        return "", ""

    def _try_extract_json(self, text):
        if not text:
            return None
        candidates = []
        if text.startswith("{") and text.endswith("}"):
            candidates.append(text)
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidates.append(text[start : end + 1])
        for candidate in candidates:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
        return None

    @staticmethod
    def _clean_text(value):
        if not value:
            return ""
        cleaned = value.strip()
        cleaned = cleaned.replace("\t", " ")
        cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
        cleaned = cleaned.replace("\ufffd", "")
        return cleaned


def build_ai_test_image_bytes():
    pixmap = QPixmap(360, 160)
    pixmap.fill(QColor("#f5f5f5"))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    font = QFont("Microsoft YaHei", 16)
    painter.setFont(font)
    painter.setPen(QPen(QColor("#222222"), 2))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "AI OCR TEST\n用于验证的示例图片")
    painter.end()
    return pixmap_to_png_bytes(pixmap)


class AITestWorker(QThread):
    finished = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, image_bytes: bytes, settings, parent=None):
        super().__init__(parent)
        self._image_bytes = image_bytes
        self._settings = settings

    def run(self):
        try:
            service = AITranslationService(self._settings)
            service.translate_image(self._image_bytes)
            self.finished.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class AITranslationWorker(QThread):
    completed = pyqtSignal(str, str)
    failed = pyqtSignal(str)

    def __init__(self, image_bytes: bytes, settings, parent=None):
        super().__init__(parent)
        self._image_bytes = image_bytes
        self._settings = settings

    def run(self):
        # region agent log
        # H7: AI worker 是否在你未点击“AI 截图翻译”时启动
        _agent_debug_log(
            hypothesisId="H7",
            location="screenshot_tool.py:AITranslationWorker.run",
            message="AITranslationWorker started",
            data={"image_bytes_len": len(self._image_bytes) if getattr(self, "_image_bytes", None) else 0},
        )
        # endregion
        try:
            service = AITranslationService(self._settings)
            original, translation, _ = service.translate_image(self._image_bytes)
            self.completed.emit(original, translation)
            # region agent log
            _agent_debug_log(
                hypothesisId="H7",
                location="screenshot_tool.py:AITranslationWorker.run",
                message="AITranslationWorker completed",
                data={
                    "original_len": len(original) if original else 0,
                    "translation_len": len(translation) if translation else 0,
                },
            )
            # endregion
        except Exception as exc:
            self.failed.emit(str(exc))
            # region agent log
            _agent_debug_log(
                hypothesisId="H7",
                location="screenshot_tool.py:AITranslationWorker.run",
                message="AITranslationWorker failed",
                data={"error": str(exc)[:200]},
            )
            # endregion


class Tool(Enum):
    NONE = auto()
    RECTANGLE = auto()
    MARKER = auto()
    TEXT = auto()
    SELECTION = auto()
    CROP = auto()
    IMAGE = auto()
    LINE = auto()
    ARROW = auto()

MODIFIER_ORDER = [
    (Qt.ControlModifier, "Ctrl"),
    (Qt.ShiftModifier, "Shift"),
    (Qt.AltModifier, "Alt"),
    (Qt.MetaModifier, "Win"),
]
MODIFIER_NAME_SET = {name for _, name in MODIFIER_ORDER}
SPECIAL_KEY_DISPLAY = {
    Qt.Key_Escape: "Esc",
    Qt.Key_Tab: "Tab",
    Qt.Key_Backspace: "Backspace",
    Qt.Key_Return: "Enter",
    Qt.Key_Enter: "Enter",
    Qt.Key_Insert: "Insert",
    Qt.Key_Delete: "Delete",
    Qt.Key_Home: "Home",
    Qt.Key_End: "End",
    Qt.Key_PageUp: "PageUp",
    Qt.Key_PageDown: "PageDown",
    Qt.Key_Left: "Left",
    Qt.Key_Right: "Right",
    Qt.Key_Up: "Up",
    Qt.Key_Down: "Down",
    Qt.Key_Space: "Space",
    Qt.Key_Print: "PrintScreen",
    Qt.Key_Plus: "+",
    Qt.Key_Minus: "-",
    Qt.Key_Slash: "/",
    Qt.Key_Backslash: "\\",
    Qt.Key_Comma: ",",
    Qt.Key_Period: ".",
    Qt.Key_Semicolon: ";",
    Qt.Key_Equal: "=",
    Qt.Key_BracketLeft: "[",
    Qt.Key_BracketRight: "]",
}
DISPLAY_NAME_OVERRIDES = {
    "ctrl": "Ctrl",
    "shift": "Shift",
    "alt": "Alt",
    "win": "Win",
    "meta": "Meta",
    "esc": "Esc",
    "tab": "Tab",
    "enter": "Enter",
    "delete": "Delete",
    "insert": "Insert",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "pagedown": "PageDown",
    "left": "Left",
    "right": "Right",
    "up": "Up",
    "down": "Down",
    "space": "Space",
    "printscreen": "PrintScreen",
    "print": "PrintScreen",
    "+": "+",
    "-": "-",
    "/": "/",
    "\\": "\\",
    ",": ",",
    ".": ".",
    ";": ";",
    "=": "=",
    "[": "[",
    "]": "]",
}

HOTKEY_ACTIONS = [
    ("capture", "区域截图"),
    ("repeat_capture", "重复截图"),
    ("ai_translate", "AI 截图翻译"),
]

WM_HOTKEY = 0x0312
RUN_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_REG_NAME = "CTKSnapshot"
MODIFIER_BITMASK = {
    "alt": 0x0001,
    "ctrl": 0x0002,
    "shift": 0x0004,
    "win": 0x0008,
}
VIRTUAL_KEY_MAP = {
    "esc": 0x1B,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "space": 0x20,
    "backspace": 0x08,
    "insert": 0x2D,
    "delete": 0x2E,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "printscreen": 0x2C,
    "pause": 0x13,
    "capslock": 0x14,
    ";": 0xBA,
    ":": 0xBA,
    "=": 0xBB,
    "+": 0xBB,
    ",": 0xBC,
    "<": 0xBC,
    "-": 0xBD,
    "_": 0xBD,
    ".": 0xBE,
    ">": 0xBE,
    "/": 0xBF,
    "?": 0xBF,
    "`": 0xC0,
    "~": 0xC0,
    "[": 0xDB,
    "{": 0xDB,
    "]": 0xDD,
    "}": 0xDD,
    "\\": 0xDC,
    "|": 0xDC,
}


def _key_name_to_vk(name: str):
    if not name:
        return None
    name = name.lower()
    if name in VIRTUAL_KEY_MAP:
        return VIRTUAL_KEY_MAP[name]
    if name.startswith("f") and name[1:].isdigit():
        idx = int(name[1:])
        if 1 <= idx <= 24:
            return 0x70 + (idx - 1)
    if len(name) == 1:
        char = name.upper()
        if "A" <= char <= "Z" or "0" <= char <= "9":
            return ord(char)
    return None


def _shortcut_to_native(shortcut: str):
    if not shortcut:
        return None
    parts = [part.strip() for part in shortcut.split("+") if part.strip()]
    if not parts:
        return None
    modifiers = 0
    key_name = None
    for part in parts:
        lower = part.lower()
        if lower in MODIFIER_BITMASK:
            modifiers |= MODIFIER_BITMASK[lower]
        else:
            key_name = lower
    if not key_name:
        return None
    vk = _key_name_to_vk(key_name)
    if vk is None:
        return None
    return modifiers, vk


class GlobalHotkeyManager:
    def __init__(self, window):
        self._window = window
        self._user32 = ctypes.windll.user32
        self._next_id = 1
        self._action_to_id = {}
        self._id_to_action = {}

    def register(self, action_id, shortcut):
        self.unregister(action_id)
        native = _shortcut_to_native(shortcut)
        if not native:
            raise ValueError(f"无法识别快捷键: {shortcut}")
        modifiers, vk = native
        hotkey_id = self._next_id
        self._next_id += 1
        hwnd = int(self._window.winId())
        if not self._user32.RegisterHotKey(hwnd, hotkey_id, modifiers, vk):
            raise ctypes.WinError()
        self._action_to_id[action_id] = hotkey_id
        self._id_to_action[hotkey_id] = action_id

    def unregister(self, action_id):
        hotkey_id = self._action_to_id.pop(action_id, None)
        if hotkey_id is not None:
            self._user32.UnregisterHotKey(int(self._window.winId()), hotkey_id)
            self._id_to_action.pop(hotkey_id, None)

    def unregister_all(self):
        for action_id in list(self._action_to_id.keys()):
            self.unregister(action_id)

    def handle_message(self, hotkey_id):
        action_id = self._id_to_action.get(hotkey_id)
        if action_id:
            self._window._on_hotkey_trigger(action_id)


def get_app_icon():
    global _APP_ICON
    if _APP_ICON is None:
        if os.path.exists(ICON_PATH):
            _APP_ICON = QIcon(ICON_PATH)
        else:  # pragma: no cover - fallback branch
            _APP_ICON = QIcon()
    return _APP_ICON


def _modifier_names(modifiers):
    return [name for mask, name in MODIFIER_ORDER if modifiers & mask]


def _base_key_name(key):
    if Qt.Key_F1 <= key <= Qt.Key_F35:
        return f"F{key - Qt.Key_F1 + 1}"
    if Qt.Key_A <= key <= Qt.Key_Z:
        return chr(key)
    if Qt.Key_0 <= key <= Qt.Key_9:
        return chr(key)
    return SPECIAL_KEY_DISPLAY.get(key)


def _normalize_part(part):
    return (part or "").lower()


def _format_display_shortcut(normalized):
    if not normalized:
        return "未设置全局热键"
    parts = [part for part in normalized.split("+") if part]
    friendly = []
    for part in parts:
        friendly.append(DISPLAY_NAME_OVERRIDES.get(part, part.upper()))
    return "+".join(friendly)


class ShortcutRecorder(QLineEdit):
    shortcutRecorded = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.recording = False
        self.setPlaceholderText("按组合键录入快捷键")

    def start_recording(self):
        self.recording = True
        self.setText("正在录制，按下组合键...")
        self.grabKeyboard()
        self.setFocus(Qt.OtherFocusReason)

    def stop_recording(self):
        if self.recording:
            self.releaseKeyboard()
        self.recording = False

    def keyPressEvent(self, event):
        if not self.recording:
            super().keyPressEvent(event)
            return
        if event.key() == Qt.Key_Escape:
            self.setText("")
            self.stop_recording()
            return
        parts = _modifier_names(event.modifiers())
        key_name = _base_key_name(event.key())
        if not key_name:
            return
        if key_name not in MODIFIER_NAME_SET:
            parts.append(key_name)
        if not parts:
            return
        display_value = "+".join(parts)
        normalized = "+".join(_normalize_part(part) for part in parts)
        self.setText(display_value)
        self.shortcutRecorded.emit(display_value, normalized)
        self.stop_recording()


class ShortcutDialog(QDialog):
    shortcutSet = pyqtSignal(str, str)

    def __init__(self, parent=None, current_display=""):
        super().__init__(parent)
        self.setWindowTitle("设置全局截屏热键")
        self.setWindowIcon(get_app_icon())
        self._display_value = current_display
        self._normalized_value = ""

        self.recorder = ShortcutRecorder()
        self.recorder.shortcutRecorded.connect(self._on_recorded)

        instruction_text = "按住希望的快捷键组合，然后松开。"
        if current_display:
            instruction_text += f"\n当前热键: {current_display}"
        instruction = QLabel(instruction_text)
        self._retry_btn = QPushButton("重新录制")
        self._confirm_btn = QPushButton("确认")
        self._confirm_btn.setEnabled(False)
        cancel_btn = QPushButton("取消")

        self._retry_btn.clicked.connect(self._start_recording)
        self._confirm_btn.clicked.connect(self._on_confirm)
        cancel_btn.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addWidget(self._retry_btn)
        buttons.addWidget(self._confirm_btn)
        buttons.addWidget(cancel_btn)

        layout = QVBoxLayout()
        layout.addWidget(instruction)
        layout.addWidget(self.recorder)
        layout.addLayout(buttons)
        self.setLayout(layout)

        self._start_recording()

    def _start_recording(self):
        self._confirm_btn.setEnabled(False)
        self.recorder.start_recording()

    def _on_recorded(self, display, normalized):
        self._display_value = display
        self._normalized_value = normalized
        self._confirm_btn.setEnabled(True)

    def _on_confirm(self):
        if self._normalized_value:
            self.shortcutSet.emit(self._display_value, self._normalized_value)
            self.accept()


class GeneralSettingsPage(QWidget):
    def __init__(
        self,
        auto_save_enabled,
        save_dir,
        auto_start_enabled,
        close_behavior,
        exit_unsaved_policy,
        parent=None,
    ):
        super().__init__(parent)
        self._auto_save_enabled = auto_save_enabled
        self._save_dir = save_dir or ""
        self._close_behavior = close_behavior or "tray"
        self._exit_unsaved_policy = exit_unsaved_policy or "save_all"
        if self._exit_unsaved_policy not in ("save_all", "discard_all"):
            self._exit_unsaved_policy = "save_all"
        layout = QVBoxLayout()

        title = QLabel(u"\u7cfb\u7edf\u8bbe\u7f6e \xb7 \u5e38\u89c4")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        layout.addWidget(title)

        intro = QLabel(u"\u5728\u8fd9\u91cc\u53ef\u4ee5\u8bbe\u7f6e\u81ea\u52a8\u4fdd\u5b58\u3001\u81ea\u52a8\u542f\u52a8\u4ee5\u53ca\u5173\u95ed\u7a97\u53e3\u65f6\u7684\u9ed8\u8ba4\u52a8\u4f5c\u3002")
        intro.setStyleSheet("color: #666666;")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        layout.addSpacing(16)

        auto_save_box = QVBoxLayout()
        self.auto_save_checkbox = QCheckBox(u"\u5f00\u542f\u81ea\u52a8\u4fdd\u5b58\u539f\u59cb\u622a\u56fe")
        self.auto_save_checkbox.setChecked(auto_save_enabled)
        self.auto_save_checkbox.toggled.connect(self._on_auto_save_toggled)
        auto_save_box.addWidget(self.auto_save_checkbox)

        path_row = QHBoxLayout()
        self.path_edit = QLineEdit(self._save_dir)
        self.path_edit.setReadOnly(True)
        path_row.addWidget(self.path_edit, 1)
        self.choose_btn = QPushButton(u"\u9009\u62e9\u76ee\u5f55")
        self.choose_btn.clicked.connect(self._choose_dir)
        path_row.addWidget(self.choose_btn)
        auto_save_box.addLayout(path_row)

        hint = QLabel(u"\u542f\u7528\u540e\u6bcf\u6b21\u622a\u56fe\u90fd\u4f1a\u81ea\u52a8\u5199\u5165\u5f53\u524d\u76ee\u5f55\uff0c\u4ecd\u53ef\u5728\u624b\u52a8\u4fdd\u5b58\u65f6\u8986\u76d6\u3002")
        hint.setStyleSheet("color: #777777; font-size: 12px;")
        hint.setWordWrap(True)
        auto_save_box.addWidget(hint)

        layout.addLayout(auto_save_box)
        layout.addSpacing(16)

        self.startup_checkbox = QCheckBox(u"\u5f00\u673a\u81ea\u52a8\u542f\u52a8\u5e76\u9a7b\u7559\u7cfb\u7edf\u6258\u76d8")
        self.startup_checkbox.setChecked(auto_start_enabled)
        startup_hint = QLabel(u"\u7cfb\u7edf\u542f\u52a8\u540e\u4f1a\u81ea\u52a8\u8fd0\u884c CTK Snapshot \u5e76\u7f29\u5230\u6258\u76d8\u3002")
        startup_hint.setWordWrap(True)
        startup_hint.setStyleSheet("color: #777777; font-size: 12px;")
        layout.addWidget(self.startup_checkbox)
        layout.addWidget(startup_hint)

        layout.addSpacing(20)

        close_group = QGroupBox(u"\u5173\u95ed\u4e3b\u7a97\u53e3\u65f6")
        close_group_layout = QVBoxLayout(close_group)
        close_desc = QLabel(u"\u8bbe\u7f6e\u70b9\u51fb\u7a97\u53e3\u5173\u95ed\u6309\u94ae\u540e\u7684\u9ed8\u8ba4\u884c\u4e3a\u3002")
        close_desc.setStyleSheet("color: #555555;")
        close_desc.setWordWrap(True)
        close_group_layout.addWidget(close_desc)

        self.close_tray_radio = QRadioButton(u"\u6700\u5c0f\u5316\u5230\u7cfb\u7edf\u6258\u76d8\uff08\u9ed8\u8ba4\uff09")
        self.close_exit_radio = QRadioButton(u"\u76f4\u63a5\u9000\u51fa\u7a0b\u5e8f")
        close_group_layout.addWidget(self.close_tray_radio)
        close_group_layout.addWidget(self.close_exit_radio)

        self.exit_policy_container = QGroupBox(u"\u76f4\u63a5\u9000\u51fa\u65f6\u82e5\u5b58\u5728\u672a\u4fdd\u5b58\u7684\u56fe\u7247")
        exit_layout = QVBoxLayout(self.exit_policy_container)
        self.exit_save_radio = QRadioButton(u"\u81ea\u52a8\u4fdd\u5b58\u6240\u6709\u540e\u9000\u51fa")
        self.exit_discard_radio = QRadioButton(u"\u4e0d\u4fdd\u5b58\u76f4\u63a5\u9000\u51fa\uff08\u5c06\u4e22\u5931\u4fee\u6539\uff09")
        exit_layout.addWidget(self.exit_save_radio)
        exit_layout.addWidget(self.exit_discard_radio)
        close_group_layout.addWidget(self.exit_policy_container)

        if self._close_behavior == "exit":
            self.close_exit_radio.setChecked(True)
        else:
            self.close_tray_radio.setChecked(True)

        if self._exit_unsaved_policy == "discard_all":
            self.exit_discard_radio.setChecked(True)
        else:
            self.exit_save_radio.setChecked(True)

        self.close_tray_radio.toggled.connect(self._update_exit_controls_state)
        self.close_exit_radio.toggled.connect(self._update_exit_controls_state)

        layout.addWidget(close_group)
        layout.addStretch()
        self.setLayout(layout)
        self._update_path_controls(auto_save_enabled)
        self._update_exit_controls_state(self.close_exit_radio.isChecked())

    def _on_auto_save_toggled(self, checked):
        if checked and not self._save_dir:
            self._choose_dir()
        self._update_path_controls(checked)

    def _choose_dir(self):
        folder = QFileDialog.getExistingDirectory(self, "选择自动保存目录", self._save_dir or DEFAULT_SAVE_DIR)
        if folder:
            self._save_dir = folder
            self.path_edit.setText(folder)
        elif not self._save_dir:
            self.auto_save_checkbox.setChecked(False)

    def _update_path_controls(self, enabled):
        self.path_edit.setEnabled(enabled)
        self.choose_btn.setEnabled(enabled)

    def _update_exit_controls_state(self, _=None):
        exit_selected = self.close_exit_radio.isChecked()
        self.exit_policy_container.setEnabled(exit_selected)

    def get_settings(self):
        return {
            "auto_save_enabled": self.auto_save_checkbox.isChecked(),
            "save_dir": self._save_dir,
            "auto_start_enabled": self.startup_checkbox.isChecked(),
            "close_behavior": "exit" if self.close_exit_radio.isChecked() else "tray",
            "exit_unsaved_policy": "discard_all" if self.exit_discard_radio.isChecked() else "save_all",
        }


class HotkeySettingsPage(QWidget):
    def __init__(self, hotkeys, parent=None):
        super().__init__(parent)
        self._working_hotkeys = {k: dict(v) for k, v in hotkeys.items()}
        layout = QVBoxLayout()

        header = QLabel("为不同操作设置全局快捷键")
        header.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(header)
        desc = QLabel("在此输入供应商提供的 Base URL、API Key 和模型名称，可点击“测试”确认是否具备图像识别能力。")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self._rows = {}
        for action_id, action_name in HOTKEY_ACTIONS:
            row_frame = QFrame()
            row_frame.setFrameShape(QFrame.StyledPanel)
            row_frame.setStyleSheet("QFrame { background: #fafafa; border: 1px solid #e5e5e5; border-radius: 6px; }")
            row_layout = QHBoxLayout(row_frame)
            label = QLabel(action_name)
            label.setStyleSheet("font-weight: 600;")
            shortcut_label = QLabel(self._format_hotkey_text(action_id))
            shortcut_label.setWordWrap(True)
            shortcut_label.setStyleSheet("color: #333333;")
            row_layout.addWidget(label)
            row_layout.addWidget(shortcut_label, 1)

            button_box = QHBoxLayout()
            set_btn = QPushButton("设置快捷键")
            clear_btn = QPushButton("清除")
            set_btn.clicked.connect(lambda _, act=action_id: self._open_shortcut_dialog(act))
            clear_btn.clicked.connect(lambda _, act=action_id: self._clear_hotkey(act))
            button_box.addWidget(set_btn)
            button_box.addWidget(clear_btn)
            row_layout.addLayout(button_box)

            layout.addWidget(row_frame)
            self._rows[action_id] = (shortcut_label, set_btn, clear_btn)

        self._status_label = QLabel()
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color: #666666;")
        layout.addSpacing(10)
        layout.addWidget(self._status_label)
        layout.addStretch()
        self.setLayout(layout)
        self._refresh_rows()

    def _format_hotkey_text(self, action_id):
        hotkey = self._working_hotkeys.get(action_id, {})
        display = hotkey.get("display")
        shortcut = hotkey.get("shortcut")
        if display:
            return display
        if shortcut:
            return _format_display_shortcut(shortcut)
        return "未设置"

    def _refresh_rows(self):
        for action_id, (label, set_btn, clear_btn) in self._rows.items():
            label.setText(self._format_hotkey_text(action_id))
            has_hotkey = bool(self._working_hotkeys.get(action_id, {}).get("shortcut"))
            set_btn.setEnabled(True)
            clear_btn.setEnabled(has_hotkey)
        status_text = "快捷键依赖系统级注册，保存设置后立即生效。若注册失败会弹出提示，请更换组合或检查权限。"
        self._status_label.setText(status_text)

    def _open_shortcut_dialog(self, action_id):
        dialog = ShortcutDialog(self, current_display=self._format_hotkey_text(action_id))
        dialog.shortcutSet.connect(lambda display, normalized, act=action_id: self._apply_hotkey(act, display, normalized))
        dialog.exec_()

    def _apply_hotkey(self, action_id, display, normalized):
        self._working_hotkeys[action_id] = {"display": display, "shortcut": normalized}
        self._refresh_rows()

    def _clear_hotkey(self, action_id):
        if action_id in self._working_hotkeys:
            self._working_hotkeys.pop(action_id)
            self._refresh_rows()

    def get_hotkeys(self):
        return {k: v for k, v in self._working_hotkeys.items() if v.get("shortcut")}


class QualitySettingsPage(QWidget):
    def __init__(self, quality, parent=None):
        super().__init__(parent)
        self._quality = self._clamp_quality(quality if quality is not None else DEFAULT_IMAGE_QUALITY)
        layout = QVBoxLayout()

        title = QLabel("配置 AI 截图翻译能力")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(title)

        desc = QLabel("拖动滑块或直接输入百分比，决定保存 PNG/JPG 时使用的图像质量。数值越大，画质越高、文件也越大。")
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #4a4a4a;")
        layout.addWidget(desc)

        slider_row = QHBoxLayout()
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(10, 100)
        self.slider.setSingleStep(1)
        self.slider.valueChanged.connect(self._on_slider_changed)
        slider_row.addWidget(self.slider, 1)

        self.spin = QSpinBox()
        self.spin.setRange(10, 100)
        self.spin.setSuffix("%")
        self.spin.valueChanged.connect(self._on_spin_changed)
        slider_row.addSpacing(12)
        slider_row.addWidget(self.spin)
        layout.addLayout(slider_row)

        self.summary = QLabel()
        self.summary.setStyleSheet("color: #5c6470;")
        layout.addSpacing(12)
        layout.addWidget(self.summary)
        layout.addStretch()
        self.setLayout(layout)

        self._sync_controls(self._quality)

    def _sync_controls(self, value):
        self.slider.blockSignals(True)
        self.spin.blockSignals(True)
        self.slider.setValue(value)
        self.spin.setValue(value)
        self.slider.blockSignals(False)
        self.spin.blockSignals(False)
        self.summary.setText(f"当前质量：{value}%。推荐 80-95 之间兼顾画质与体积。")

    def _on_slider_changed(self, value):
        value = self._clamp_quality(value)
        self._quality = value
        self._sync_controls(value)

    def _on_spin_changed(self, value):
        value = self._clamp_quality(value)
        self._quality = value
        self._sync_controls(value)

    def get_quality(self):
        return self._quality

    def _clamp_quality(self, value):
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = DEFAULT_IMAGE_QUALITY
        return max(10, min(100, value))


class SettingsDialog(QDialog):
    def __init__(self, parent, config):
        super().__init__(parent)
        self.setWindowTitle("系统设置")
        self.setWindowIcon(get_app_icon())
        self.resize(900, 600)
        self._hotkey_result = config.get("hotkeys", {}).copy()
        self._quality_value = int(config.get("image_quality", DEFAULT_IMAGE_QUALITY))
        self._ai_settings = _normalized_ai_settings(config.get("ai_settings", {}))
        self._general_settings = {
            "auto_save_enabled": config.get("auto_save_enabled", False),
            "save_dir": config.get("save_dir", ""),
            "auto_start_enabled": config.get("auto_start_enabled", False),
            "close_behavior": config.get("close_behavior", "tray"),
            "exit_unsaved_policy": config.get("exit_unsaved_policy", "save_all"),
        }
        layout = QVBoxLayout()

        header = QLabel("配置中心")
        header.setStyleSheet("font-size: 22px; font-weight: 600;")
        layout.addWidget(header)

        content_layout = QHBoxLayout()
        self.nav_list = QListWidget()
        self.nav_list.addItem("常规")
        self.nav_list.addItem("快捷键")
        self.nav_list.addItem("质量")
        self.nav_list.addItem("AI 设置")
        self.nav_list.setFixedWidth(170)
        self.nav_list.setStyleSheet(
            "QListWidget { border: 1px solid #e0e0e0; } "
            "QListWidget::item { padding: 12px; color: #1f2330; } "
            "QListWidget::item:selected { background: #eef4ff; color: #0c1d37; font-weight: 600; }"
        )
        content_layout.addWidget(self.nav_list)

        separator = QFrame()
        separator.setFrameShape(QFrame.VLine)
        separator.setFrameShadow(QFrame.Sunken)
        content_layout.addWidget(separator)

        self.stack = QStackedWidget()
        self.general_page = GeneralSettingsPage(
            self._general_settings["auto_save_enabled"],
            self._general_settings["save_dir"],
            self._general_settings["auto_start_enabled"],
            self._general_settings["close_behavior"],
            self._general_settings["exit_unsaved_policy"],
        )
        self.hotkey_page = HotkeySettingsPage(config.get("hotkeys", {}))
        self.quality_page = QualitySettingsPage(self._quality_value)
        self.ai_page = AISettingsPage(self._ai_settings)
        self.stack.addWidget(self.general_page)
        self.stack.addWidget(self.hotkey_page)
        self.stack.addWidget(self.quality_page)
        self.stack.addWidget(self.ai_page)
        content_layout.addWidget(self.stack, 1)

        layout.addLayout(content_layout)

        button_box = QHBoxLayout()
        button_box.addStretch()
        cancel_btn = QPushButton("取消")
        save_btn = QPushButton("保存设置")
        cancel_btn.clicked.connect(self.reject)
        save_btn.clicked.connect(self.accept)
        button_box.addWidget(cancel_btn)
        button_box.addWidget(save_btn)

        layout.addLayout(button_box)
        self.setLayout(layout)

        self.nav_list.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav_list.setCurrentRow(0)

    def accept(self):
        general_settings = self.general_page.get_settings()
        if general_settings["auto_save_enabled"] and not general_settings["save_dir"]:
            QMessageBox.warning(self, "自动保存", "启用自动保存前请先选择有效的保存目录。")
            return
        self._general_settings = general_settings
        self._hotkey_result = self.hotkey_page.get_hotkeys()
        self._quality_value = self.quality_page.get_quality()
        self._ai_settings = self.ai_page.get_settings()
        super().accept()

    def get_hotkeys(self):
        return self._hotkey_result

    def get_image_quality(self):
        return self._quality_value

    def get_general_settings(self):
        return self._general_settings

    def get_ai_settings(self):
        return self._ai_settings


class AISettingsPage(QWidget):
    def __init__(self, ai_settings, parent=None):
        super().__init__(parent)
        self._tester = None
        self._settings = _normalized_ai_settings(ai_settings or {})
        layout = QVBoxLayout()

        title = QLabel(u"\u914d\u7f6e AI \u622a\u56fe\u7ffb\u8bd1\u80fd\u529b")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(title)

        desc = QLabel(u"\u5728\u6b64\u8f93\u5165\u4f9b\u5e94\u5546\u63d0\u4f9b\u7684 Base URL\u3001API Key \u548c\u6a21\u578b\u540d\u79f0\uff0c\u53ef\u70b9\u51fb\u201c\u6d4b\u8bd5\u201d\u786e\u8ba4\u662f\u5426\u5177\u5907\u56fe\u50cf\u8bc6\u522b\u80fd\u529b\u3002")
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #4a4a4a;")
        layout.addWidget(desc)

        reminder = QLabel(
            u"\u63d0\u793a\uff1a\n"
            u"\u0031\u002e\u0020\u63a5\u53e3\u9700\u8981\u517c\u5bb9 OpenAI Chat Completions \u534f\u8bae\uff1b\n"
            u"\u0032\u002e\u0020\u6240\u9009\u6a21\u578b\u5fc5\u987b\u5177\u5907\u56fe\u50cf\u8bc6\u522b\uff08Vision\uff09\u80fd\u529b\u3002"
        )
        reminder.setWordWrap(True)
        reminder.setStyleSheet("color: #b7410e; font-size: 12px; background: rgba(255,200,0,0.15); padding:6px; border-radius:6px; margin-bottom:6px;")
        layout.addWidget(reminder)

        form = QFormLayout()
        self.base_url_edit = QLineEdit(self._settings.get("base_url", ""))
        self.base_url_edit.setPlaceholderText("例如：https://aihubmix.com/v1")
        form.addRow("Base URL", self.base_url_edit)

        self.model_edit = QLineEdit(self._settings.get("model", ""))
        self.model_edit.setPlaceholderText("例如：gpt-4.1-nano")
        form.addRow("模型名称", self.model_edit)

        api_row = QHBoxLayout()
        self.api_key_edit = QLineEdit(self._settings.get("api_key", ""))
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        api_row.addWidget(self.api_key_edit, 1)
        self.show_key_checkbox = QCheckBox("显示")
        self.show_key_checkbox.toggled.connect(self._toggle_api_key_visibility)
        api_row.addWidget(self.show_key_checkbox)
        form.addRow("API Key", api_row)
        layout.addLayout(form)

        self.test_button = QPushButton("测试图片识别")
        self.test_button.clicked.connect(self._on_test_clicked)
        layout.addWidget(self.test_button)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #5c6470;")
        layout.addWidget(self.status_label)
        layout.addStretch()
        self.setLayout(layout)

    def _toggle_api_key_visibility(self, checked):
        self.api_key_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)

    def _set_testing_state(self, running, message=""):
        self.test_button.setEnabled(not running)
        self.base_url_edit.setEnabled(not running)
        self.model_edit.setEnabled(not running)
        self.api_key_edit.setEnabled(not running)
        self.show_key_checkbox.setEnabled(not running)
        self.status_label.setText(message)

    def _cleanup_tester(self):
        if self._tester:
            self._tester.deleteLater()
            self._tester = None

    def _on_test_clicked(self):
        settings = self.get_settings()
        if not AITranslationService.is_configured(settings):
            QMessageBox.warning(self, "缺少配置", "请先填写 Base URL、API Key 和模型名称。")
            return
        if OpenAI is None:
            QMessageBox.warning(self, "缺少依赖", "未检测到 openai 包，请先运行 pip install openai。")
            return
        image_bytes = build_ai_test_image_bytes()
        self._set_testing_state(True, "正在通过示例图片测试，请稍候...")
        self._tester = AITestWorker(image_bytes, settings)
        self._tester.finished.connect(self._on_test_success)
        self._tester.failed.connect(self._on_test_failed)
        self._tester.start()

    def _on_test_success(self):
        self._set_testing_state(False, "测试通过：已成功识别示例图片。")
        self._cleanup_tester()

    def _on_test_failed(self, message):
        self._set_testing_state(False, f"测试失败：{message}")
        self._cleanup_tester()

    def get_settings(self):
        return {
            "base_url": self.base_url_edit.text().strip(),
            "api_key": self.api_key_edit.text().strip(),
            "model": self.model_edit.text().strip(),
        }


class TranslationTab(QWidget):
    def __init__(self, tab_index):
        super().__init__()
        layout = QVBoxLayout(self)
        self.splitter = QSplitter(Qt.Vertical)
        self.original_edit = QPlainTextEdit()
        self.original_edit.setReadOnly(True)
        self.original_edit.setPlaceholderText("等待识别原文...")
        self.translation_edit = QPlainTextEdit()
        self.translation_edit.setReadOnly(True)
        self.translation_edit.setPlaceholderText("等待翻译结果...")
        self.splitter.addWidget(self.original_edit)
        self.splitter.addWidget(self.translation_edit)
        layout.addWidget(self.splitter)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self.copy_translation_btn = QPushButton("复制译文")
        self.copy_translation_btn.setEnabled(False)
        self.copy_translation_btn.clicked.connect(self._copy_translation)
        self.copy_original_btn = QPushButton("复制原文")
        self.copy_original_btn.setEnabled(False)
        self.copy_original_btn.clicked.connect(self._copy_original)
        self.copy_both_btn = QPushButton("复制原文+译文")
        self.copy_both_btn.setEnabled(False)
        self.copy_both_btn.clicked.connect(self._copy_combined)
        button_row.addWidget(self.copy_translation_btn)
        button_row.addWidget(self.copy_original_btn)
        button_row.addWidget(self.copy_both_btn)
        layout.addLayout(button_row)

    def set_texts(self, original, translation):
        self.original_edit.setPlainText(original or "(未识别到文字)")
        self.translation_edit.setPlainText(translation or "(未获得翻译)")
        has_original = bool(original)
        has_translation = bool(translation)
        self.copy_original_btn.setEnabled(has_original)
        self.copy_translation_btn.setEnabled(has_translation)
        self.copy_both_btn.setEnabled(has_original or has_translation)

    def _copy_translation(self):
        _set_rich_clipboard(self.translation_edit.toPlainText())

    def _copy_original(self):
        _set_rich_clipboard(self.original_edit.toPlainText())

    def _copy_combined(self):
        original = self.original_edit.toPlainText()
        translation = self.translation_edit.toPlainText()
        combined = original or ""
        if translation:
            if combined:
                combined = f"{combined}\n\n[{translation}]"
            else:
                combined = f"[{translation}]"
        _set_rich_clipboard(combined)


class AITranslationPanel(QWidget):
    translationCompleted = pyqtSignal()
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        header = QLabel("AI 截图翻译")
        header.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(header)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.status_label = QLabel()
        self.status_label.setStyleSheet("color: #5c6470;")
        layout.addWidget(self.status_label)
        self.setLayout(layout)
        self._ai_settings = {}
        self._tab_counter = 0
        self._workers = []

    def set_ai_settings(self, settings):
        self._ai_settings = _normalized_ai_settings(settings or {})

    def add_capture(self, pixmap: QPixmap):
        # region agent log
        # H7: 只要这里被调用，就等同于“开始 AI 识别/翻译”（用户会感觉在自动 OCR）
        _agent_debug_log(
            hypothesisId="H7",
            location="screenshot_tool.py:AITranslationPanel.add_capture",
            message="AITranslationPanel.add_capture called",
            data={
                "configured": bool(AITranslationService.is_configured(self._ai_settings)),
                "pixmap_isNull": bool(pixmap.isNull()) if pixmap else None,
                "pixmap_size": (pixmap.width(), pixmap.height()) if pixmap else None,
            },
        )
        # endregion
        if not AITranslationService.is_configured(self._ai_settings):
            self.status_label.setText("请先在系统设置 -> AI 设置中填写 base_url/API Key/模型。")
            return
        try:
            image_bytes = pixmap_to_png_bytes(pixmap)
        except Exception as exc:
            self.status_label.setText(f"图片处理失败：{exc}")
            return
        # region agent log
        _agent_debug_log(
            hypothesisId="H7",
            location="screenshot_tool.py:AITranslationPanel.add_capture",
            message="AITranslationPanel prepared image bytes",
            data={"image_bytes_len": len(image_bytes) if image_bytes else 0},
        )
        # endregion
        self._tab_counter += 1
        tab = TranslationTab(self._tab_counter)
        title = f"{self._tab_counter}. 识别中"
        self.tabs.addTab(tab, title)
        self.tabs.setCurrentWidget(tab)
        self.status_label.setText("AI 正在识别并翻译截图...")
        worker = AITranslationWorker(image_bytes, self._ai_settings)
        start_time = time.perf_counter()
        worker.completed.connect(
            lambda original, translation, tab=tab, start=start_time: self._on_translation_success(
                tab, original, translation, start
            )
        )
        worker.failed.connect(
            lambda message, tab=tab, start=start_time: self._on_translation_failure(tab, message, start)
        )
        worker.finished.connect(lambda w=worker: self._cleanup_worker(w))
        self._workers.append(worker)
        worker.start()

    def _on_translation_success(self, tab, original, translation, start_time=None):
        tab.set_texts(original, translation)
        idx = self.tabs.indexOf(tab)
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.tabs.setTabText(idx, f"{idx + 1}. {timestamp}")
        duration_text = ""
        if start_time is not None:
            duration = time.perf_counter() - start_time
            duration_text = f"（消耗时间: {duration:.2f}秒）"
        if translation:
            if _set_clipboard_text_with_retry(translation):
                self.status_label.setText(
                    f"识别完成，译文已复制到剪贴板。{duration_text}"
                )
            else:
                self.status_label.setText(
                    f"识别完成，但写入剪贴板失败，请手动复制。{duration_text}"
                )
        else:
            self.status_label.setText(
                f"识别完成，请手动复制需要的内容。{duration_text}"
            )
        self.translationCompleted.emit()

    def _on_translation_failure(self, tab, message, start_time=None):
        idx = self.tabs.indexOf(tab)
        if idx >= 0:
            self.tabs.setTabText(idx, f"{idx + 1}. 失败")
        duration_text = ""
        if start_time is not None:
            duration = time.perf_counter() - start_time
            duration_text = f"（消耗时间: {duration:.2f}秒）"
        self.status_label.setText(f"AI 识别失败：{message}{duration_text}")

    def _cleanup_worker(self, worker):
        if worker in self._workers:
            self._workers.remove(worker)
            worker.deleteLater()
class ActionButton(QPushButton):
    def __init__(self, title, subtitle="", callback=None, enabled=True):
        super().__init__()
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(70)
        self.setText(f"{title}\n{subtitle}" if subtitle else title)
        self.setEnabled(enabled)
        self.setStyleSheet(
            "QPushButton { text-align: left; padding: 12px 16px; border: 1px solid #dfe3eb; border-radius: 8px; "
            "background: #ffffff; font-size: 14px; }"
            "QPushButton:hover { background: #f5f7fb; border-color: #a8c3ff; }"
            "QPushButton:disabled { color: #999999; border-style: dashed; }"
        )
        if callback:
            self.clicked.connect(callback)


class HomePage(QWidget):
    openFolderRequested = pyqtSignal()
    captureRequested = pyqtSignal()
    repeatRequested = pyqtSignal()
    aiTranslateRequested = pyqtSignal()
    openSettingsRequested = pyqtSignal()
    openWorkspaceRequested = pyqtSignal()
    openImagesRequested = pyqtSignal()

    def __init__(self, save_dir):
        super().__init__()
        self._save_dir = save_dir
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout()
        title = QLabel("选择操作…")
        title.setStyleSheet("font-size: 24px; font-weight: 700; margin-bottom: 8px;")
        layout.addWidget(title)

        content_layout = QHBoxLayout()

        new_task_layout = QVBoxLayout()
        new_task_label = QLabel("新任务")
        new_task_label.setStyleSheet("font-size: 18px; font-weight: 600;")
        new_task_layout.addWidget(new_task_label)
        new_task_layout.addWidget(ActionButton("导入图片", "选择已有文件开始标注", self.openImagesRequested.emit))
        new_task_layout.addStretch()
        content_layout.addLayout(new_task_layout, 1)

        capture_layout = QVBoxLayout()
        capture_label = QLabel("截取屏幕")
        capture_label.setStyleSheet("font-size: 18px; font-weight: 600;")
        capture_layout.addWidget(capture_label)
        capture_layout.addWidget(ActionButton("区域截图", "选择屏幕区域", self.captureRequested.emit))
        self.repeat_button = ActionButton("重复上次截取", "使用上一次选择的矩形区域", self.repeatRequested.emit, enabled=False)
        self.ai_button = ActionButton("AI 截图翻译", "选取区域后AI自动识别并翻译", self.aiTranslateRequested.emit)
        capture_layout.addWidget(self.ai_button)
        capture_layout.addWidget(self.repeat_button)
        capture_layout.addStretch()
        content_layout.addLayout(capture_layout, 1)

        tools_layout = QVBoxLayout()
        tools_label = QLabel("标注管理")
        tools_label.setStyleSheet("font-size: 18px; font-weight: 600;")
        tools_layout.addWidget(tools_label)
        tools_layout.addWidget(ActionButton("打开保存目录", "快速查看文件", self.openFolderRequested.emit))
        tools_layout.addWidget(ActionButton("系统设置", "热键、自定义流程", self.openSettingsRequested.emit))
        tools_layout.addWidget(ActionButton("标注工作台", "查看历史截图并继续标注", self.openWorkspaceRequested.emit))
        self.hotkey_summary_label = QLabel()
        self.hotkey_summary_label.setWordWrap(True)
        self.hotkey_summary_label.setStyleSheet("color: #5f6b7c; font-size: 12px;")
        tools_layout.addWidget(self.hotkey_summary_label)
        tools_layout.addStretch()
        content_layout.addLayout(tools_layout, 1)

        layout.addLayout(content_layout)
        layout.addStretch()
        self.setLayout(layout)
    def set_repeat_enabled(self, enabled):
        self.repeat_button.setEnabled(enabled)

    def set_hotkey_summary(self, text):
        self.hotkey_summary_label.setText(text)

    def set_ai_translate_enabled(self, enabled):
        self.ai_button.setEnabled(enabled)


class ComingSoonPage(QWidget):
    def __init__(self, title):
        super().__init__()
        layout = QVBoxLayout()
        label = QLabel(f"{title}\n\n该功能即将上线，敬请期待。")
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet("font-size: 18px; color: #5f6b7c;")
        layout.addStretch()
        layout.addWidget(label)
        layout.addStretch()
        self.setLayout(layout)


class AboutPage(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        title = QLabel("CTK Snapshot - 关于")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        intro = QLabel(
            "CTK Snapshot 是一款面向个人效率的截图标注工具，"
            "提供区域截取、批量导入、矩形/顺序标记、放大镜等体验。"
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #4a5568; font-size: 14px;")
        author = QLabel("作者：乾颐堂（现任明教教主）")
        author.setStyleSheet("font-size: 16px; font-weight: 600; margin-top: 12px;")
        version = QLabel("版本：1.1.0")
        version.setStyleSheet("font-size: 14px; color: #5f6b7c;")
        contact = QLabel("反馈邮箱：collinsctk@qytang.com\n如有 Bug 或建议请邮件联系。")
        contact.setWordWrap(True)
        contact.setStyleSheet("font-size: 14px; color: #4a5568; margin-top: 12px;")
        layout.addWidget(title)
        layout.addSpacing(8)
        layout.addWidget(intro)
        layout.addWidget(author)
        layout.addWidget(version)
        layout.addWidget(contact)
        layout.addStretch()
        self.setLayout(layout)


class AnnotationCanvas(QWidget):
    optionsUpdated = pyqtSignal()
    zoomChanged = pyqtSignal(float)

    HANDLE_SIZE = 12
    MIN_RECT_SIZE = 8
    CANVAS_PADDING = 8  # 最小边距，确保边缘控制柄可以被点击

    def __init__(self, pixmap: QPixmap):
        super().__init__()
        if pixmap.isNull():
            self.base_pixmap = QPixmap()
        else:
            image = pixmap.toImage().convertToFormat(QImage.Format_ARGB32_Premultiplied)
            self.base_pixmap = QPixmap.fromImage(image)
        self._zoom = 1.0
        self._min_zoom = 0.25
        self._max_zoom = 4.0
        self.setMouseTracking(True)
        self.rectangles = []
        self.markers = []
        self.tool = Tool.NONE
        self.marker_fill_color = QColor(DEFAULT_MARKER_STYLE["fill"])
        self.marker_border_color = QColor(DEFAULT_MARKER_STYLE["border"])
        self.marker_border_enabled = DEFAULT_MARKER_STYLE["border_enabled"]
        self.marker_size = DEFAULT_MARKER_STYLE["size"]
        self.marker_font_ratio = DEFAULT_MARKER_STYLE["font_ratio"]
        self.next_marker_number = DEFAULT_MARKER_STYLE.get("next_number", 1)
        self.lines = []
        self.arrows = []
        self.selected_line_index = None
        self.selected_arrow_index = None
        self._shape_drag_mode = None
        self._shape_drag_handle = None
        self._shape_drag_origin = None
        self._shape_initial_data = None
        self._current_shape = None  # 用于拖拽创建中的形状
        
        self.dragging_marker_index = None
        self.selected_marker_index = None
        self.hover_marker_index = None
        self.markers_flattened = True
        self.rectangle_fill_color = QColor(DEFAULT_RECT_STYLE["fill"])
        self.rectangle_border_color = QColor(DEFAULT_RECT_STYLE["border"])
        self.rectangle_border_enabled = DEFAULT_RECT_STYLE["border_enabled"]
        self.rectangle_border_width = DEFAULT_RECT_STYLE["width"]
        self.rectangles_flattened = True
        self.line_color = QColor("#5f27cd")
        self.line_width = 3
        self.arrow_color = QColor("#ff4757")
        self.arrow_width = 3
        
        # 图片边框设置
        self.image_border_color = QColor(DEFAULT_IMAGE_BORDER_STYLE["color"])
        self.image_border_width = DEFAULT_IMAGE_BORDER_STYLE["width"]
        
        self.selected_rectangle_index = None
        self.rect_drag_mode = None
        self.rect_drag_handle = None
        self.rect_initial_rect = QRect()
        self.rect_drag_origin = QPoint()
        self.creating_new_rect = False
        self.creating_rect_origin = QPoint()
        self._checker_brush = None
        self._undo_stack = []
        self._marker_dragging = False
        self._text_dragging = False
        self._text_drag_index = None
        self._text_drag_offset = QPoint()
        self.text_items = []
        self.text_color = QColor(DEFAULT_TEXT_STYLE["color"])
        self.text_background_color = QColor(DEFAULT_TEXT_STYLE["background"])
        self.text_font_family = DEFAULT_TEXT_STYLE["font"]
        self.text_font_size = DEFAULT_TEXT_STYLE["size"]
        self.owner_tab = None
        self.selected_text_index = None
        self.selection_rect = None
        self._selection_origin = None
        self._selection_dragging = False
        self._selection_drag_mode = None  # "move" or "resize"
        self._selection_handle = None
        self._selection_initial_rect = None
        self._selection_offset = QPoint()
        self.selection_fill_color = QColor("#FFFFFFFF")
        
        # 渲染优化：引入离屏缓存层
        self._backing_store = QPixmap()
        self._backing_store_dirty = True
        
        self._apply_zoom()

    def update(self):
        """重写 update 方法，确保每次更新都会重新渲染缓存层"""
        self._backing_store_dirty = True
        super().update()

    def zoom_factor(self):
        return self._zoom

    def set_zoom(self, factor: float):
        factor = max(self._min_zoom, min(self._max_zoom, factor))
        if abs(factor - self._zoom) < 0.001:
            return
        self._zoom = factor
        self._apply_zoom()
        self.zoomChanged.emit(self._zoom)

    def zoom_in(self):
        self.set_zoom(self._zoom * 1.1)

    def zoom_out(self):
        self.set_zoom(self._zoom / 1.1)

    def reset_zoom(self):
        self.set_zoom(1.0)

    def _scaled_size(self):
        # 基础缩放大小
        sw = max(1, int(round(self.base_pixmap.width() * self._zoom)))
        sh = max(1, int(round(self.base_pixmap.height() * self._zoom)))
        # 加上两倍边距（左右各一，上下各一）
        pad = int(self.CANVAS_PADDING * 2 * self._zoom)
        return QSize(sw + pad, sh + pad)

    def _apply_zoom(self):
        # 画布总大小 = (原图 + 两倍边距) * 缩放
        pad = self.CANVAS_PADDING
        final_w = int(round((self.base_pixmap.width() + pad * 2) * self._zoom))
        final_h = int(round((self.base_pixmap.height() + pad * 2) * self._zoom))
        self.setFixedSize(final_w, final_h)
        self.update()

    def _view_to_scene(self, point: QPoint):
        if self._zoom == 0:
            return QPoint(point)
        # 考虑到画布边距偏移：(鼠标位置 / 缩放) - 边距
        pad = self.CANVAS_PADDING
        return QPoint(
            int(round(point.x() / self._zoom - pad)),
            int(round(point.y() / self._zoom - pad)),
        )
        # 减去边距偏移后再进行缩放映射
        pad = self.CANVAS_PADDING * self._zoom
        rx = (point.x() - pad) / self._zoom
        ry = (point.y() - pad) / self._zoom
        return QPoint(int(round(rx)), int(round(ry)))

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y() or event.pixelDelta().y()
            if delta > 0:
                self.zoom_in()
            elif delta < 0:
                self.zoom_out()
            event.accept()
            return
        event.ignore()
        super().wheelEvent(event)

    def set_tool(self, tool: Tool):
        self.tool = tool
        self._reset_rect_drag()
        if tool != Tool.MARKER:
            self._set_hover_marker(None)
        if tool == Tool.CROP:
            # 进入裁切模式时，默认选中整个图片，用户可以拖动控制柄调整
            self.selection_rect = self.base_pixmap.rect()
            # region agent log
            _agent_debug_log(
                hypothesisId="H3",
                location="screenshot_tool.py:AnnotationCanvas.set_tool",
                message="entering CROP mode (full image selected)",
                data={
                    "selection_rect": [self.selection_rect.x(), self.selection_rect.y(), self.selection_rect.width(), self.selection_rect.height()],
                    "pixmap_rect": [self.base_pixmap.rect().x(), self.base_pixmap.rect().y(), self.base_pixmap.rect().width(), self.base_pixmap.rect().height()],
                },
            )
            # endregion
        else:
            self.clear_active_selection()
        self._update_default_cursor()
        self.update()

    def clear_annotations(self):
        self._push_undo_state()
        self.rectangles.clear()
        self.markers.clear()
        self.lines.clear()
        self.arrows.clear()
        self.next_marker_number = 1
        self.markers_flattened = True
        self.rectangles_flattened = True
        self.dragging_marker_index = None
        self.selected_marker_index = None
        self.hover_marker_index = None
        self.selected_rectangle_index = None
        self.text_items.clear()
        self.selected_text_index = None
        self._reset_rect_drag()
        self._marker_dragging = False
        self._mark_backing_store_dirty()
        self.optionsUpdated.emit()

    def _has_active_marker(self):
        return (
            self.selected_marker_index is not None
            and not self.markers_flattened
            and 0 <= self.selected_marker_index < len(self.markers)
        )

    def _has_active_text(self):
        return (
            self.selected_text_index is not None
            and 0 <= self.selected_text_index < len(self.text_items)
        )

    def set_marker_color(self, color: QColor):
        if color.isValid():
            self._push_undo_state()
            self.marker_fill_color = color
            if self._has_active_marker():
                self.markers[self.selected_marker_index]['fill'] = QColor(color)
            self.update()
            self.optionsUpdated.emit()

    def set_marker_size(self, size: int):
        self._push_undo_state()
        self.marker_size = max(10, min(120, size))
        if self._has_active_marker():
            self.markers[self.selected_marker_index]['size'] = self.marker_size
        self.update()
        self.optionsUpdated.emit()

    def set_next_marker_number(self, number: int):
        self._push_undo_state()
        self.next_marker_number = max(1, number)
        self.optionsUpdated.emit()

    def set_current_marker_number(self, number: int):
        if self._has_active_marker():
            self._push_undo_state()
            self.markers[self.selected_marker_index]['number'] = max(1, number)
            self.update()
            self.optionsUpdated.emit()

    def set_marker_border_enabled(self, enabled: bool):
        self._push_undo_state()
        self.marker_border_enabled = enabled
        if self._has_active_marker():
            self.markers[self.selected_marker_index]['border_enabled'] = enabled
        self.update()
        self.optionsUpdated.emit()

    def set_marker_border_color(self, color: QColor):
        if color.isValid():
            self._push_undo_state()
            self.marker_border_color = color
            if self._has_active_marker():
                self.markers[self.selected_marker_index]['border_color'] = QColor(color)
            self.update()
            self.optionsUpdated.emit()

    def set_marker_font_ratio(self, ratio: float):
        self._push_undo_state()
        self.marker_font_ratio = max(0.3, min(1.2, ratio))
        if self._has_active_marker():
            self.markers[self.selected_marker_index]['font_ratio'] = self.marker_font_ratio
        self.update()
        self.optionsUpdated.emit()

    def flatten_markers(self):
        self._push_undo_state()
        self.dragging_marker_index = None
        self.markers_flattened = True
        self.selected_marker_index = None
        self.hover_marker_index = None
        self.next_marker_number = 1
        self.optionsUpdated.emit()

    def duplicate_marker(self):
        if not self._has_active_marker():
            return False
        self._push_undo_state()
        marker = dict(self.markers[self.selected_marker_index])
        marker['pos'] = marker['pos'] + QPoint(12, 12)
        self.markers.append(marker)
        self.selected_marker_index = len(self.markers) - 1
        self.dragging_marker_index = self.selected_marker_index
        self.selected_rectangle_index = None
        self.rect_drag_mode = None
        self._set_hover_marker(None)
        self.markers_flattened = False
        self.update()
        self.optionsUpdated.emit()
        return True

    def _has_active_rectangle(self):
        return (
            self.selected_rectangle_index is not None
            and not self.rectangles_flattened
            and 0 <= self.selected_rectangle_index < len(self.rectangles)
            and not self.rectangles[self.selected_rectangle_index]['flattened']
        )

    def set_rectangle_fill_color(self, color: QColor):
        if color.isValid():
            self._push_undo_state()
            self.rectangle_fill_color = color
            if self._has_active_rectangle():
                self.rectangles[self.selected_rectangle_index]['fill'] = QColor(color)
            self.update()
            self.optionsUpdated.emit()

    def set_rectangle_border_color(self, color: QColor):
        if color.isValid():
            self._push_undo_state()
            self.rectangle_border_color = color
            if self._has_active_rectangle():
                self.rectangles[self.selected_rectangle_index]['border'] = QColor(color)
            self.update()
            self.optionsUpdated.emit()

    def set_rectangle_border_width(self, width: int):
        self._push_undo_state()
        self.rectangle_border_width = max(1, min(20, width))
        if self._has_active_rectangle():
            self.rectangles[self.selected_rectangle_index]['width'] = self.rectangle_border_width
        self.update()
        self.optionsUpdated.emit()

    def set_rectangle_corner_radius(self, radius: int):
        self._push_undo_state()
        self.rectangle_corner_radius = max(0, min(60, radius))
        if self._has_active_rectangle():
            self.rectangles[self.selected_rectangle_index]['radius'] = self.rectangle_corner_radius
        self.update()
        self.optionsUpdated.emit()

    def set_rectangle_border_enabled(self, enabled: bool):
        self._push_undo_state()
        self.rectangle_border_enabled = enabled
        if self._has_active_rectangle():
            self.rectangles[self.selected_rectangle_index]['border_enabled'] = enabled
        self.update()
        self.optionsUpdated.emit()

    def flatten_rectangle(self):
        if self._has_active_rectangle():
            self._push_undo_state()
            self.rectangles[self.selected_rectangle_index]['flattened'] = True
            self.selected_rectangle_index = None
            if all(r['flattened'] for r in self.rectangles):
                self.rectangles_flattened = True
            self.update()
            self.optionsUpdated.emit()

    def duplicate_rectangle(self):
        if not self._has_active_rectangle():
            return False
        self._push_undo_state()
        info = dict(self.rectangles[self.selected_rectangle_index])
        info['rect'] = info['rect'].translated(12, 12)
        info['flattened'] = False
        self.rectangles.append(info)
        self.selected_rectangle_index = len(self.rectangles) - 1
        self.selected_marker_index = None
        self.dragging_marker_index = None
        self._set_hover_marker(None)
        self.rectangles_flattened = False
        self.update()
        self.optionsUpdated.emit()
        return True

    def duplicate_active_shape(self):
        kind = self.active_selection_kind()
        if kind == "rectangle":
            return self.duplicate_rectangle()
        if kind == "marker":
            return self.duplicate_marker()
        if kind == "line":
            if self.selected_line_index is not None and self.selected_line_index < len(self.lines):
                self._push_undo_state()
                info = self._clone_line(self.lines[self.selected_line_index])
                info['start'] += QPoint(15, 15)
                info['end'] += QPoint(15, 15)
                self.lines.append(info)
                self.selected_line_index = len(self.lines) - 1
                self.update()
                self.optionsUpdated.emit()
                return True
        if kind == "arrow":
            if self.selected_arrow_index is not None and self.selected_arrow_index < len(self.arrows):
                self._push_undo_state()
                info = self._clone_arrow(self.arrows[self.selected_arrow_index])
                info['start'] += QPoint(15, 15)
                info['end'] += QPoint(15, 15)
                self.arrows.append(info)
                self.selected_arrow_index = len(self.arrows) - 1
                self.update()
                self.optionsUpdated.emit()
                return True
        return False

    def apply_style_defaults(self, marker_style, rect_style, text_style=None, image_border_style=None):
        if marker_style:
            self.marker_fill_color = QColor(marker_style.get("fill", DEFAULT_MARKER_STYLE["fill"]))
            self.marker_border_color = QColor(marker_style.get("border", DEFAULT_MARKER_STYLE["border"]))
            self.marker_border_enabled = marker_style.get("border_enabled", DEFAULT_MARKER_STYLE["border_enabled"])
            self.marker_size = marker_style.get("size", DEFAULT_MARKER_STYLE["size"])
            self.marker_font_ratio = marker_style.get("font_ratio", DEFAULT_MARKER_STYLE["font_ratio"])
        self.next_marker_number = 1
        if rect_style:
            self.rectangle_fill_color = QColor(rect_style.get("fill", DEFAULT_RECT_STYLE["fill"]))
            self.rectangle_border_color = QColor(rect_style.get("border", DEFAULT_RECT_STYLE["border"]))
            self.rectangle_border_enabled = rect_style.get("border_enabled", DEFAULT_RECT_STYLE["border_enabled"])
            self.rectangle_border_width = rect_style.get("width", DEFAULT_RECT_STYLE["width"])
            self.rectangle_corner_radius = rect_style.get("radius", DEFAULT_RECT_STYLE["radius"])
        if text_style:
            self.apply_text_style_defaults(text_style)
        if image_border_style:
            self.image_border_color = QColor(image_border_style.get("color", DEFAULT_IMAGE_BORDER_STYLE["color"]))
            self.image_border_width = image_border_style.get("width", DEFAULT_IMAGE_BORDER_STYLE["width"])
        self.optionsUpdated.emit()
        self.update()

    def apply_text_style_defaults(self, text_style):
        style = text_style or {}
        color = QColor(style.get("color", DEFAULT_TEXT_STYLE["color"]))
        background = QColor(style.get("background", DEFAULT_TEXT_STYLE["background"]))
        font_family = _sanitize_font_family(style.get("font", DEFAULT_TEXT_STYLE["font"]))
        try:
            size = int(style.get("size", DEFAULT_TEXT_STYLE["size"]))
        except (TypeError, ValueError):
            size = DEFAULT_TEXT_STYLE["size"]
        self.text_color = color
        self.text_background_color = background
        self.text_font_family = font_family or DEFAULT_TEXT_STYLE["font"]
        self.text_font_size = size
        self.update()
        self.optionsUpdated.emit()

    def apply_text_style(self, style):
        if not style:
            return
        color = QColor(style.get("color", self.text_color))
        bg = QColor(style.get("background", self.text_background_color))
        font_family = _sanitize_font_family(style.get("font", self.text_font_family))
        try:
            font_size = int(style.get("font_size", style.get("size", self.text_font_size)))
        except (TypeError, ValueError):
            font_size = self.text_font_size
        self.text_color = color
        self.text_background_color = bg
        self.text_font_family = font_family
        self.text_font_size = font_size
        self.optionsUpdated.emit()
        self.update()

    def marker_style_state(self):
        return {
            "fill": self.marker_fill_color.name(QColor.HexArgb),
            "border": self.marker_border_color.name(QColor.HexArgb),
            "border_enabled": self.marker_border_enabled,
            "size": self.marker_size,
            "font_ratio": self.marker_font_ratio,
            "next_number": self.next_marker_number,
        }

    def rectangle_style_state(self):
        return {
            "fill": self.rectangle_fill_color.name(QColor.HexArgb),
            "border": self.rectangle_border_color.name(QColor.HexArgb),
            "border_enabled": self.rectangle_border_enabled,
            "width": self.rectangle_border_width,
            "radius": self.rectangle_corner_radius,
        }

    def set_text_color(self, color: QColor):
        if not color or not color.isValid():
            return
        if self.text_color == color:
            return  # 值未变化，不推送 undo
        self._push_undo_state()
        self.text_color = QColor(color)
        if self._has_active_text():
            self.text_items[self.selected_text_index]["color"] = QColor(color)
        self.optionsUpdated.emit()
        self.update()

    def set_text_font_size(self, size: int):
        try:
            size = int(size)
        except (TypeError, ValueError):
            return
        clamped = max(8, min(72, size))
        if abs(clamped - self.text_font_size) < 0.1:
            return
        self._push_undo_state()
        self.text_font_size = clamped
        if self._has_active_text():
            self.text_items[self.selected_text_index]["font_size"] = clamped
        self.optionsUpdated.emit()
        self.update()

    def set_text_font_family(self, family: str):
        family = _sanitize_font_family(family)
        if not family:
            return
        if self.text_font_family == family:
            return  # 值未变化，不推送 undo
        self._push_undo_state()
        self.text_font_family = family
        if self._has_active_text():
            self.text_items[self.selected_text_index]["font_family"] = family
        self.optionsUpdated.emit()
        self.update()

    def set_text_background_color(self, color: QColor):
        if not color or not color.isValid():
            return
        if self.text_background_color == color:
            return  # 值未变化，不推送 undo
        self._push_undo_state()
        self.text_background_color = QColor(color)
        if self._has_active_text():
            self.text_items[self.selected_text_index]["background"] = QColor(color)
        self.optionsUpdated.emit()
        self.update()

    def update_selected_text_color(self, color: QColor):
        if not color or not color.isValid() or not self._has_active_text():
            return
        self._push_undo_state()
        item = self.text_items[self.selected_text_index]
        item["color"] = QColor(color)
        self.text_color = QColor(color)
        self.optionsUpdated.emit()
        self.update()

    def update_selected_shape_color(self, color: QColor):
        if not color or not color.isValid():
            return
        # 只有在有选中形状时才更新并推送 undo
        has_selection = (self.selected_line_index is not None or 
                         self.selected_arrow_index is not None)
        if not has_selection:
            return
            
        self._push_undo_state()
        if self.selected_line_index is not None:
            self.lines[self.selected_line_index]['color'] = QColor(color)
            self.line_color = QColor(color)
        elif self.selected_arrow_index is not None:
            self.arrows[self.selected_arrow_index]['color'] = QColor(color)
            self.arrow_color = QColor(color)
        self.optionsUpdated.emit()
        self.update()

    def update_selected_shape_width(self, width: int):
        has_selection = (self.selected_line_index is not None or 
                         self.selected_arrow_index is not None)
        if not has_selection:
            return
        self._push_undo_state()
        if self.selected_line_index is not None:
            self.lines[self.selected_line_index]['width'] = width
            self.line_width = width
        elif self.selected_arrow_index is not None:
            self.arrows[self.selected_arrow_index]['width'] = width
            self.arrow_width = width
        self.optionsUpdated.emit()
        self.update()

    def update_selected_text_background(self, color: QColor):
        if not color or not color.isValid() or not self._has_active_text():
            return
        self._push_undo_state()
        item = self.text_items[self.selected_text_index]
        item["background"] = QColor(color)
        self.text_background_color = QColor(color)
        self.optionsUpdated.emit()
        self.update()

    def update_selected_text_font_family(self, family: str):
        if not self._has_active_text():
            return
        family = _sanitize_font_family(family)
        if not family:
            return
        self._push_undo_state()
        item = self.text_items[self.selected_text_index]
        item["font_family"] = family
        self.text_font_family = family
        self.optionsUpdated.emit()
        self.update()

    def update_selected_text_font_size(self, size: int):
        if not self._has_active_text():
            return
        try:
            size = int(size)
        except (TypeError, ValueError):
            return
        size = max(8, min(72, size))
        self._push_undo_state()
        item = self.text_items[self.selected_text_index]
        item["font_size"] = size
        self.text_font_size = size
        self.optionsUpdated.emit()
        self.update()

    def text_style_state(self):
        return {
            "color": self.text_color.name(QColor.HexArgb),
            "font": self.text_font_family,
            "size": self.text_font_size,
            "background": self.text_background_color.name(QColor.HexArgb),
        }

    def active_selection_kind(self):
        if self.selection_rect:
            return "pixel_selection"
        if self._has_active_text():
            return "text"
        if self._has_active_marker():
            return "marker"
        if self._has_active_rectangle():
            return "rectangle"
        if self.selected_line_index is not None:
            return "line"
        if self.selected_arrow_index is not None:
            return "arrow"
        if self.tool == Tool.LINE:
            return "line"
        if self.tool == Tool.ARROW:
            return "arrow"
        return "none"

    def clear_active_selection(self, emit=True):
        changed = False
        if self.selection_rect is not None:
            self.selection_rect = None
            self._selection_origin = None
            self._selection_dragging = False
            self._selection_drag_mode = None
            self._selection_handle = None
            self._selection_initial_rect = None
            changed = True
        if self.selected_text_index is not None:
            self.selected_text_index = None
            changed = True
            self._text_dragging = False
            self._text_drag_index = None
            self._text_drag_offset = QPoint()
        if self.selected_marker_index is not None:
            self.selected_marker_index = None
            self.dragging_marker_index = None
            changed = True
        if self.selected_rectangle_index is not None:
            self.selected_rectangle_index = None
            self.rect_drag_mode = None
            self.rect_drag_handle = None
            self.rect_initial_rect = QRect()
            self.rect_drag_origin = QPoint()
            self.creating_new_rect = False
            changed = True
        if self.hover_marker_index is not None:
            self._set_hover_marker(None)
            changed = True
        
        # 清除图形选中状态
        if self.selected_line_index is not None:
            self.selected_line_index = None
            changed = True
        if self.selected_arrow_index is not None:
            self.selected_arrow_index = None
            changed = True
            
        if changed:
            self._update_default_cursor()
            if emit:
                self.optionsUpdated.emit()
                self._notify_text_selection()
        # region agent log
        # H1/H2: Esc 退出失败时，是否存在“拖拽状态未复位/焦点不在预期控件”的证据
        try:
            focus = QApplication.focusWidget()
            focus_name = focus.__class__.__name__ if focus else None
        except Exception:
            focus_name = None
        _agent_debug_log(
            hypothesisId="H1",
            location="screenshot_tool.py:AnnotationCanvas.clear_active_selection",
            message="clear_active_selection called",
            data={
                "changed": changed,
                "emit": bool(emit),
                "tool": getattr(self, "tool", None).name if getattr(self, "tool", None) else None,
                "markers_flattened": bool(getattr(self, "markers_flattened", True)),
                "selected_marker_index": getattr(self, "selected_marker_index", None),
                "dragging_marker_index": getattr(self, "dragging_marker_index", None),
                "_marker_dragging": bool(getattr(self, "_marker_dragging", False)),
                "_text_dragging": bool(getattr(self, "_text_dragging", False)),
                "focusWidget": focus_name,
            },
        )
        # endregion
        self.update()
        return changed

    def _selection_bounds(self):
        if self.selection_rect is None:
            return None
        rect = QRect(self.selection_rect).normalized()
        bounds = QRect(0, 0, self.base_pixmap.width(), self.base_pixmap.height())
        rect = rect.intersected(bounds)
        if rect.width() < 1 or rect.height() < 1:
            return None
        return rect

    def _clamp_rect_to_bounds(self, rect: QRect, bounds: QRect, min_size=2):
        if rect is None:
            return None
        rect = QRect(rect).normalized()
        rect = rect.intersected(bounds)
        if rect.isNull():
            return None
        if rect.width() < min_size:
            rect.setWidth(min_size)
            if rect.right() > bounds.right():
                rect.moveRight(bounds.right())
            if rect.left() < bounds.left():
                rect.moveLeft(bounds.left())
        if rect.height() < min_size:
            rect.setHeight(min_size)
            if rect.bottom() > bounds.bottom():
                rect.moveBottom(bounds.bottom())
            if rect.top() < bounds.top():
                rect.moveTop(bounds.top())
        rect = rect.intersected(bounds)
        return rect if not rect.isNull() else None

    def _selection_handle_rects(self, for_hit=False):
        if not self.selection_rect:
            return []
        rect = QRect(self.selection_rect).normalized()
        size = SELECTION_HANDLE_HIT_SIZE if for_hit else SELECTION_HANDLE_SIZE
        half = size // 2
        points = [rect.topLeft(), rect.topRight(), rect.bottomLeft(), rect.bottomRight()]
        return [QRect(p.x() - half, p.y() - half, size, size) for p in points]

    def _selection_handle_hit_test(self, pos: QPoint):
        handles = ['top-left', 'top-right', 'bottom-left', 'bottom-right']
        handle_rects = self._selection_handle_rects(for_hit=True)
        # region agent log
        _agent_debug_log(
            hypothesisId="H2",
            location="screenshot_tool.py:AnnotationCanvas._selection_handle_hit_test",
            message="handle hit test",
            data={
                "pos": [pos.x(), pos.y()],
                "handle_rects": [[r.x(), r.y(), r.width(), r.height()] for r in handle_rects],
                "selection_rect": [self.selection_rect.x(), self.selection_rect.y(), self.selection_rect.width(), self.selection_rect.height()] if self.selection_rect else None,
            },
        )
        # endregion
        for handle_name, handle_rect in zip(handles, handle_rects):
            if handle_rect.contains(pos):
                return handle_name
        return None

    def _clone_marker(self, marker):
        return {
            "pos": QPoint(marker["pos"]),
            "number": marker["number"],
            "fill": QColor(marker["fill"]),
            "size": marker["size"],
            "border_enabled": marker.get("border_enabled", True),
            "border_color": QColor(marker.get("border_color", self.marker_border_color)),
            "font_ratio": marker.get("font_ratio", self.marker_font_ratio),
        }

    def _clone_rectangle(self, info):
        return {
            "rect": QRect(info["rect"]),
            "fill": QColor(info["fill"]),
            "border": QColor(info["border"]),
            "border_enabled": info.get("border_enabled", True),
            "width": info.get("width", self.rectangle_border_width),
            "radius": info.get("radius", self.rectangle_corner_radius),
            "flattened": info.get("flattened", False),
        }

    def _clone_text_item(self, item):
        return {
            "pos": QPoint(item["pos"]),
            "text": item["text"],
            "color": QColor(item.get("color", self.text_color)),
            "background": QColor(item.get("background", self.text_background_color)),
            "font_family": item.get("font_family", self.text_font_family),
            "font_size": item.get("font_size", self.text_font_size),
        }

    def _clone_line(self, line):
        return {
            "start": QPoint(line["start"]),
            "end": QPoint(line["end"]),
            "color": QColor(line["color"]),
            "width": line["width"],
        }

    def _clone_arrow(self, arrow):
        return {
            "start": QPoint(arrow["start"]),
            "end": QPoint(arrow["end"]),
            "color": QColor(arrow["color"]),
            "width": arrow["width"],
        }

    def _snapshot_state(self):
        try:
            base_snapshot = self.base_pixmap.copy()
        except Exception:
            base_snapshot = QPixmap()
        return {
            "base_pixmap": base_snapshot,
            "markers": [self._clone_marker(m) for m in self.markers],
            "rectangles": [self._clone_rectangle(r) for r in self.rectangles],
            "lines": [self._clone_line(l) for l in self.lines],
            "arrows": [self._clone_arrow(a) for a in self.arrows],
            "text_items": [self._clone_text_item(t) for t in self.text_items],
            "selection_rect": QRect(self.selection_rect) if self.selection_rect else None,
            "markers_flattened": self.markers_flattened,
            "rectangles_flattened": self.rectangles_flattened,
            "selected_marker_index": self.selected_marker_index,
            "selected_rectangle_index": self.selected_rectangle_index,
            "selected_text_index": self.selected_text_index,
            "selected_line_index": self.selected_line_index,
            "selected_arrow_index": self.selected_arrow_index,
            "next_marker_number": self.next_marker_number,
            "marker_style": {
                "fill": QColor(self.marker_fill_color),
                "border": QColor(self.marker_border_color),
                "border_enabled": self.marker_border_enabled,
                "size": self.marker_size,
                "font_ratio": self.marker_font_ratio,
            },
            "rectangle_style": {
                "fill": QColor(self.rectangle_fill_color),
                "border": QColor(self.rectangle_border_color),
                "border_enabled": self.rectangle_border_enabled,
                "width": self.rectangle_border_width,
                "radius": self.rectangle_corner_radius,
            },
            "text_style": {
                "color": QColor(self.text_color),
                "background": QColor(self.text_background_color),
                "font": self.text_font_family,
                "size": self.text_font_size,
            },
        }

    def _push_undo_state(self):
        # region agent log
        import traceback
        caller_stack = traceback.format_stack(limit=5)
        _agent_debug_log(
            hypothesisId="H-UNDO-PUSH",
            location="screenshot_tool.py:AnnotationCanvas._push_undo_state",
            message="undo state pushed",
            data={
                "stack_size_before": len(self._undo_stack),
                "pixmap_size": [self.base_pixmap.width(), self.base_pixmap.height()],
                "caller": caller_stack[-2].strip() if len(caller_stack) >= 2 else "unknown",
            },
        )
        # endregion
        snapshot = self._snapshot_state()
        if snapshot:
            self._undo_stack.append(snapshot)

    def _restore_state(self, state):
        if not state:
            return
        self.base_pixmap = state["base_pixmap"].copy()
        self.markers = [self._clone_marker(m) for m in state.get("markers", [])]
        self.rectangles = [self._clone_rectangle(r) for r in state.get("rectangles", [])]
        self.lines = [self._clone_line(l) for l in state.get("lines", [])]
        self.arrows = [self._clone_arrow(a) for a in state.get("arrows", [])]
        self.text_items = [self._clone_text_item(t) for t in state.get("text_items", [])]
        snap_rect = state.get("selection_rect")
        self.selection_rect = QRect(snap_rect) if snap_rect else None
        self.markers_flattened = state.get("markers_flattened", True)
        self.rectangles_flattened = state.get("rectangles_flattened", True)
        self.selected_marker_index = state.get("selected_marker_index")
        self.selected_rectangle_index = state.get("selected_rectangle_index")
        self.selected_text_index = state.get("selected_text_index")
        
        # 验证选中的索引是否仍然有效
        if self.selected_marker_index is not None and (self.selected_marker_index < 0 or self.selected_marker_index >= len(self.markers)):
            self.selected_marker_index = None
        if self.selected_rectangle_index is not None and (self.selected_rectangle_index < 0 or self.selected_rectangle_index >= len(self.rectangles)):
            self.selected_rectangle_index = None
        if self.selected_text_index is not None and (self.selected_text_index < 0 or self.selected_text_index >= len(self.text_items)):
            self.selected_text_index = None
            
        self.selected_line_index = state.get("selected_line_index")
        if self.selected_line_index is not None and (self.selected_line_index < 0 or self.selected_line_index >= len(self.lines)):
            self.selected_line_index = None
            
        self.selected_arrow_index = state.get("selected_arrow_index")
        if self.selected_arrow_index is not None and (self.selected_arrow_index < 0 or self.selected_arrow_index >= len(self.arrows)):
            self.selected_arrow_index = None
        self.next_marker_number = state.get("next_marker_number", self.next_marker_number)
        marker_style = state.get("marker_style", {})
        self.marker_fill_color = QColor(marker_style.get("fill", self.marker_fill_color))
        self.marker_border_color = QColor(marker_style.get("border", self.marker_border_color))
        self.marker_border_enabled = marker_style.get("border_enabled", self.marker_border_enabled)
        self.marker_size = marker_style.get("size", self.marker_size)
        self.marker_font_ratio = marker_style.get("font_ratio", self.marker_font_ratio)
        rect_style = state.get("rectangle_style", {})
        self.rectangle_fill_color = QColor(rect_style.get("fill", self.rectangle_fill_color))
        self.rectangle_border_color = QColor(rect_style.get("border", self.rectangle_border_color))
        self.rectangle_border_enabled = rect_style.get("border_enabled", self.rectangle_border_enabled)
        self.rectangle_border_width = rect_style.get("width", self.rectangle_border_width)
        self.rectangle_corner_radius = rect_style.get("radius", self.rectangle_corner_radius)
        text_style = state.get("text_style", {})
        self.text_color = QColor(text_style.get("color", self.text_color))
        self.text_background_color = QColor(text_style.get("background", self.text_background_color))
        self.text_font_family = text_style.get("font", self.text_font_family)
        self.text_font_size = text_style.get("size", self.text_font_size)
        self._selection_origin = None
        self._selection_dragging = False
        self._selection_drag_mode = None
        self._selection_handle = None
        self._selection_initial_rect = None
        self._selection_offset = QPoint()
        self.dragging_marker_index = None
        self._marker_dragging = False
        self.hover_marker_index = None
        self.rect_drag_mode = None
        self.rect_drag_handle = None
        self.rect_initial_rect = QRect()
        self.rect_drag_origin = QPoint()
        self.creating_new_rect = False
        self.creating_rect_origin = QPoint()
        self._text_dragging = False
        self._text_drag_index = None
        self._text_drag_offset = QPoint()
        self.update()
        self.optionsUpdated.emit()

    def clear_selection_pixels(self):
        rect = self._selection_bounds()
        # region agent log
        _agent_debug_log(
            hypothesisId="H-CLEAR-SEL",
            location="screenshot_tool.py:AnnotationCanvas.clear_selection_pixels",
            message="clear_selection_pixels called",
            data={
                "rect": [rect.x(), rect.y(), rect.width(), rect.height()] if rect else None,
                "selection_rect": [self.selection_rect.x(), self.selection_rect.y(), self.selection_rect.width(), self.selection_rect.height()] if self.selection_rect else None,
            },
        )
        # endregion
        if rect is None:
            return False
        self._push_undo_state()
        image = self.base_pixmap.toImage().convertToFormat(QImage.Format_ARGB32)
        painter = QPainter(image)
        painter.setCompositionMode(QPainter.CompositionMode_Clear)
        painter.fillRect(rect, Qt.transparent)
        painter.end()
        self.base_pixmap = QPixmap.fromImage(image)
        # region agent log
        _agent_debug_log(
            hypothesisId="H-CLEAR-SEL",
            location="screenshot_tool.py:AnnotationCanvas.clear_selection_pixels",
            message="clear_selection_pixels completed",
            data={
                "new_pixmap_format": self.base_pixmap.toImage().format(),
                "has_alpha": self.base_pixmap.hasAlphaChannel(),
            },
        )
        # endregion
        self.selection_rect = None
        self.update()
        self.optionsUpdated.emit()
        if getattr(self, "owner_tab", None):
            self.owner_tab.on_canvas_pixels_changed()
        return True

    def fill_selection_pixels(self, color: QColor):
        if not color or not color.isValid():
            return False
        rect = self._selection_bounds()
        if rect is None:
            return False
        self._push_undo_state()
        image = self.base_pixmap.toImage().convertToFormat(QImage.Format_ARGB32)
        painter = QPainter(image)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.fillRect(rect, color)
        painter.end()
        self.base_pixmap = QPixmap.fromImage(image)
        self.selection_rect = None
        self.update()
        self.optionsUpdated.emit()
        if getattr(self, "owner_tab", None):
            self.owner_tab.on_canvas_pixels_changed()
        return True

    def delete_selected_shape(self):
        if self._has_active_text():
            self._push_undo_state()
            self.text_items.pop(self.selected_text_index)
            self.selected_text_index = None
            self.optionsUpdated.emit()
            self.update()
            return True
        if self._has_active_marker():
            self._push_undo_state()
            self.markers.pop(self.selected_marker_index)
            self.selected_marker_index = None
            self.dragging_marker_index = None
            self._set_hover_marker(None)
            self.update()
            self.optionsUpdated.emit()
            return True
        if self._has_active_rectangle():
            self._push_undo_state()
            self.rectangles.pop(self.selected_rectangle_index)
            self.selected_rectangle_index = None
            self.rect_drag_mode = None
            self.rect_drag_handle = None
            self.update()
            self.optionsUpdated.emit()
            self._update_default_cursor()
            return True
        if self.selected_line_index is not None:
            self._push_undo_state()
            self.lines.pop(self.selected_line_index)
            self.selected_line_index = None
            self.update()
            self.optionsUpdated.emit()
            return True
        if self.selected_arrow_index is not None:
            self._push_undo_state()
            self.arrows.pop(self.selected_arrow_index)
            self.selected_arrow_index = None
            self.update()
            self.optionsUpdated.emit()
            return True
        return False

    def flatten_all_annotations(self):
        self._push_undo_state()
        self.markers_flattened = True
        self.selected_marker_index = None
        self.dragging_marker_index = None
        self.hover_marker_index = None
        for marker in self.markers:
            marker['border_enabled'] = marker.get('border_enabled', True)
        for rect in self.rectangles:
            rect['flattened'] = True
        self.rectangles_flattened = True
        self.selected_rectangle_index = None
        self.optionsUpdated.emit()

    def undo_last_shape(self):
        # region agent log
        _agent_debug_log(
            hypothesisId="H-UNDO",
            location="screenshot_tool.py:AnnotationCanvas.undo_last_shape",
            message="undo_last_shape called",
            data={
                "stack_size": len(self._undo_stack),
                "has_snapshot": bool(self._undo_stack),
            },
        )
        # endregion
        if not self._undo_stack:
            return False
        snapshot = self._undo_stack.pop()
        # region agent log
        old_size = [self.base_pixmap.width(), self.base_pixmap.height()]
        new_pixmap = snapshot.get("base_pixmap")
        new_size = [new_pixmap.width(), new_pixmap.height()] if new_pixmap else None
        _agent_debug_log(
            hypothesisId="H-UNDO",
            location="screenshot_tool.py:AnnotationCanvas.undo_last_shape",
            message="restoring snapshot",
            data={
                "old_pixmap_size": old_size,
                "new_pixmap_size": new_size,
            },
        )
        # endregion
        self._restore_state(snapshot)
        # 撤销后清除选区状态，避免工具状态混乱
        self.selection_rect = None
        self._selection_origin = None
        self._selection_dragging = False
        self._apply_zoom()  # 确保恢复缩放和窗口大小（针对裁切）
        self._mark_backing_store_dirty()
        self.optionsUpdated.emit()
        return True

    def _reset_rect_drag(self):
        self.rect_drag_mode = None
        self.rect_drag_handle = None
        self.rect_initial_rect = QRect()
        self.rect_drag_origin = QPoint()
        self.creating_new_rect = False
        self.creating_rect_origin = QPoint()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        pos = self._view_to_scene(event.pos())
        # region agent log
        _agent_debug_log(
            hypothesisId="H2-H3",
            location="screenshot_tool.py:AnnotationCanvas.mousePressEvent",
            message="mouse press - checking bounds",
            data={
                "raw_pos": [event.pos().x(), event.pos().y()],
                "scene_pos": [pos.x(), pos.y()],
                "tool": self.tool.name if self.tool else None,
                "pixmap_size": [self.base_pixmap.width(), self.base_pixmap.height()],
                "canvas_size": [self.width(), self.height()],
                "is_pos_in_bounds": (0 <= pos.x() <= self.base_pixmap.width() and 0 <= pos.y() <= self.base_pixmap.height()),
                "canvas_padding": self.CANVAS_PADDING,
                "zoom": self._zoom,
            },
        )
        # endregion
        if self.tool == Tool.SELECTION or self.tool == Tool.CROP:
            handle = self._selection_handle_hit_test(pos) if self.selection_rect else None
            # region agent log
            _agent_debug_log(
                hypothesisId="H2-H3-PRESS",
                location="screenshot_tool.py:AnnotationCanvas.mousePressEvent",
                message="CROP/SELECTION press handling",
                data={
                    "tool": self.tool.name,
                    "handle": handle,
                    "selection_rect_exists": self.selection_rect is not None,
                    "scene_pos": [pos.x(), pos.y()],
                },
            )
            # endregion
            if handle:
                # 点击控制柄：调整选区大小
                self._selection_drag_mode = "resize"
                self._selection_handle = handle
                self._selection_initial_rect = QRect(self.selection_rect).normalized()
                self._selection_origin = QPoint(pos)
                self._selection_dragging = True
            elif self.tool == Tool.CROP:
                # 裁切模式：点击任何非控制柄区域，开始画新选区
                self.selection_rect = QRect(pos, pos)
                self._selection_origin = QPoint(pos)
                self._selection_dragging = True  # 设为 True，松开时才能触发裁切
                self._selection_drag_mode = "new"
                self._selection_handle = "bottom-right"
                self._selection_initial_rect = QRect(self.selection_rect)
                # region agent log
                _agent_debug_log(
                    hypothesisId="H2-H3-PRESS",
                    location="screenshot_tool.py:AnnotationCanvas.mousePressEvent",
                    message="CROP new selection started",
                    data={
                        "selection_rect": [self.selection_rect.x(), self.selection_rect.y(), self.selection_rect.width(), self.selection_rect.height()],
                        "selection_origin": [self._selection_origin.x(), self._selection_origin.y()],
                    },
                )
                # endregion
            elif self.selection_rect:
                # 选区模式：点击选区内部可以移动
                expanded = QRect(self.selection_rect).normalized()
                expanded = expanded.adjusted(-SELECTION_HIT_MARGIN, -SELECTION_HIT_MARGIN, SELECTION_HIT_MARGIN, SELECTION_HIT_MARGIN)
                if expanded.contains(pos):
                    self._selection_drag_mode = "move"
                    self._selection_dragging = True
                    self._selection_origin = QPoint(pos)
                    self._selection_initial_rect = QRect(self.selection_rect)
                    self._selection_offset = pos - self.selection_rect.topLeft()
                else:
                    self.selection_rect = QRect(pos, pos)
                    self._selection_origin = QPoint(pos)
                    self._selection_dragging = True
                    self._selection_drag_mode = "new"
                    self._selection_handle = "bottom-right"
                    self._selection_initial_rect = QRect(self.selection_rect)
            else:
                self.selection_rect = QRect(pos, pos)
                self._selection_origin = QPoint(pos)
                self._selection_dragging = True
                self._selection_drag_mode = "new"
                self._selection_handle = "bottom-right"
                self._selection_initial_rect = QRect(self.selection_rect)
            self.selected_marker_index = None
            self.selected_rectangle_index = None
            self.selected_text_index = None
            self._marker_dragging = False
            self._text_dragging = False
            self.update()
            return
        if self.tool == Tool.MARKER:
            if self._handle_marker_press(pos, allow_creation=True):
                return
        elif self.tool == Tool.RECTANGLE:
            if self._handle_rect_press(pos, allow_creation=True):
                return
        elif self.tool == Tool.TEXT:
            if self._handle_text_press(pos, allow_creation=True):
                return
        elif self.tool in (Tool.LINE, Tool.ARROW):
            if self._handle_shape_press(pos, allow_creation=True):
                return
        
        if self._handle_rect_press(pos, allow_creation=False, handles_only=True):
            return
        if self._handle_text_press(pos, allow_creation=False):
            return
        if self._handle_marker_press(pos, allow_creation=False):
            return
        if self._handle_rect_press(pos, allow_creation=False):
            return
        if self._handle_shape_press(pos, allow_creation=False):
            return
        self.clear_active_selection()

    def mouseMoveEvent(self, event):
        self._maybe_auto_scroll(event)
        pos = self._view_to_scene(event.pos())
        # region agent log
        if self.tool == Tool.CROP:
            _agent_debug_log(
                hypothesisId="H2-H3",
                location="screenshot_tool.py:AnnotationCanvas.mouseMoveEvent",
                message="crop mode move",
                data={
                    "raw_pos": [event.pos().x(), event.pos().y()],
                    "scene_pos": [pos.x(), pos.y()],
                    "selection_origin": [self._selection_origin.x(), self._selection_origin.y()] if self._selection_origin else None,
                    "selection_rect": [self.selection_rect.x(), self.selection_rect.y(), self.selection_rect.width(), self.selection_rect.height()] if self.selection_rect else None,
                    "pixmap_size": [self.base_pixmap.width(), self.base_pixmap.height()],
                    "_selection_dragging": self._selection_dragging,
                },
            )
        # endregion
        
        if self._current_shape:
            if self._current_shape['type'] in ('line', 'arrow'):
                end_pos = pos
                if event.modifiers() & Qt.ShiftModifier:
                    # Shift 按下时，吸附到 45 度角
                    start = self._current_shape['start']
                    dx = pos.x() - start.x()
                    dy = pos.y() - start.y()
                    angle = math.atan2(dy, dx)
                    # 将弧度转换为 45 度的倍数
                    # 45 deg = pi/4 rad
                    snapped_angle = round(angle / (math.pi / 4)) * (math.pi / 4)
                    dist = math.sqrt(dx*dx + dy*dy)
                    end_pos = QPoint(
                        int(round(start.x() + dist * math.cos(snapped_angle))),
                        int(round(start.y() + dist * math.sin(snapped_angle)))
                    )
                self._current_shape['end'] = end_pos
            self.update()
            return

        # 处理形状移动/缩放
        if self._shape_drag_mode == 'move':
            delta = pos - self._shape_drag_origin
            if self.selected_line_index is not None and self.selected_line_index < len(self.lines):
                info = self.lines[self.selected_line_index]
                info['start'] = self._shape_initial_data['start'] + delta
                info['end'] = self._shape_initial_data['end'] + delta
            elif self.selected_arrow_index is not None and self.selected_arrow_index < len(self.arrows):
                info = self.arrows[self.selected_arrow_index]
                info['start'] = self._shape_initial_data['start'] + delta
                info['end'] = self._shape_initial_data['end'] + delta
            self.update()
            return
        elif self._shape_drag_mode == 'resize':
            if self.selected_line_index is not None and self.selected_line_index < len(self.lines):
                info = self.lines[self.selected_line_index]
                if self._shape_drag_handle == 'start':
                    new_start = pos
                    if event.modifiers() & Qt.ShiftModifier:
                        # 吸附
                        dx = pos.x() - info['end'].x()
                        dy = pos.y() - info['end'].y()
                        angle = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
                        dist = math.sqrt(dx*dx + dy*dy)
                        new_start = info['end'] + QPoint(int(round(dist * math.cos(angle))), int(round(dist * math.sin(angle))))
                    info['start'] = new_start
                else:
                    new_end = pos
                    if event.modifiers() & Qt.ShiftModifier:
                        dx = pos.x() - info['start'].x()
                        dy = pos.y() - info['start'].y()
                        angle = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
                        dist = math.sqrt(dx*dx + dy*dy)
                        new_end = info['start'] + QPoint(int(round(dist * math.cos(angle))), int(round(dist * math.sin(angle))))
                    info['end'] = new_end
            elif self.selected_arrow_index is not None and self.selected_arrow_index < len(self.arrows):
                info = self.arrows[self.selected_arrow_index]
                if self._shape_drag_handle == 'start':
                    new_start = pos
                    if event.modifiers() & Qt.ShiftModifier:
                        dx = pos.x() - info['end'].x()
                        dy = pos.y() - info['end'].y()
                        angle = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
                        dist = math.sqrt(dx*dx + dy*dy)
                        new_start = info['end'] + QPoint(int(round(dist * math.cos(angle))), int(round(dist * math.sin(angle))))
                    info['start'] = new_start
                else:
                    new_end = pos
                    if event.modifiers() & Qt.ShiftModifier:
                        dx = pos.x() - info['start'].x()
                        dy = pos.y() - info['start'].y()
                        angle = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
                        dist = math.sqrt(dx*dx + dy*dy)
                        new_end = info['start'] + QPoint(int(round(dist * math.cos(angle))), int(round(dist * math.sin(angle))))
                    info['end'] = new_end
            self.update()
            return

        if (self.tool == Tool.SELECTION or self.tool == Tool.CROP) and self._selection_origin is not None:
            bounds = QRect(0, 0, self.base_pixmap.width(), self.base_pixmap.height())
            if self._selection_drag_mode == "move" and self.selection_rect:
                top_left = pos - self._selection_offset
                rect = QRect(top_left, self._selection_initial_rect.size())
                rect = self._clamp_rect_to_bounds(rect, bounds)
            elif self._selection_drag_mode == "resize" and self.selection_rect:
                delta = pos - self._selection_origin
                rect = self._resize_rect(
                    self._selection_initial_rect,
                    self._selection_handle,
                    delta,
                    event.modifiers(),
                    min_size=self.MIN_RECT_SIZE,
                    bounds=bounds,
                )
            else:
                rect = QRect(self._selection_origin, pos)
                rect = self._clamp_rect_to_bounds(rect, bounds)
            self.selection_rect = rect if rect else None
            self.update()
            return
        if self._text_dragging and self._text_drag_index is not None:
            item = self.text_items[self._text_drag_index]
            item["pos"] = QPoint(pos - self._text_drag_offset)
            self.update()
            self.optionsUpdated.emit()
            return
        if self.dragging_marker_index is not None and not self.markers_flattened:
            self.markers[self.dragging_marker_index]['pos'] = pos
            self.update()
            return
        if self.rect_drag_mode and self.selected_rectangle_index is not None:
            info = self.rectangles[self.selected_rectangle_index]
            rect = QRect(self.rect_initial_rect)
            delta = pos - self.rect_drag_origin
            if self.rect_drag_mode == 'move':
                rect.translate(delta)
            else:
                bounds = QRect(0, 0, self.base_pixmap.width(), self.base_pixmap.height())
                if self.creating_new_rect and not self.creating_rect_origin.isNull():
                    eff_pos = QPoint(
                        max(pos.x(), self.creating_rect_origin.x() + 1),
                        max(pos.y(), self.creating_rect_origin.y() + 1),
                    )
                    rect = QRect(self.creating_rect_origin, eff_pos)
                else:
                    rect = self._resize_rect(
                        self.rect_initial_rect,
                        self.rect_drag_handle,
                        delta,
                        event.modifiers(),
                        bounds=bounds,
                    )
            rect = rect.normalized()
            if rect.width() > 4 and rect.height() > 4:
                info['rect'] = rect
            self.update()
            return
        self._update_pointer_feedback(pos)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        pos = self._view_to_scene(event.pos())
        
        # 记录当前状态，因为裁切后会重置
        is_crop_mode = (self.tool == Tool.CROP)
        is_dragging = self._selection_dragging
        
        if self._current_shape:
            # 完成形状创建
            if self._current_shape['type'] == 'line':
                self.lines.append(self._current_shape)
                self.selected_line_index = len(self.lines) - 1
            elif self._current_shape['type'] == 'arrow':
                self.arrows.append(self._current_shape)
                self.selected_arrow_index = len(self.arrows) - 1
            self._current_shape = None
            self._mark_backing_store_dirty()
            self.optionsUpdated.emit()
            self.update()
            return

        if self._shape_drag_mode:
            self._shape_drag_mode = None
            self._shape_drag_origin = None
            self._shape_initial_data = None
            self._mark_backing_store_dirty()
            self.optionsUpdated.emit()
            self.update()
            return

        if (self.tool == Tool.SELECTION or self.tool == Tool.CROP) and self._selection_origin is not None:
            bounds = QRect(0, 0, self.base_pixmap.width(), self.base_pixmap.height())
            if self._selection_drag_mode == "move" and self.selection_rect:
                rect = QRect(self.selection_rect).intersected(bounds)
                self.selection_rect = rect if rect.isValid() else None
            elif self._selection_drag_mode == "resize" and self.selection_rect:
                rect = QRect(self.selection_rect).normalized().intersected(bounds)
                rect = self._clamp_rect_to_bounds(rect, bounds)
                self.selection_rect = rect if rect else None
            elif self._selection_drag_mode == "new":
                # 新建选区模式
                rect = QRect(self._selection_origin, pos).normalized().intersected(bounds)
                self.selection_rect = rect if rect and rect.width() >= 2 and rect.height() >= 2 else None
            else:
                rect = QRect(self._selection_origin, pos).normalized().intersected(bounds)
                self.selection_rect = rect if rect and rect.width() >= 2 and rect.height() >= 2 else None
            
            # 如果是裁切模式，且松开鼠标，执行裁切
            if is_crop_mode and is_dragging and self.selection_rect:
                final_rect = QRect(self.selection_rect)
                if final_rect.width() > 10 and final_rect.height() > 10:
                    self.perform_crop(final_rect)
                else:
                    self.clear_active_selection()
            
            if self.selection_rect is None:
                # No valid selection retained
                self._selection_origin = None
                self._selection_dragging = False
                self._selection_drag_mode = None
                self._selection_handle = None
                self.update()
                return
            self._selection_origin = None
            self._selection_dragging = False
            self._selection_drag_mode = None
            self._selection_handle = None
            self.update()
            return
        if self.dragging_marker_index is not None:
            self._end_marker_drag()
            self.dragging_marker_index = None
            self.update()
        if self.rect_drag_mode and self.selected_rectangle_index is not None:
            if (
                self.creating_new_rect
                and self.selected_rectangle_index < len(self.rectangles)
            ):
                rect = self.rectangles[self.selected_rectangle_index]['rect']
                if rect.width() < self.MIN_RECT_SIZE or rect.height() < self.MIN_RECT_SIZE:
                    self.rectangles.pop(self.selected_rectangle_index)
                    self.selected_rectangle_index = None
                    self.update()
                    self.optionsUpdated.emit()
            self._reset_rect_drag()
        if self._text_dragging:
            self._end_text_drag()
        self._update_pointer_feedback(pos)

    def mouseDoubleClickEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        if self.tool == Tool.SELECTION:
            return
        pos = self._view_to_scene(event.pos())
        idx = self._text_hit_test(pos)
        if idx is not None:
            self._edit_text(idx)

    def _handle_marker_press(self, pos: QPoint, allow_creation=True):
        idx = self._marker_hit_test(pos)
        if idx is not None and not self.markers_flattened:
            self._push_undo_state()
            self.dragging_marker_index = idx
            self.selected_marker_index = idx
            self.selected_rectangle_index = None
            self.selected_text_index = None
            self.rect_drag_mode = None
            self._set_hover_marker(None)
            self._begin_marker_drag()
            self.optionsUpdated.emit()
            self.update()
            return True
        if allow_creation:
            self._push_undo_state()
            marker = {
                'pos': pos,
                'number': self.next_marker_number,
                'fill': QColor(self.marker_fill_color),
                'size': self.marker_size,
                'border_enabled': self.marker_border_enabled,
                'border_color': QColor(self.marker_border_color),
                'font_ratio': self.marker_font_ratio,
            }
            self.markers.append(marker)
            self.selected_marker_index = len(self.markers) - 1
            self.selected_rectangle_index = None
            self.selected_text_index = None
            self.dragging_marker_index = self.selected_marker_index
            self.rect_drag_mode = None
            self.markers_flattened = False
            self._set_hover_marker(None)
            self.next_marker_number += 1
            self._begin_marker_drag()
            self.optionsUpdated.emit()
            self.update()
            return True
        return False

    def _handle_text_press(self, pos: QPoint, allow_creation=False):
        idx = self._text_hit_test(pos)
        if idx is not None:
            self._push_undo_state()
            self.selected_text_index = idx
            self.selected_marker_index = None
            self.selected_rectangle_index = None
            self.rect_drag_mode = None
            self._set_hover_marker(None)
            self.optionsUpdated.emit()
            self.update()
            self._notify_text_selection()
            if not self._text_dragging:
                self._begin_text_drag(idx, pos)
            return True
        if allow_creation and self.tool == Tool.TEXT:
            return self._add_text_at(pos)
        return False

    def _add_text_at(self, pos: QPoint):
        text, ok = QInputDialog.getText(self, "插入文字", "请输入文字内容", QLineEdit.Normal, "")
        if not ok:
            return False
        text = text.replace("\r", "").replace("\n", "").strip()
        if not text:
            return False
        self._push_undo_state()
        item = {
            "pos": QPoint(pos),
            "text": text,
            "color": QColor(self.text_color),
            "background": QColor(self.text_background_color),
            "font_family": self.text_font_family,
            "font_size": self.text_font_size,
        }
        self.text_items.append(item)
        self.selected_text_index = len(self.text_items) - 1
        self.selected_marker_index = None
        self.selected_rectangle_index = None
        self.rect_drag_mode = None
        self._set_hover_marker(None)
        self._begin_text_drag(self.selected_text_index, pos)
        self.optionsUpdated.emit()
        self.update()

    def _notify_text_selection(self):
        if hasattr(self, "owner_tab") and self.owner_tab:
            item = None
            if self._has_active_text():
                item = dict(self.text_items[self.selected_text_index])
            self.owner_tab.on_canvas_text_selection(item)
        return True

    def _edit_text(self, idx: int):
        if idx is None or not (0 <= idx < len(self.text_items)):
            return
        item = self.text_items[idx]
        text, ok = QInputDialog.getText(self, "修改文字", "编辑当前文字", QLineEdit.Normal, item["text"])
        if not ok:
            return
        text = text.strip()
        if not text:
            return
        self._push_undo_state()
        item["text"] = text
        self.optionsUpdated.emit()
        self.update()

    def _begin_marker_drag(self):
        if not self._marker_dragging:
            self._marker_dragging = True
            self.setCursor(Qt.BlankCursor)

    def _end_marker_drag(self):
        if self._marker_dragging:
            self._marker_dragging = False
            view_pos = self.mapFromGlobal(QCursor.pos())
            self._update_pointer_feedback(self._view_to_scene(view_pos))

    def _begin_text_drag(self, idx: int, pos: QPoint):
        self._text_dragging = True
        self._text_drag_index = idx
        self._text_drag_offset = pos - self.text_items[idx]["pos"]
        if self.tool == Tool.TEXT:
            self.setCursor(Qt.BlankCursor)
        else:
            self.setCursor(Qt.SizeAllCursor)

    def _end_text_drag(self):
        if self._text_dragging:
            self._text_dragging = False
            self._text_drag_index = None
            self._text_drag_offset = QPoint()
            view_pos = self.mapFromGlobal(QCursor.pos())
            self._update_pointer_feedback(self._view_to_scene(view_pos))

    def _handle_shape_press(self, pos: QPoint, allow_creation=True):
        # 0. 检查是否点击了已选中形状的控制柄
        handle = self._shape_handle_hit_test(pos)
        if handle:
            self._push_undo_state()
            self._shape_drag_mode = 'resize'
            self._shape_drag_handle = handle
            self._shape_drag_origin = pos
            if self.selected_line_index is not None:
                self._shape_initial_data = self._clone_line(self.lines[self.selected_line_index])
            elif self.selected_arrow_index is not None:
                self._shape_initial_data = self._clone_arrow(self.arrows[self.selected_arrow_index])
            self.optionsUpdated.emit()
            return True

        # 1. 检查是否点击了已有的形状
        line_idx = self._line_hit_test(pos)
        if line_idx is not None:
            self._push_undo_state()
            self.selected_line_index = line_idx
            self._shape_drag_mode = 'move'
            self._shape_drag_origin = pos
            self._shape_initial_data = self._clone_line(self.lines[line_idx])
            self.update()
            self.optionsUpdated.emit()
            return True
            
        arrow_idx = self._arrow_hit_test(pos)
        if arrow_idx is not None:
            self._push_undo_state()
            self.selected_arrow_index = arrow_idx
            self._shape_drag_mode = 'move'
            self._shape_drag_origin = pos
            self._shape_initial_data = self._clone_arrow(self.arrows[arrow_idx])
            self.update()
            self.optionsUpdated.emit()
            return True

        if not allow_creation:
            return False
            
        self._push_undo_state()
        if self.tool == Tool.LINE:
            self._current_shape = {'type': 'line', 'start': pos, 'end': pos, 'color': self.line_color, 'width': self.line_width}
        elif self.tool == Tool.ARROW:
            self._current_shape = {'type': 'arrow', 'start': pos, 'end': pos, 'color': self.arrow_color, 'width': self.arrow_width}
            
        self.update()
        return True

    def _handle_rect_press(self, pos: QPoint, allow_creation=True, handles_only=False):
        idx, handle = self._handle_rect_handle_hit_test(pos)
        if idx is not None:
            self._push_undo_state()
            self.selected_rectangle_index = idx
            self.selected_marker_index = None
            self.dragging_marker_index = None
            self.rect_drag_mode = 'resize'
            self.rect_drag_handle = handle
            self.rect_initial_rect = QRect(self.rectangles[idx]['rect'])
            self.rect_drag_origin = QPoint(pos)
            self.rectangles_flattened = False
            self.creating_new_rect = False
            self._set_hover_marker(None)
            self.optionsUpdated.emit()
            if handle in ("top-left", "bottom-right"):
                self._update_cursor(Qt.SizeFDiagCursor)
            else:
                self._update_cursor(Qt.SizeBDiagCursor)
            return True
        if handles_only:
            return False
        idx = self._rect_hit_test(pos)
        if idx is not None:
            self._push_undo_state()
            self.selected_rectangle_index = idx
            self.selected_marker_index = None
            self.selected_text_index = None
            self.dragging_marker_index = None
            self.rect_drag_mode = 'move'
            self.rect_initial_rect = QRect(self.rectangles[idx]['rect'])
            self.rect_drag_origin = QPoint(pos)
            self.rectangles_flattened = False
            self.creating_new_rect = False
            self._set_hover_marker(None)
            self.optionsUpdated.emit()
            self._update_cursor(Qt.SizeAllCursor)
            return True
        if not allow_creation:
            return False
        self._push_undo_state()
        rect_info = {
            'rect': QRect(pos, pos),
            'fill': QColor(self.rectangle_fill_color),
            'border': QColor(self.rectangle_border_color),
            'border_enabled': self.rectangle_border_enabled,
            'width': self.rectangle_border_width,
            'radius': self.rectangle_corner_radius,
            'flattened': False,
        }
        self.rectangles.append(rect_info)
        self.selected_rectangle_index = len(self.rectangles) - 1
        self.selected_marker_index = None
        self.selected_text_index = None
        self.dragging_marker_index = None
        self.rect_drag_mode = 'resize'
        self.rect_drag_handle = 'bottom-right'
        self.rect_initial_rect = QRect(rect_info['rect'])
        self.rect_drag_origin = QPoint(pos)
        self.creating_rect_origin = QPoint(pos)
        self.rectangles_flattened = False
        self.creating_new_rect = True
        self._set_hover_marker(None)
        self.optionsUpdated.emit()
        self._update_cursor(Qt.ArrowCursor)
        return True

    def _mark_backing_store_dirty(self):
        self._backing_store_dirty = True
        self.update()

    def _render_to_backing_store(self):
        if self.base_pixmap.isNull():
            return
        if self._backing_store.size() != self.base_pixmap.size():
            self._backing_store = QPixmap(self.base_pixmap.size())
        self._backing_store.fill(Qt.transparent)
        painter = QPainter(self._backing_store)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        
        # 绘制底图
        painter.drawPixmap(0, 0, self.base_pixmap)
        
        # 绘制所有矩形
        for info in self.rectangles:
            painter.setBrush(info['fill'])
            if info['border_enabled']:
                painter.setPen(QPen(info['border'], info['width']))
            else:
                painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(info['rect'], info['radius'], info['radius'])
            
        # 绘制所有直线
        for idx, info in enumerate(self.lines):
            painter.setPen(QPen(info['color'], info['width']))
            painter.drawLine(info['start'], info['end'])
            # 选中高亮
            if idx == self.selected_line_index:
                painter.setPen(QPen(QColor(30, 144, 255), 1, Qt.DashLine))
                painter.drawRect(QRect(info['start'], info['end']).normalized().adjusted(-5, -5, 5, 5))
                # 绘制端点控制柄
                painter.setBrush(QColor("#ffffff"))
                painter.setPen(QPen(QColor(30, 144, 255), 1))
                r = 4
                painter.drawEllipse(info['start'], r, r)
                painter.drawEllipse(info['end'], r, r)
            
        # 绘制所有箭头
        for idx, info in enumerate(self.arrows):
            self._draw_arrow(painter, info['start'], info['end'], info['color'], info['width'])
            # 选中高亮
            if idx == self.selected_arrow_index:
                painter.setPen(QPen(QColor(30, 144, 255), 1, Qt.DashLine))
                painter.drawRect(QRect(info['start'], info['end']).normalized().adjusted(-10, -10, 10, 10))
                # 绘制端点控制柄
                painter.setBrush(QColor("#ffffff"))
                painter.setPen(QPen(QColor(30, 144, 255), 1))
                r = 4
                painter.drawEllipse(info['start'], r, r)
                painter.drawEllipse(info['end'], r, r)
            
        # 绘制所有文字
        for idx, item in enumerate(self.text_items):
            bounding, text_font, _ = self._text_geometry(item)
            painter.setFont(text_font)
            bg_color = item.get("background") or self.text_background_color
            bg_qcolor = QColor(bg_color)
            if bg_qcolor.isValid() and bg_qcolor.alpha() > 0:
                painter.setPen(Qt.NoPen)
                painter.setBrush(bg_qcolor)
                painter.drawRect(bounding)
            painter.setPen(QColor(item.get("color") or self.text_color))
            painter.setBrush(Qt.NoBrush)
            painter.drawText(item["pos"], item["text"])
            
        # 绘制所有标记
        marker_font = QFont()
        marker_font.setBold(True)
        for marker in self.markers:
            radius = marker['size']
            marker_font.setPixelSize(int(radius * marker['font_ratio']))
            painter.setFont(marker_font)
            ellipse_rect = QRect(marker['pos'].x() - radius, marker['pos'].y() - radius, radius * 2, radius * 2)
            painter.setBrush(marker['fill'])
            painter.drawEllipse(ellipse_rect)
            if marker['border_enabled']:
                painter.setPen(QPen(marker['border_color'], max(2, radius * 0.2)))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(ellipse_rect)
            painter.setBrush(marker['fill'])
            painter.setPen(Qt.white)
            painter.drawText(ellipse_rect, Qt.AlignCenter, str(marker['number']))
            
        painter.end()
        self._backing_store_dirty = False

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        
        pad = self.CANVAS_PADDING
        zoom = self._zoom
        
        # region agent log
        if self.tool == Tool.CROP:
            _agent_debug_log(
                hypothesisId="H5",
                location="screenshot_tool.py:AnnotationCanvas.paintEvent",
                message="paint event in CROP mode",
                data={
                    "canvas_size": [self.width(), self.height()],
                    "pad": pad,
                    "zoom": zoom,
                    "pixmap_size": [self.base_pixmap.width(), self.base_pixmap.height()],
                    "selection_rect": [self.selection_rect.x(), self.selection_rect.y(), self.selection_rect.width(), self.selection_rect.height()] if self.selection_rect else None,
                    "translate_offset": [pad * zoom, pad * zoom],
                },
            )
        # endregion
        
        # 1. 绘制背景（整个画布，包括边距）- 使用纯色背景
        painter.fillRect(self.rect(), QColor("#f0f0f0"))
        
        # 2. 坐标变换：平移边距并应用缩放
        painter.save()
        painter.translate(pad * zoom, pad * zoom)
        painter.scale(zoom, zoom)
        
        # 在底图区域先绘制棋盘格背景（用于显示透明区域）
        img_rect = QRect(0, 0, self.base_pixmap.width(), self.base_pixmap.height())
        painter.fillRect(img_rect, self._checkerboard_brush())
        
        # 画底图
        painter.drawPixmap(0, 0, self.base_pixmap)

        # 绘制静态标注缓存
        if self._backing_store_dirty:
            self._render_to_backing_store()
        painter.drawPixmap(0, 0, self._backing_store)
        
        # 绘制当前正在创建的形状
        if self._current_shape:
            if self._current_shape['type'] == 'line':
                painter.setPen(QPen(self._current_shape['color'], self._current_shape['width']))
                painter.drawLine(self._current_shape['start'], self._current_shape['end'])
            elif self._current_shape['type'] == 'arrow':
                self._draw_arrow(painter, self._current_shape['start'], self._current_shape['end'], self._current_shape['color'], self._current_shape['width'])
        
        # 3. 绘制动态元素
        # 裁切模式提示（无选区时显示整体暗化和边框提示）
        if self.tool == Tool.CROP and (not self.selection_rect or self.selection_rect.width() <= 1 or self.selection_rect.height() <= 1):
            # 轻微暗化整个图像，提示用户处于裁切模式
            img_rect = QRect(0, 0, self.base_pixmap.width(), self.base_pixmap.height())
            painter.setBrush(QColor(0, 0, 0, 60))  # 轻微暗化
            painter.setPen(Qt.NoPen)
            painter.drawRect(img_rect)
            # 绘制红色虚线边框提示
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor("#dc2626"), 2, Qt.DashLine))
            painter.drawRect(img_rect.adjusted(1, 1, -1, -1))
        
        # 选区/裁切框
        if self.selection_rect and self.selection_rect.width() > 1 and self.selection_rect.height() > 1:
            if self.tool == Tool.CROP:
                # 裁切模式：暗化外部区域，醒目的红色边框
                img_rect = QRect(0, 0, self.base_pixmap.width(), self.base_pixmap.height())
                sel = self.selection_rect.normalized()
                dim_color = QColor(0, 0, 0, 150)  # 更深的暗化
                painter.setBrush(dim_color)
                painter.setPen(Qt.NoPen)
                # 上
                if sel.top() > 0:
                    painter.drawRect(QRect(0, 0, img_rect.width(), sel.top()))
                # 下
                if sel.bottom() < img_rect.height() - 1:
                    painter.drawRect(QRect(0, sel.bottom() + 1, img_rect.width(), img_rect.height() - sel.bottom() - 1))
                # 左
                if sel.left() > 0:
                    painter.drawRect(QRect(0, sel.top(), sel.left(), sel.height()))
                # 右
                if sel.right() < img_rect.width() - 1:
                    painter.drawRect(QRect(sel.right() + 1, sel.top(), img_rect.width() - sel.right() - 1, sel.height()))
                # 绘制裁切框边框（粗实线，醒目红色）
                painter.setBrush(Qt.NoBrush)
                pen = QPen(QColor("#dc2626"), 3, Qt.SolidLine)  # 更粗的红色边框
                painter.setPen(pen)
                painter.drawRect(sel)
            else:
                # 选区模式：使用半透明填充
                overlay = QColor("#0ea5e980")
                painter.setBrush(overlay)
                painter.setPen(QPen(QColor("#0ea5e9"), 1, Qt.DashLine))
                painter.drawRect(self.selection_rect)
            # 绘制控制柄（裁切模式用更大更醒目的控制柄）
            handle_rects = self._selection_handle_rects(for_hit=False)
            for handle_rect in handle_rects:
                if self.tool == Tool.CROP:
                    # 裁切模式：更大更醒目的红色控制柄
                    larger_rect = handle_rect.adjusted(-2, -2, 2, 2)
                    painter.setBrush(QColor("#dc2626"))
                    painter.setPen(QPen(QColor("#ffffff"), 1))
                    painter.drawRect(larger_rect)
                else:
                    painter.setBrush(QColor("#0ea5e9"))
                    painter.setPen(Qt.NoPen)
                    painter.drawRect(handle_rect)
        
        # 文本选中高亮
        if self.selected_text_index is not None and self.selected_text_index < len(self.text_items):
            item = self.text_items[self.selected_text_index]
            bounding, _, _ = self._text_geometry(item)
            highlight_rect = bounding.adjusted(-4, -3, 4, 3)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(247, 181, 0, 70))
            painter.drawRect(highlight_rect)
            painter.setPen(QPen(QColor("#f7b500")))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(highlight_rect)

        # 标记选中高亮
        if self.selected_marker_index is not None and self.selected_marker_index < len(self.markers):
            marker = self.markers[self.selected_marker_index]
            radius = marker['size']
            ellipse_rect = QRect(marker['pos'].x() - radius, marker['pos'].y() - radius, radius * 2, radius * 2)
            glow_rect = ellipse_rect.adjusted(-int(radius * 0.2), -int(radius * 0.2), int(radius * 0.2), int(radius * 0.2))
            color = QColor(marker['fill'])
            color.setAlpha(120)
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(glow_rect)

        # 绘制新图形的选中控制柄
        painter.setBrush(QColor("#5f27cd"))
        painter.setPen(QPen(Qt.white, 1))
        handle_size = 8
        half = handle_size // 2
        
        if self.selected_line_index is not None and self.selected_line_index < len(self.lines):
            info = self.lines[self.selected_line_index]
            for p in [info['start'], info['end']]:
                painter.drawRect(p.x() - half, p.y() - half, handle_size, handle_size)
        elif self.selected_arrow_index is not None and self.selected_arrow_index < len(self.arrows):
            info = self.arrows[self.selected_arrow_index]
            for p in [info['start'], info['end']]:
                painter.drawRect(p.x() - half, p.y() - half, handle_size, handle_size)

        painter.restore()

    def export_pixmap(self):
        """导出最终图像，包含所有标注和可选的边框"""
        # 如果设置了边框，导出的图需要略大一点以容纳边框
        w = self.base_pixmap.width()
        h = self.base_pixmap.height()
        bw = self.image_border_width
        
        # region agent log
        _agent_debug_log(
            hypothesisId="H-EXPORT",
            location="screenshot_tool.py:AnnotationCanvas.export_pixmap",
            message="export_pixmap called",
            data={
                "base_pixmap_size": [w, h],
                "border_width": bw,
                "base_has_alpha": self.base_pixmap.hasAlphaChannel(),
                "base_format": self.base_pixmap.toImage().format(),
            },
        )
        # endregion
        
        # 创建一个包含边框的新画布
        final_w = w + bw * 2
        final_h = h + bw * 2
        annotated = QPixmap(final_w, final_h)
        annotated.fill(Qt.transparent)
        
        painter = QPainter(annotated)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        
        # 1. 绘制边框（只绘制四条边，不覆盖底图区域以保留透明）
        if bw > 0:
            painter.setBrush(self.image_border_color)
            painter.setPen(Qt.NoPen)
            # 上边框
            painter.drawRect(0, 0, final_w, bw)
            # 下边框
            painter.drawRect(0, final_h - bw, final_w, bw)
            # 左边框
            painter.drawRect(0, bw, bw, h)
            # 右边框
            painter.drawRect(final_w - bw, bw, bw, h)
            
        # 2. 绘制原始底图
        painter.drawPixmap(bw, bw, self.base_pixmap)
        
        # 3. 绘制所有标注（注意坐标平移，因为整体多了 bw 的位移）
        painter.translate(bw, bw)
        
        # 绘制矩形
        for info in self.rectangles:
            painter.setBrush(info['fill'])
            if info['border_enabled']:
                painter.setPen(QPen(info['border'], info['width']))
            else:
                painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(info['rect'], info['radius'], info['radius'])
            
        # 绘制所有直线
        for info in self.lines:
            painter.setPen(QPen(info['color'], info['width']))
            painter.drawLine(info['start'], info['end'])
            
        # 绘制所有箭头
        for info in self.arrows:
            self._draw_arrow(painter, info['start'], info['end'], info['color'], info['width'])
            
        # 绘制文字
        for idx, item in enumerate(self.text_items):
            text_color = item.get("color") or self.text_color
            text_font = QFont(item.get("font_family", self.text_font_family), item.get("font_size", self.text_font_size))
            painter.setFont(text_font)
            metrics = QFontMetrics(text_font)
            bounding = metrics.boundingRect(item["text"])
            bounding.moveTo(item["pos"])
            bg_color = item.get("background")
            if bg_color is None:
                bg_color = self.text_background_color
            bg_qcolor = QColor(bg_color)
            if bg_qcolor.isValid() and bg_qcolor.alpha() > 0:
                painter.setPen(Qt.NoPen)
                painter.setBrush(bg_qcolor)
                painter.drawRect(bounding.adjusted(-2, -2, 2, 2))
            painter.setPen(QColor(text_color))
            painter.drawText(item["pos"], item["text"])
            
        # 绘制标记
        marker_font = QFont()
        marker_font.setBold(True)
        for marker in self.markers:
            radius = marker['size']
            marker_font.setPixelSize(int(radius * marker['font_ratio']))
            painter.setFont(marker_font)
            ellipse_rect = QRect(marker['pos'].x() - radius, marker['pos'].y() - radius, radius * 2, radius * 2)
            painter.setBrush(marker['fill'])
            painter.drawEllipse(ellipse_rect)
            if marker['border_enabled']:
                painter.setPen(QPen(marker['border_color'], max(2, radius * 0.2)))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(ellipse_rect)
            painter.setBrush(marker['fill'])
            painter.setPen(Qt.white)
            painter.drawText(ellipse_rect, Qt.AlignCenter, str(marker['number']))
            
        painter.end()
        return annotated

    def _marker_hit_test(self, pos: QPoint):
        for idx, marker in enumerate(self.markers):
            radius = marker['size']
            ellipse_rect = QRect(marker['pos'].x() - radius, marker['pos'].y() - radius, radius * 2, radius * 2)
            if ellipse_rect.contains(pos):
                return idx
        return None

    def _shape_handle_hit_test(self, pos: QPoint):
        r = 10
        if self.selected_line_index is not None and self.selected_line_index < len(self.lines):
            info = self.lines[self.selected_line_index]
            if (pos - info['start']).manhattanLength() < r: return 'start'
            if (pos - info['end']).manhattanLength() < r: return 'end'
        if self.selected_arrow_index is not None and self.selected_arrow_index < len(self.arrows):
            info = self.arrows[self.selected_arrow_index]
            if (pos - info['start']).manhattanLength() < r: return 'start'
            if (pos - info['end']).manhattanLength() < r: return 'end'
        return None

    def _text_hit_test(self, pos: QPoint):
        for idx in reversed(range(len(self.text_items))):
            rect, _, _ = self._text_geometry(self.text_items[idx])
            if rect.adjusted(-6, -6, 6, 6).contains(pos):
                return idx
        return None

    def _text_geometry(self, item):
        font = QFont(
            item.get("font_family", self.text_font_family),
            item.get("font_size", self.text_font_size),
        )
        metrics = QFontMetrics(font)
        text = item.get("text") or ""
        width = max(1, metrics.horizontalAdvance(text))
        height = max(1, metrics.height())
        top_left = QPoint(item["pos"].x(), item["pos"].y() - metrics.ascent())
        rect = QRect(top_left, QSize(width, height))
        return rect, font, metrics

    def _rect_hit_test(self, pos: QPoint):
        for idx in reversed(range(len(self.rectangles))):
            info = self.rectangles[idx]
            if info['rect'].contains(pos) and not info['flattened']:
                return idx
        return None

    def _handle_rects(self, rect: QRect):
        half = self.HANDLE_SIZE // 2
        points = [rect.topLeft(), rect.topRight(), rect.bottomLeft(), rect.bottomRight()]
        return [QRect(p.x() - half, p.y() - half, self.HANDLE_SIZE, self.HANDLE_SIZE) for p in points]

    def _checkerboard_brush(self):
        if self._checker_brush is not None:
            return self._checker_brush
        size = CHECKER_TILE_SIZE
        tile = QPixmap(size, size)
        tile.fill(Qt.transparent)
        painter = QPainter(tile)
        light = QColor("#f5f5f5")
        dark = QColor("#e0e0e0")
        painter.fillRect(0, 0, size, size, light)
        painter.fillRect(0, 0, size // 2, size // 2, dark)
        painter.fillRect(size // 2, size // 2, size // 2, size // 2, dark)
        painter.end()
        self._checker_brush = QBrush(tile)
        return self._checker_brush

    def _draw_arrow(self, painter, start, end, color, width):
        painter.save()
        painter.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(color)
        
        # 绘制主干线
        painter.drawLine(start, end)
        
        # 计算箭头
        line = QLineF(start, end)
        if line.length() < 1:
            painter.restore()
            return
            
        angle = math.atan2(-line.dy(), line.dx())
        arrow_size = 15 + width * 2
        arrow_angle = math.pi / 6
        
        p1 = end + QPointF(math.cos(angle + math.pi - arrow_angle) * arrow_size,
                           -math.sin(angle + math.pi - arrow_angle) * arrow_size)
        p2 = end + QPointF(math.cos(angle + math.pi + arrow_angle) * arrow_size,
                           -math.sin(angle + math.pi + arrow_angle) * arrow_size)
        
        painter.drawPolygon(QPolygonF([end, p1, p2]))
        painter.restore()

    def _line_hit_test(self, pos: QPoint):
        # 检查点击是否在某条直线上（容差 10 像素）
        tolerance = 10
        for idx in reversed(range(len(self.lines))):
            info = self.lines[idx]
            p1 = info['start']
            p2 = info['end']
            p3 = pos
            
            # 简单的距离计算：点到线段的距离
            line_len_sq = (p2.x() - p1.x())**2 + (p2.y() - p1.y())**2
            if line_len_sq == 0:
                dist = math.sqrt((p3.x() - p1.x())**2 + (p3.y() - p1.y())**2)
            else:
                t = ((p3.x() - p1.x()) * (p2.x() - p1.x()) + (p3.y() - p1.y()) * (p2.y() - p1.y())) / line_len_sq
                t = max(0, min(1, t))
                proj_x = p1.x() + t * (p2.x() - p1.x())
                proj_y = p1.y() + t * (p2.y() - p1.y())
                dist = math.sqrt((p3.x() - proj_x)**2 + (p3.y() - proj_y)**2)
            
            if dist < tolerance:
                return idx
        return None

    def _arrow_hit_test(self, pos: QPoint):
        # 箭头也是基于直线逻辑
        tolerance = 10
        for idx in reversed(range(len(self.arrows))):
            info = self.arrows[idx]
            p1 = info['start']
            p2 = info['end']
            p3 = pos
            
            line_len_sq = (p2.x() - p1.x())**2 + (p2.y() - p1.y())**2
            if line_len_sq == 0:
                dist = math.sqrt((p3.x() - p1.x())**2 + (p3.y() - p1.y())**2)
            else:
                t = ((p3.x() - p1.x()) * (p2.x() - p1.x()) + (p3.y() - p1.y()) * (p2.y() - p1.y())) / line_len_sq
                t = max(0, min(1, t))
                proj_x = p1.x() + t * (p2.x() - p1.x())
                proj_y = p1.y() + t * (p2.y() - p1.y())
                dist = math.sqrt((p3.x() - proj_x)**2 + (p3.y() - proj_y)**2)
            
            if dist < tolerance:
                return idx
        return None

    def _handle_rect_handle_hit_test(self, pos: QPoint):
        handles = ['top-left', 'top-right', 'bottom-left', 'bottom-right']
        for idx in reversed(range(len(self.rectangles))):
            info = self.rectangles[idx]
            if info['flattened']:
                continue
            for handle_name, handle_rect in zip(handles, self._handle_rects(info['rect'])):
                if handle_rect.contains(pos):
                    return idx, handle_name
        return None, None

    def _resize_rect(self, initial_rect: QRect, handle: str, delta: QPoint, modifiers, min_size=None, bounds: QRect = None):
        """Resize a rect with fixed anchor (opposite corner), avoiding flips/jumps."""
        if min_size is None:
            min_size = getattr(self, "MIN_RECT_SIZE", 8)
        rect0 = QRect(initial_rect).normalized()
        dx, dy = delta.x(), delta.y()
        l, t, r, b = rect0.left(), rect0.top(), rect0.right(), rect0.bottom()

        if handle == 'top-left':
            new_l = l + dx
            new_t = t + dy
            if bounds:
                new_l = max(bounds.left(), new_l)
                new_t = max(bounds.top(), new_t)
            new_l = min(r - min_size, new_l)
            new_t = min(b - min_size, new_t)
            rect = QRect(QPoint(new_l, new_t), QPoint(r, b))
        elif handle == 'top-right':
            new_r = r + dx
            new_t = t + dy
            if bounds:
                new_r = min(bounds.right(), new_r)
                new_t = max(bounds.top(), new_t)
            new_r = max(l + min_size, new_r)
            new_t = min(b - min_size, new_t)
            rect = QRect(QPoint(l, new_t), QPoint(new_r, b))
        elif handle == 'bottom-left':
            new_l = l + dx
            new_b = b + dy
            if bounds:
                new_l = max(bounds.left(), new_l)
                new_b = min(bounds.bottom(), new_b)
            new_l = min(r - min_size, new_l)
            new_b = max(t + min_size, new_b)
            rect = QRect(QPoint(new_l, t), QPoint(r, new_b))
        else:  # bottom-right
            new_r = r + dx
            new_b = b + dy
            if bounds:
                new_r = min(bounds.right(), new_r)
                new_b = min(bounds.bottom(), new_b)
            new_r = max(l + min_size, new_r)
            new_b = max(t + min_size, new_b)
            rect = QRect(QPoint(l, t), QPoint(new_r, new_b))

        if modifiers & Qt.ControlModifier:
            w = rect.width()
            h = rect.height()
            size = min(w, h)
            if handle == 'top-left':
                rect = QRect(QPoint(rect.right() - size, rect.bottom() - size), rect.bottomRight())
            elif handle == 'top-right':
                rect = QRect(QPoint(rect.left(), rect.bottom() - size), QPoint(rect.left() + size, rect.bottom()))
            elif handle == 'bottom-left':
                rect = QRect(QPoint(rect.right() - size, rect.top()), QPoint(rect.right(), rect.top() + size))
            else:  # bottom-right
                rect.setSize(QSize(size, size))
        return rect

    def _set_hover_marker(self, idx):
        if self.hover_marker_index != idx:
            self.hover_marker_index = idx
            self.update()

    def _update_pointer_feedback(self, pos: QPoint):
        if self._marker_dragging:
            return
        if self.tool == Tool.SELECTION or self.tool == Tool.CROP:
            if self.selection_rect:
                handle = self._selection_handle_hit_test(pos)
                if handle in ("top-left", "bottom-right"):
                    self._update_cursor(Qt.SizeFDiagCursor)
                    return
                if handle in ("top-right", "bottom-left"):
                    self._update_cursor(Qt.SizeBDiagCursor)
                    return
                if self.selection_rect.contains(pos):
                    self._update_cursor(Qt.SizeAllCursor)
                    return
            self._update_cursor(Qt.CrossCursor)
            return
        allow_rect_cursor = self.tool != Tool.MARKER
        if allow_rect_cursor:
            idx, handle = self._handle_rect_handle_hit_test(pos)
            if idx is not None and handle:
                self._set_hover_marker(None)
                if handle in ("top-left", "bottom-right"):
                    self._update_cursor(Qt.SizeFDiagCursor)
                else:
                    self._update_cursor(Qt.SizeBDiagCursor)
                return
            idx = self._rect_hit_test(pos)
            if idx is not None:
                self._set_hover_marker(None)
                self._update_cursor(Qt.SizeAllCursor)
                return
        if not self.markers_flattened:
            marker_idx = self._marker_hit_test(pos)
            if marker_idx is not None:
                self._set_hover_marker(marker_idx)
                self._update_cursor(Qt.SizeAllCursor)
                return
        self._set_hover_marker(None)
        self._update_default_cursor()

    def perform_crop(self, rect: QRect):
        """执行裁切：裁剪底图并平移所有标注"""
        if not rect.isValid() or rect.isEmpty():
            return
        
        # region agent log
        _agent_debug_log(
            hypothesisId="H-UNDO",
            location="screenshot_tool.py:AnnotationCanvas.perform_crop",
            message="perform_crop called, pushing undo state",
            data={
                "crop_rect": [rect.x(), rect.y(), rect.width(), rect.height()],
                "current_pixmap_size": [self.base_pixmap.width(), self.base_pixmap.height()],
                "stack_size_before": len(self._undo_stack),
            },
        )
        # endregion
        self._push_undo_state()
        
        # 1. 裁剪底图
        new_pixmap = self.base_pixmap.copy(rect)
        self.base_pixmap = new_pixmap
        
        # 2. 计算偏移量
        offset = rect.topLeft()
        
        # 3. 平移并过滤矩形
        new_rects = []
        for r in self.rectangles:
            new_r = r.copy()
            # 平移矩形坐标
            translated_rect = r['rect'].translated(-offset)
            # 只保留与新视图有交集的部分
            intersected = translated_rect.intersected(QRect(0, 0, new_pixmap.width(), new_pixmap.height()))
            if not intersected.isEmpty() and intersected.width() > 2 and intersected.height() > 2:
                new_r['rect'] = intersected
                new_rects.append(new_r)
        self.rectangles = new_rects
        
        # 4. 平移并过滤标记
        new_markers = []
        for m in self.markers:
            new_pos = m['pos'] - offset
            # 只保留在新区域内的标记
            if QRect(0, 0, new_pixmap.width(), new_pixmap.height()).contains(new_pos):
                new_m = m.copy()
                new_m['pos'] = new_pos
                new_markers.append(new_m)
        self.markers = new_markers
        
        # 5. 平移并过滤文字
        new_texts = []
        for t in self.text_items:
            new_pos = t['pos'] - offset
            # 简单判断基准点是否在新区域内
            if QRect(0, 0, new_pixmap.width(), new_pixmap.height()).contains(new_pos):
                new_t = t.copy()
                new_t['pos'] = new_pos
                new_texts.append(new_t)
        self.text_items = new_texts
        
        # 6. 重置状态
        self.clear_active_selection()
        self.set_tool(Tool.RECTANGLE)  # 裁切完成后恢复到标注框工具
        self._apply_zoom()
        self._mark_backing_store_dirty()
        self.update()
        self.optionsUpdated.emit()

    def _update_default_cursor(self):
        if self.tool == Tool.MARKER:
            self._update_cursor(Qt.CrossCursor)
        elif self.tool == Tool.TEXT:
            self._update_cursor(Qt.IBeamCursor)
        elif self.tool in (Tool.SELECTION, Tool.CROP, Tool.LINE, Tool.ARROW):
            self._update_cursor(Qt.CrossCursor)
        else:
            self._update_cursor(Qt.ArrowCursor)

    def _update_cursor(self, cursor_shape):
        self.setCursor(cursor_shape)

    def _is_dragging_operation_active(self):
        if self.tool == Tool.SELECTION and self._selection_origin is not None:
            return True
        if self._text_dragging:
            return True
        if self.dragging_marker_index is not None and not self.markers_flattened:
            return True
        if self.rect_drag_mode and self.selected_rectangle_index is not None:
            return True
        return False

    def _maybe_auto_scroll(self, event):
        if not self._is_dragging_operation_active():
            return
        owner = getattr(self, "owner_tab", None)
        if owner and hasattr(owner, "request_canvas_autoscroll"):
            try:
                owner.request_canvas_autoscroll(event.pos())
            except Exception:
                pass

class AnnotationTab(QWidget):
    toolChanged = pyqtSignal(object)
    dirtyStateChanged = pyqtSignal(bool)
    def __init__(
        self,
        pixmap: QPixmap,
        save_dir: str,
        style_state,
        style_callback,
        image_quality,
        auto_save_enabled,
        source_path=None,
        initial_zoom=1.0,
    ):
        super().__init__()
        self.image_quality = self._clamp_quality(image_quality)
        self.auto_save_enabled = bool(auto_save_enabled)
        self.canvas = AnnotationCanvas(pixmap)
        self.canvas.owner_tab = self
        self.canvas.apply_style_defaults(
            style_state.get("marker"),
            style_state.get("rectangle"),
            style_state.get("text"),
            style_state.get("image_border"),
        )
        self.save_dir = save_dir
        if source_path:
            self.auto_saved_path = source_path
            self._external_source = True
        else:
            self.auto_saved_path = self._auto_save_pixmap(pixmap) or "未保存截图"
            self._external_source = False
        self.style_state = style_state
        self.style_callback = style_callback
        self._text_defaults = self._build_text_defaults(style_state.get("text"))
        self._apply_text_defaults_to_canvas()
        self._current_tool = Tool.NONE
        self.base_status_text = self._default_base_status_text()
        self.dirty = False
        layout = QVBoxLayout()

        def _make_tool_icon(kind: str, base_color: str, stroke_color: str) -> QIcon:
            # 64px 矢量底稿，配合 32px 展示，细节更平滑
            size = QSize(64, 64)
            scale = size.width() / 48.0
            sc = lambda v: float(v) * scale
            rectf = lambda x, y, w, h: QRectF(sc(x), sc(y), sc(w), sc(h))
            pix = QPixmap(size)
            pix.fill(Qt.transparent)
            painter = QPainter(pix)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)

            bg_rect = rectf(5, 6, 48 - 10, 48 - 16)
            bg_path = QPainterPath()
            bg_path.addRoundedRect(bg_rect, sc(12), sc(12))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(base_color))
            painter.drawPath(bg_path)

            stroke = QPen(QColor(stroke_color), sc(2.6), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            painter.setPen(stroke)
            painter.setBrush(QColor(stroke_color))
            if kind == "rect":
                painter.setBrush(Qt.NoBrush)
                painter.drawRoundedRect(rectf(12, 14, 24, 18), sc(8), sc(8))
            elif kind == "marker":
                outer = rectf(14, 12, 20, 20)
                painter.setPen(stroke)
                painter.setBrush(QColor(stroke_color))
                painter.drawEllipse(outer)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor("#ffffff"))
                inner_rect = rectf(18, 16, 12, 12)
                painter.drawEllipse(inner_rect)
                num_font = QFont()
                num_font.setBold(True)
                num_font.setPointSize(int(sc(10)))
                painter.setFont(num_font)
                painter.setPen(QColor(stroke_color))
                painter.drawText(inner_rect, Qt.AlignCenter, "1")
            elif kind == "text":
                font = QFont()
                font.setBold(True)
                font.setPointSize(int(sc(14)))
                painter.setFont(font)
                painter.drawText(pix.rect().adjusted(0, int(sc(6)), 0, -int(sc(12))), Qt.AlignCenter, "T")
            elif kind == "select":
                painter.setBrush(Qt.NoBrush)
                frame = rectf(11, 14, 26, 18)
                painter.setPen(QPen(QColor(stroke_color), sc(2.5), Qt.DashLine, Qt.RoundCap, Qt.RoundJoin))
                painter.drawRoundedRect(frame, sc(6), sc(6))
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(stroke_color))
                h = sc(6)
                handles = [
                    QRectF(frame.left() - h / 2, frame.top() - h / 2, h, h),  # top-left
                    QRectF(frame.right() - h / 2, frame.top() - h / 2, h, h),  # top-right
                    QRectF(frame.left() - h / 2, frame.bottom() - h / 2, h, h),  # bottom-left
                    QRectF(frame.right() - h / 2, frame.bottom() - h / 2, h, h),  # bottom-right
                ]
                for r in handles:
                    painter.drawRect(r)
            elif kind == "crop":
                painter.setBrush(Qt.NoBrush)
                # 裁切图标：两个相交的 L 型（模拟裁切框）
                painter.setPen(stroke)
                # 左上 L
                painter.drawLine(int(sc(10)), int(sc(18)), int(sc(10)), int(sc(10)))
                painter.drawLine(int(sc(10)), int(sc(10)), int(sc(18)), int(sc(10)))
                # 右下 L
                painter.drawLine(int(sc(30)), int(sc(38)), int(sc(38)), int(sc(38)))
                painter.drawLine(int(sc(38)), int(sc(38)), int(sc(38)), int(sc(30)))
                # 虚线矩形
                p2 = QPen(QColor(stroke_color), sc(1.5), Qt.DashLine)
                painter.setPen(p2)
                painter.drawRect(rectf(14, 14, 20, 20))
            elif kind == "clear":
                painter.save()
                painter.translate(sc(6), -sc(1))
                painter.rotate(-15)
                body = rectf(16, 18, 20, 12)
                top_strip = rectf(16, 18, 20, 5)
                painter.setPen(QPen(QColor("#0b1220"), sc(0.8)))
                painter.setBrush(QColor("#f3f4f6"))
                painter.drawRoundedRect(body, sc(3), sc(3))
                painter.setBrush(QColor(stroke_color))
                painter.drawRoundedRect(top_strip, sc(2.5), sc(2.5))
                painter.restore()
                painter.setPen(QPen(QColor(stroke_color), sc(2.2), Qt.SolidLine, Qt.RoundCap))
                painter.drawLine(QPointF(sc(22), sc(34)), QPointF(sc(36), sc(26)))
            elif kind == "delete":
                painter.setBrush(Qt.NoBrush)
                painter.drawRoundedRect(rectf(14, 18, 20, 16), sc(3), sc(3))
                painter.drawLine(QPointF(sc(14), sc(18)), QPointF(sc(34), sc(18)))
                painter.drawLine(QPointF(sc(18), sc(14)), QPointF(sc(30), sc(14)))
                painter.drawLine(QPointF(sc(20), sc(14)), QPointF(sc(20), sc(11)))
                painter.drawLine(QPointF(sc(28), sc(14)), QPointF(sc(28), sc(11)))
                painter.drawLine(QPointF(sc(20), sc(22)), QPointF(sc(20), sc(30)))
                painter.drawLine(QPointF(sc(24), sc(22)), QPointF(sc(24), sc(30)))
                painter.drawLine(QPointF(sc(28), sc(22)), QPointF(sc(28), sc(30)))
            elif kind == "duplicate":
                # 克隆图标：两个叠加的小纸片
                painter.setBrush(QColor(base_color))
                painter.drawRoundedRect(rectf(12, 12, 18, 20), sc(4), sc(4))
                painter.setBrush(QColor("#ffffff"))
                painter.drawRoundedRect(rectf(18, 18, 18, 20), sc(4), sc(4))
                painter.setPen(QPen(QColor(stroke_color), sc(2)))
                painter.drawRoundedRect(rectf(18, 18, 18, 20), sc(4), sc(4))
            elif kind == "line":
                painter.setBrush(Qt.NoBrush)
                painter.drawLine(QPointF(sc(12), sc(36)), QPointF(sc(36), sc(12)))
            elif kind == "arrow":
                painter.setBrush(QColor(stroke_color))
                painter.drawLine(QPointF(sc(12), sc(36)), QPointF(sc(36), sc(12)))
                # 绘制箭头小三角形
                painter.drawPolygon(QPolygonF([
                    QPointF(sc(36), sc(12)),
                    QPointF(sc(36), sc(22)),
                    QPointF(sc(26), sc(12))
                ]))
            elif kind == "save":
                painter.setBrush(Qt.NoBrush)
                painter.drawRoundedRect(rectf(14, 28, 20, 8), sc(3), sc(3))
                painter.drawLine(QPointF(sc(24), sc(14)), QPointF(sc(24), sc(28)))
                painter.drawLine(QPointF(sc(24), sc(26)), QPointF(sc(17), sc(20)))
                painter.drawLine(QPointF(sc(24), sc(26)), QPointF(sc(31), sc(20)))
            painter.end()
            return QIcon(pix)

        toolbar = QToolBar("工具栏")
        toolbar.setObjectName("AnnotationToolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(30, 30))
        toolbar.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        toolbar.setStyleSheet(
            """
            QToolBar#AnnotationToolbar {
                border: none;
                padding: 1px 0;
                margin-bottom: 1px;
            }
            QToolBar#AnnotationToolbar QToolButton {
                border-radius: 7px;
                padding: 3px 8px 4px;
                font-weight: 700;
                font-size: 11px;
                background: rgba(14,19,37,0.05);
                color: #0f172a;
                margin-right: 3px;
                min-height: 32px;
                min-width: 54px;
                qproperty-iconSize: 30px;
            }
            QToolBar#AnnotationToolbar QToolButton::menu-indicator { width: 0; height: 0; }
            QToolBar#AnnotationToolbar QToolButton#Tool_rect {
                background: rgba(95,39,205,0.18);
                color: #421aab;
            }
            QToolBar#AnnotationToolbar QToolButton#Tool_rect:checked {
                background: #5f27cd;
                color: #ffffff;
            }
            QToolBar#AnnotationToolbar QToolButton#Tool_marker {
                background: rgba(46,211,163,0.18);
                color: #0f6d57;
            }
            QToolBar#AnnotationToolbar QToolButton#Tool_marker:checked {
                background: #2ed3a3;
                color: #0c1c27;
            }
            QToolBar#AnnotationToolbar QToolButton#Tool_select {
                background: rgba(14,165,233,0.18);
                color: #075985;
            }
            QToolBar#AnnotationToolbar QToolButton#Tool_select:checked {
                background: #0ea5e9;
                color: #041724;
            }
            QToolBar#AnnotationToolbar QToolButton#Tool_text {
                background: rgba(247,181,0,0.18);
                color: #7a5007;
            }
            QToolBar#AnnotationToolbar QToolButton#Tool_text:checked {
                background: #f7b500;
                color: #060606;
            }
            QToolBar#AnnotationToolbar QToolButton#Tool_line {
                background: rgba(95,39,205,0.12);
                color: #5f27cd;
            }
            QToolBar#AnnotationToolbar QToolButton#Tool_line:checked {
                background: #5f27cd;
                color: #ffffff;
            }
            QToolBar#AnnotationToolbar QToolButton#Tool_arrow {
                background: rgba(255,71,87,0.12);
                color: #ff4757;
            }
            QToolBar#AnnotationToolbar QToolButton#Tool_arrow:checked {
                background: #ff4757;
                color: #ffffff;
            }
            QToolBar#AnnotationToolbar QToolButton#Tool_crop {
                background: rgba(185,28,28,0.18);
                color: #991b1b;
            }
            QToolBar#AnnotationToolbar QToolButton#Tool_crop:checked {
                background: #dc2626;
                color: #ffffff;
            }
            """
        )



        rect_action = QAction("标注框", self)
        rect_action.setIcon(_make_tool_icon("rect", "#e9ddff", "#5f27cd"))
        rect_action.setCheckable(True)
        rect_action.triggered.connect(lambda: self._set_tool(Tool.RECTANGLE))
        toolbar.addAction(rect_action)
        rect_button = toolbar.widgetForAction(rect_action)
        if rect_button:
            rect_button.setObjectName("Tool_rect")

        marker_action = QAction("顺序标记", self)
        marker_action.setIcon(_make_tool_icon("marker", "#d1fae5", "#0f6d57"))
        marker_action.setCheckable(True)
        marker_action.triggered.connect(lambda: self._set_tool(Tool.MARKER))
        toolbar.addAction(marker_action)
        marker_button = toolbar.widgetForAction(marker_action)
        if marker_button:
            marker_button.setObjectName("Tool_marker")

        text_action = QAction("插入文字", self)
        text_action.setIcon(_make_tool_icon("text", "#fef3c7", "#b45309"))
        text_action.setCheckable(True)
        text_action.triggered.connect(lambda: self._set_tool(Tool.TEXT))
        toolbar.addAction(text_action)
        text_button = toolbar.widgetForAction(text_action)
        if text_button:
            text_button.setObjectName("Tool_text")

        # --- 图形下拉菜单 ---
        shapes_button = QToolButton(self)
        shapes_button.setText("图形")
        shapes_button.setIcon(_make_tool_icon("line", "#e9ddff", "#5f27cd")) # 默认图标
        shapes_button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        shapes_button.setPopupMode(QToolButton.InstantPopup)
        shapes_button.setObjectName("Tool_shapes")
        
        shapes_menu = QMenu(shapes_button)
        shapes_menu.setStyleSheet("""
            QMenu {
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 4px;
            }
            QMenu::item {
                padding: 8px 24px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #f1f5f9;
                color: #0f172a;
            }
        """)

        line_action = QAction("直线", self)
        line_action.setIcon(_make_tool_icon("line", "#e9ddff", "#5f27cd"))
        line_action.setCheckable(True)
        line_action.triggered.connect(lambda: self._set_tool(Tool.LINE))
        shapes_menu.addAction(line_action)

        arrow_action = QAction("箭头", self)
        arrow_action.setIcon(_make_tool_icon("arrow", "#ffe2e2", "#ff4757"))
        arrow_action.setCheckable(True)
        arrow_action.triggered.connect(lambda: self._set_tool(Tool.ARROW))
        shapes_menu.addAction(arrow_action)

        shapes_button.setMenu(shapes_menu)
        toolbar.addWidget(shapes_button)

        # 保持对这些 action 的引用以进行状态同步
        self._shape_actions = {
            Tool.LINE: line_action,
            Tool.ARROW: arrow_action,
        }

        select_action = QAction("选区", self)
        select_action.setIcon(_make_tool_icon("select", "#dbeafe", "#0ea5e9"))
        select_action.setCheckable(True)
        select_action.triggered.connect(lambda: self._set_tool(Tool.SELECTION))
        toolbar.addAction(select_action)
        select_button = toolbar.widgetForAction(select_action)
        if select_button:
            select_button.setObjectName("Tool_select")

        crop_action = QAction("裁切", self)
        crop_action.setIcon(_make_tool_icon("crop", "#fee2e2", "#b91c1c"))
        crop_action.setCheckable(True)
        crop_action.triggered.connect(lambda: self._set_tool(Tool.CROP))
        toolbar.addAction(crop_action)
        crop_button = toolbar.widgetForAction(crop_action)
        if crop_button:
            crop_button.setObjectName("Tool_crop")

        clear_action = QAction("清除标注", self)
        clear_action.setIcon(_make_tool_icon("clear", "#e5e7eb", "#111827"))
        clear_action.triggered.connect(self.canvas.clear_annotations)
        toolbar.addAction(clear_action)

        delete_action = QAction("删除选中", self)
        delete_action.setIcon(_make_tool_icon("delete", "#f3f4f6", "#b91c1c"))
        delete_action.triggered.connect(self._delete_selected)
        toolbar.addAction(delete_action)

        duplicate_action = QAction("克隆标注", self)
        duplicate_action.setIcon(_make_tool_icon("duplicate", "#e0f2fe", "#0f172a"))
        duplicate_action.triggered.connect(self._duplicate_active_shape)
        toolbar.addAction(duplicate_action)

        save_action = QAction("保存标注图", self)
        save_action.setIcon(_make_tool_icon("save", "#ede9fe", "#4338ca"))
        save_action.triggered.connect(self.save_annotated_image)
        toolbar.addAction(save_action)

        self._tool_actions = {
            Tool.RECTANGLE: rect_action,
            Tool.MARKER: marker_action,
            Tool.TEXT: text_action,
            Tool.LINE: line_action,
            Tool.ARROW: arrow_action,
            Tool.SELECTION: select_action,
            Tool.CROP: crop_action,
        }
        layout.addWidget(toolbar)

        self.selection_panel = SelectionOptionsPanel(self.canvas)
        self.marker_panel = MarkerOptionsPanel(self.canvas)
        self.rectangle_panel = RectangleOptionsPanel(self.canvas)
        self.text_panel = TextOptionsPanel(self)
        self.shape_panel = ShapeOptionsPanel(self.canvas)
        self._options_placeholder = QWidget()
        self._options_placeholder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.panel_stack = QStackedWidget()
        self.panel_stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.panel_stack.addWidget(self._options_placeholder)
        self.panel_stack.addWidget(self.selection_panel)
        self.panel_stack.addWidget(self.marker_panel)
        self.panel_stack.addWidget(self.rectangle_panel)
        self.panel_stack.addWidget(self.text_panel)
        self.panel_stack.addWidget(self.shape_panel)
        
        # 裁切选项面板（复用选区面板样式）
        self.crop_panel = QFrame()
        self.crop_panel.setObjectName("CropPanel")
        self.crop_panel.setStyleSheet("background: rgba(185,28,28,0.08); border-radius: 10px;")
        crop_layout = QHBoxLayout(self.crop_panel)
        crop_layout.setContentsMargins(12, 8, 12, 8)
        crop_lbl = QLabel("裁切模式: 拖动边缘选择区域，松开鼠标立即裁切")
        crop_lbl.setStyleSheet("color: #b91c1c; font-weight: bold; font-size: 12px;")
        crop_layout.addWidget(crop_lbl)
        crop_layout.addStretch()
        self.panel_stack.addWidget(self.crop_panel)

        stack_height = max(
            self.selection_panel.sizeHint().height(),
            self.marker_panel.sizeHint().height(),
            self.rectangle_panel.sizeHint().height(),
            self.text_panel.sizeHint().height(),
            self.crop_panel.sizeHint().height(),
        )
        self.panel_stack.setFixedHeight(stack_height)
        self._options_placeholder.setFixedHeight(stack_height)
        self.selection_panel.setMinimumHeight(stack_height)
        self.marker_panel.setMinimumHeight(stack_height)
        self.rectangle_panel.setMinimumHeight(stack_height)
        self.text_panel.setMinimumHeight(stack_height)

        layout.addWidget(self.panel_stack)

        scroll = QScrollArea()
        scroll.setWidgetResizable(False)
        scroll.setWidget(self.canvas)
        scroll.viewport().installEventFilter(self)
        self.canvas.installEventFilter(self)
        self._scroll_area = scroll
        layout.addWidget(scroll, 1)

        status_layout = QHBoxLayout()
        self.status_label = QLabel(self.base_status_text)
        self.zoom_label = QLabel("100%")
        self.zoom_label.setStyleSheet("color: #4c566a;")
        status_layout.addWidget(self.status_label, 1)
        status_layout.addWidget(self.zoom_label, 0, alignment=Qt.AlignRight)
        layout.addLayout(status_layout)
        self.setLayout(layout)
        self.canvas.optionsUpdated.connect(self._handle_canvas_update)
        self._update_panel_visibility()
        self._persist_style_defaults()
        if not self._external_source and not self.auto_save_enabled:
            self.dirty = True

        self.undo_shortcut = QShortcut(QKeySequence("Ctrl+Z"), self)
        self.undo_shortcut.activated.connect(self._undo_last_action)
        self.copy_shortcut = QShortcut(QKeySequence("Ctrl+C"), self)
        self.copy_shortcut.activated.connect(self._copy_to_clipboard)
        self.delete_shortcut = QShortcut(QKeySequence("Delete"), self)
        self.delete_shortcut.activated.connect(self._delete_selected)
        self.save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        self.save_shortcut.activated.connect(self.save_annotated_image)
        self.escape_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self.escape_shortcut.activated.connect(self._handle_escape)
        self.canvas.zoomChanged.connect(self._on_zoom_changed)
        self.reset_zoom_shortcut = QShortcut(QKeySequence("Ctrl+0"), self)
        self.reset_zoom_shortcut.activated.connect(self.canvas.reset_zoom)
        self.canvas.set_zoom(initial_zoom)

    def _build_text_defaults(self, values):
        defaults = DEFAULT_TEXT_STYLE.copy()
        values = values or {}
        defaults["color"] = values.get("color", defaults["color"])
        defaults["background"] = values.get("background", defaults["background"])
        defaults["font"] = _sanitize_font_family(values.get("font", defaults["font"]))
        try:
            defaults["size"] = int(values.get("size", defaults["size"]))
        except (TypeError, ValueError):
            defaults["size"] = DEFAULT_TEXT_STYLE["size"]
        return defaults

    def _apply_text_defaults_to_canvas(self):
        color = QColor(self._text_defaults["color"])
        background = QColor(self._text_defaults["background"])
        self.canvas.set_text_color(color)
        self.canvas.set_text_background_color(background)
        self.canvas.set_text_font_family(self._text_defaults["font"])
        self.canvas.set_text_font_size(int(self._text_defaults["size"]))

    def on_canvas_text_selection(self, item):
        if item:
            self.canvas.apply_text_style(item)
        else:
            self._apply_text_defaults_to_canvas()
        self.on_canvas_pixels_changed()

    def on_canvas_pixels_changed(self):
        self._mark_dirty()

    def on_text_color_changed(self, color: QColor):
        if self.canvas._has_active_text():
            self.canvas.update_selected_text_color(color)
            return
        if not color or not color.isValid():
            return
        self._text_defaults["color"] = color.name(QColor.HexArgb)
        self.canvas.set_text_color(color)
        self._persist_style_defaults()

    def on_text_background_changed(self, color: QColor):
        if self.canvas._has_active_text():
            self.canvas.update_selected_text_background(color)
            return
        if not color or not color.isValid():
            return
        self._text_defaults["background"] = color.name(QColor.HexArgb)
        self.canvas.set_text_background_color(color)
        self._persist_style_defaults()

    def on_text_font_family_changed(self, family: str):
        family = _sanitize_font_family(family)
        if self.canvas._has_active_text():
            self.canvas.update_selected_text_font_family(family)
            return
        self._text_defaults["font"] = family
        self.canvas.set_text_font_family(family)
        self._persist_style_defaults()

    def on_text_font_size_changed(self, size: int):
        if self.canvas._has_active_text():
            self.canvas.update_selected_text_font_size(size)
            return
        try:
            size = int(size)
        except (TypeError, ValueError):
            return
        size = max(8, min(72, size))
        self._text_defaults["size"] = size
        self.canvas.set_text_font_size(size)
        self._persist_style_defaults()

    def update_text_color_default(self, color: QColor):
        if not color or not color.isValid():
            return
        self._text_defaults["color"] = color.name(QColor.HexArgb)
        self.canvas.set_text_color(color)
        self._persist_style_defaults()

    def update_text_background_default(self, color: QColor):
        if not color or not color.isValid():
            return
        self._text_defaults["background"] = color.name(QColor.HexArgb)
        self.canvas.set_text_background_color(color)
        self._persist_style_defaults()

    def update_text_font_family_default(self, family: str):
        family = _sanitize_font_family(family)
        if not family:
            return
        self._text_defaults["font"] = family
        self.canvas.set_text_font_family(family)
        self._persist_style_defaults()

    def update_text_font_size_default(self, size: int):
        try:
            size = int(size)
        except (TypeError, ValueError):
            return
        size = max(8, min(72, size))
        self._text_defaults["size"] = size
        self.canvas.set_text_font_size(size)
        self._persist_style_defaults()

    def _clamp_quality(self, value):
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = DEFAULT_IMAGE_QUALITY
        return max(10, min(100, value))

    def set_image_quality(self, value):
        self.image_quality = self._clamp_quality(value)

    def _auto_save_pixmap(self, pixmap: QPixmap):
        target_dir = self.save_dir or DEFAULT_SAVE_DIR
        errors = []
        try:
            os.makedirs(target_dir, exist_ok=True)
        except OSError as exc:
            errors.append((target_dir, exc))
            fallback_dir = DEFAULT_SAVE_DIR
            if target_dir != fallback_dir:
                try:
                    os.makedirs(fallback_dir, exist_ok=True)
                    QMessageBox.warning(
                        self,
                        "保存目录不可用",
                        f"无法写入截图目录：{target_dir}\n已切换到默认目录：{fallback_dir}\n\n系统信息: {exc}",
                    )
                    target_dir = fallback_dir
                    self.save_dir = fallback_dir
                except OSError as fallback_exc:
                    errors.append((fallback_dir, fallback_exc))
                    temp_dir = os.path.join(tempfile.gettempdir(), "ctk_snapshot")
                    try:
                        os.makedirs(temp_dir, exist_ok=True)
                        QMessageBox.warning(
                            self,
                            "保存目录不可用",
                            f"无法写入截图目录：{target_dir}\n已切换到临时目录：{temp_dir}\n\n系统信息: {exc}\n{fallback_exc}",
                        )
                        target_dir = temp_dir
                        self.save_dir = temp_dir
                    except OSError as temp_exc:
                        errors.append((temp_dir, temp_exc))
                        combined = "\n".join(f"{path}: {err}" for path, err in errors)
                        QMessageBox.critical(
                            self,
                            "保存失败",
                            f"无法写入任何截图目录，请检查权限或磁盘空间。\n\n{combined}",
                        )
                        return None
            else:
                temp_dir = os.path.join(tempfile.gettempdir(), "ctk_snapshot")
                try:
                    os.makedirs(temp_dir, exist_ok=True)
                    QMessageBox.warning(
                        self,
                        "保存目录不可用",
                        f"无法写入截图目录：{target_dir}\n已切换到临时目录：{temp_dir}\n\n系统信息: {exc}",
                    )
                    target_dir = temp_dir
                    self.save_dir = temp_dir
                except OSError as temp_exc:
                    errors.append((temp_dir, temp_exc))
                    combined = "\n".join(f"{path}: {err}" for path, err in errors)
                    QMessageBox.critical(
                        self,
                        "保存失败",
                        f"无法写入任何截图目录，请检查权限或磁盘空间。\n\n{combined}",
                    )
                    return None
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"screenshot_{timestamp}.png"
        path = os.path.join(target_dir, filename)
        if self.auto_save_enabled:
            if not pixmap.save(path, "PNG"):
                QMessageBox.warning(self, "保存失败", f"无法自动保存截图到 {path}")
        return path

    def save_annotated_image(self):
        if self.canvas.markers and not self.canvas.markers_flattened:
            self.canvas.flatten_all_annotations()
        annotated = self.canvas.export_pixmap()
        base, _ = os.path.splitext(os.path.basename(self.auto_saved_path))
        annotated_path = os.path.join(self.save_dir, f"{base}_annotated.png")
        if annotated.save(annotated_path, "PNG", self.image_quality):
            self.status_label.setText(f"标注图已保存: {annotated_path}")
            self._set_dirty(False)
            return True
        QMessageBox.warning(self, "保存失败", "无法写入标注截图，请检查保存路径。")
        return False

    def _set_tool(self, tool: Tool):
        previous = getattr(self, "_current_tool", None)
        self._current_tool = tool
        # region agent log
        _agent_debug_log(
            hypothesisId="H1-H5",
            location="screenshot_tool.py:AnnotationTab._set_tool",
            message="tool changed",
            data={
                "previous_tool": previous.name if previous else None,
                "new_tool": tool.name if tool else None,
                "is_crop": tool == Tool.CROP,
            },
        )
        # endregion
        self.canvas.clear_active_selection(emit=False)
        self.canvas.set_tool(tool)
        self._sync_tool_action_checks(tool)
        self._update_panel_visibility(preferred=tool)
        if tool == Tool.TEXT and not self.canvas._has_active_text():
            self._apply_text_defaults_to_canvas()
        if tool != previous:
            try:
                self.toolChanged.emit(tool)
            except Exception:
                pass

    def _handle_canvas_update(self):
        self._mark_dirty()
        self._update_panel_visibility()
        # 每次画布更新时自动持久化样式设置
        self._persist_style_defaults()
    
    def _handle_escape(self):
        # region agent log
        # H1/H2: Esc 是否触发到了这里？当时工具/选区/拖拽/焦点是什么？
        try:
            focus = QApplication.focusWidget()
            focus_name = focus.__class__.__name__ if focus else None
        except Exception:
            focus_name = None
        _agent_debug_log(
            hypothesisId="H2",
            location="screenshot_tool.py:AnnotationTab._handle_escape",
            message="Esc shortcut activated",
            data={
                "_current_tool": getattr(self, "_current_tool", None).name if getattr(self, "_current_tool", None) else None,
                "active_selection_kind": self.canvas.active_selection_kind() if hasattr(self, "canvas") else None,
                "markers_flattened": bool(getattr(self.canvas, "markers_flattened", True)),
                "dragging_marker_index": getattr(self.canvas, "dragging_marker_index", None),
                "_marker_dragging": bool(getattr(self.canvas, "_marker_dragging", False)),
                "focusWidget": focus_name,
            },
        )
        # endregion
        self.canvas.clear_active_selection()
        if self._current_tool in (Tool.MARKER, Tool.TEXT, Tool.SELECTION):
            self._set_tool(Tool.NONE)

    def _on_zoom_changed(self, factor):
        if hasattr(self, "zoom_label"):
            percent = int(round(factor * 100))
            self.zoom_label.setText(f"{percent}%")

    def _update_panel_visibility(self, preferred=None):
        # 如果当前工具是 CROP，优先保持 CROP 状态
        if self._current_tool == Tool.CROP:
            target = Tool.CROP
        else:
            kind = self.canvas.active_selection_kind()
            if kind == "pixel_selection":
                target = Tool.SELECTION
            elif kind == "marker":
                target = Tool.MARKER
            elif kind == "rectangle":
                target = Tool.RECTANGLE
            elif kind == "text":
                target = Tool.TEXT
            elif kind == "line":
                target = Tool.LINE
            elif kind == "arrow":
                target = Tool.ARROW
            else:
                target = preferred or self._current_tool
        if target == Tool.MARKER:
            self.panel_stack.setCurrentWidget(self.marker_panel)
            self._set_panel_active_state(marker=True)
        elif target == Tool.RECTANGLE:
            self.panel_stack.setCurrentWidget(self.rectangle_panel)
            self._set_panel_active_state(rectangle=True)
        elif target == Tool.SELECTION:
            self.panel_stack.setCurrentWidget(self.selection_panel)
            self._set_panel_active_state(selection=True)
        elif target == Tool.TEXT:
            self.panel_stack.setCurrentWidget(self.text_panel)
            self._set_panel_active_state(text=True)
        elif target == Tool.CROP:
            self.panel_stack.setCurrentWidget(self.crop_panel)
            self._set_panel_active_state()
        elif target in (Tool.LINE, Tool.ARROW):
            self.panel_stack.setCurrentWidget(self.shape_panel)
            self._set_panel_active_state(shape=True)
        else:
            self.panel_stack.setCurrentWidget(self._options_placeholder)
            self._set_panel_active_state()
        tracking = target if target in (Tool.MARKER, Tool.RECTANGLE, Tool.TEXT, Tool.SELECTION, Tool.CROP, Tool.LINE, Tool.ARROW) else Tool.NONE
        self._sync_tool_action_checks(tracking)

    def _set_panel_active_state(self, marker=False, rectangle=False, text=False, selection=False, shape=False):
        if hasattr(self.selection_panel, "setVisible"):
            self.selection_panel.setVisible(bool(selection))
        if hasattr(self.marker_panel, "set_panel_active"):
            self.marker_panel.set_panel_active(bool(marker))
        if hasattr(self.rectangle_panel, "set_panel_active"):
            self.rectangle_panel.set_panel_active(bool(rectangle))
        if hasattr(self.text_panel, "set_panel_active"):
            self.text_panel.set_panel_active(bool(text))
        if hasattr(self.shape_panel, "set_panel_active"):
            self.shape_panel.set_panel_active(bool(shape))

    def _sync_tool_action_checks(self, active_tool: Tool):
        actions = getattr(self, "_tool_actions", {})
        for tool, action in actions.items():
            action.blockSignals(True)
            action.setChecked(tool == active_tool)
            action.blockSignals(False)
            
        # 同步图形菜单项
        shape_actions = getattr(self, "_shape_actions", {})
        for tool, action in shape_actions.items():
            action.blockSignals(True)
            action.setChecked(tool == active_tool)
            action.blockSignals(False)
            
        # 如果是图形工具，更新图形按钮的高亮状态
        shapes_button = self.findChild(QToolButton, "Tool_shapes")
        if shapes_button:
            is_shape = active_tool in shape_actions
            if is_shape:
                # 更新按钮图标为当前选中的图形图标
                shapes_button.setIcon(shape_actions[active_tool].icon())
                shapes_button.setStyleSheet("background: rgba(95,39,205,0.18); color: #421aab;")
            else:
                shapes_button.setStyleSheet("")

    def _duplicate_active_shape(self):
        if not self.canvas.duplicate_active_shape():
            self.status_label.setText("请先选中一个标注再复制")

    def request_canvas_autoscroll(self, canvas_pos: QPoint):
        scroll = getattr(self, "_scroll_area", None)
        if not scroll or not hasattr(scroll, "viewport"):
            return
        viewport = scroll.viewport()
        if viewport is None:
            return
        view_pos = self.canvas.mapTo(viewport, canvas_pos)
        margin = 36
        max_step = 28
        width = max(1, viewport.width())
        height = max(1, viewport.height())
        right_edge = max(margin, width - margin)
        bottom_edge = max(margin, height - margin)

        dx = 0
        dy = 0
        if view_pos.x() < margin:
            dx = view_pos.x() - margin
        elif view_pos.x() > right_edge:
            dx = view_pos.x() - right_edge
        if view_pos.y() < margin:
            dy = view_pos.y() - margin
        elif view_pos.y() > bottom_edge:
            dy = view_pos.y() - bottom_edge

        if dx == 0 and dy == 0:
            return

        def _step(delta):
            magnitude = min(max_step, int(abs(delta) * 0.6) + 2)
            return -magnitude if delta < 0 else magnitude

        if dx:
            hbar = scroll.horizontalScrollBar()
            hbar.setValue(hbar.value() + _step(dx))
        if dy:
            vbar = scroll.verticalScrollBar()
            vbar.setValue(vbar.value() + _step(dy))

    def eventFilter(self, obj, event):
        viewport = getattr(self, "_scroll_area", None)
        viewport_widget = viewport.viewport() if viewport else None
        if (
            event.type() == QEvent.Wheel
            and obj in (self.canvas, viewport_widget)
            and event.modifiers() & Qt.ControlModifier
        ):
            delta = event.angleDelta().y() or event.pixelDelta().y()
            if delta == 0:
                return True
            if delta > 0:
                self.canvas.zoom_in()
            elif delta < 0:
                self.canvas.zoom_out()
            return True
        return super().eventFilter(obj, event)

    def _mark_dirty(self):
        self.status_label.setText(f"{self.base_status_text} *未保存")
        self._persist_style_defaults()
        self._set_dirty(True)

    def _set_dirty(self, dirty):
        if self.dirty != dirty:
            self.dirty = dirty
            self.dirtyStateChanged.emit(self.dirty)
        if not dirty:
            self.base_status_text = self._default_base_status_text()
            self.status_label.setText(self.base_status_text)

    def set_auto_save_enabled(self, enabled):
        enabled = bool(enabled)
        if self.auto_save_enabled == enabled:
            return
        self.auto_save_enabled = enabled
        if not self._external_source and self.auto_save_enabled:
            self.auto_saved_path = self._auto_save_pixmap(self.canvas.base_pixmap) or self.auto_saved_path
        self.base_status_text = self._default_base_status_text()
        if not self.dirty:
            self.status_label.setText(self.base_status_text)

    def _default_base_status_text(self):
        if self._external_source:
            return f"原始文件: {self.auto_saved_path}"
        if self.auto_save_enabled:
            return f"自动保存: {self.auto_saved_path}"
        return f"尚未保存: {self.auto_saved_path}"

    def _safe_clipboard_basename(self):
        raw = os.path.splitext(os.path.basename(self.auto_saved_path or ""))[0]
        cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._") or "screenshot"
        return cleaned

    def _export_clipboard_image(self, flatten=False, target_dir=None, stamp=None, index=None):
        if flatten:
            self.canvas.flatten_all_annotations()
        pix = self.canvas.export_pixmap()
        image = pix.toImage().convertToFormat(QImage.Format_ARGB32)
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        image.save(buffer, "PNG")
        png_bytes = bytes(buffer.data())
        buffer.close()
        target_dir = target_dir or os.path.join(tempfile.gettempdir(), "ctk_snapshot_clipboard")
        try:
            os.makedirs(target_dir, exist_ok=True)
        except OSError:
            target_dir = tempfile.gettempdir()
        stamp = stamp or datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        suffix = f"_{index:02d}" if index is not None else ""
        filename = f"{self._safe_clipboard_basename()}_{stamp}{suffix}.png"
        path = os.path.join(target_dir, filename)
        image.save(path, "PNG")
        return path, png_bytes, image


    def _undo_last_action(self):
        if self.canvas.undo_last_shape():
            self.status_label.setText("已撤销上一次操作")
            self._mark_dirty()
        else:
            QMessageBox.information(self, "无法撤销", "当前没有可撤销的操作。")

    def _copy_to_clipboard(self):
        # region agent log
        # H3/H5: 复制前的状态（尤其是 Esc 失败后的状态机）与焦点
        try:
            focus = QApplication.focusWidget()
            focus_name = focus.__class__.__name__ if focus else None
        except Exception:
            focus_name = None
        _agent_debug_log(
            hypothesisId="H5",
            location="screenshot_tool.py:AnnotationTab._copy_to_clipboard",
            message="copy_to_clipboard begin",
            data={
                "_current_tool": getattr(self, "_current_tool", None).name if getattr(self, "_current_tool", None) else None,
                "active_selection_kind": self.canvas.active_selection_kind() if hasattr(self, "canvas") else None,
                "markers_flattened": bool(getattr(self.canvas, "markers_flattened", True)),
                "dragging_marker_index": getattr(self.canvas, "dragging_marker_index", None),
                "_marker_dragging": bool(getattr(self.canvas, "_marker_dragging", False)),
                "focusWidget": focus_name,
            },
        )
        # endregion
        # 临时 PNG 文件，便于部分应用（PPT/笔记）读取透明通道
        tmp_path, png_bytes, _ = self._export_clipboard_image(flatten=True)
        # region agent log
        # H3/H5: 临时文件与 PNG 字节是否有效（PPT 若优先读 URL，文件是否存在/大小是否为 0）
        try:
            exists = os.path.exists(tmp_path) if tmp_path else False
            fsize = os.path.getsize(tmp_path) if exists else None
        except Exception:
            exists, fsize = False, None
        _agent_debug_log(
            hypothesisId="H3",
            location="screenshot_tool.py:AnnotationTab._copy_to_clipboard",
            message="clipboard export done",
            data={
                "tmp_path": tmp_path,
                "file_exists": bool(exists),
                "file_size": fsize,
                "png_bytes_len": len(png_bytes) if png_bytes else 0,
            },
        )
        # endregion
        mime = QMimeData()
        mime.setData("image/png", png_bytes)
        mime.setUrls([QUrl.fromLocalFile(tmp_path)])
        clipboard = QApplication.clipboard()
        ok = _set_clipboard_mime_with_retry(mime)
        # region agent log
        # H3: 写入剪贴板是否成功？写入后的 formats 是什么？（避免泄露内容，仅记录类型）
        try:
            fmts = list(clipboard.mimeData().formats()) if clipboard and clipboard.mimeData() else []
        except Exception:
            fmts = []
        _agent_debug_log(
            hypothesisId="H3",
            location="screenshot_tool.py:AnnotationTab._copy_to_clipboard",
            message="clipboard setMimeData result",
            data={
                "ok": bool(ok),
                "formats": fmts[:20],
                "has_image_png": "image/png" in fmts,
                "has_urls": "text/uri-list" in fmts,
            },
        )
        # endregion
        if ok:
            self.status_label.setText("已复制到剪贴板")
            self._mark_dirty()
            # region agent log
            # H1: 复制完成后延迟检查滚动位置是否变化
            def _delayed_scroll_check():
                try:
                    workspace = self.parent()
                    while workspace and not hasattr(workspace, '_current_scroll_offset'):
                        workspace = workspace.parent()
                    if workspace:
                        _agent_debug_log(
                            hypothesisId="H1",
                            location="screenshot_tool.py:AnnotationTab._copy_to_clipboard._delayed_scroll_check",
                            message="copy finished delayed scroll check",
                            data={
                                "offset_now": workspace._current_scroll_offset(),
                                "visible_range": workspace._tab_visible_range(),
                                "currentIndex": workspace.tabs.currentIndex(),
                            },
                        )
                except Exception:
                    pass
            QTimer.singleShot(50, _delayed_scroll_check)
            # endregion
        else:
            self.status_label.setText("复制到剪贴板失败，请重试。")
            QMessageBox.warning(self, "复制失败", "无法写入剪贴板，请稍后重试。")

    def _delete_selected(self):
        if self.canvas.selection_rect:
            if self.canvas.clear_selection_pixels():
                self.status_label.setText("选区已清空")
                self._mark_dirty()
            return
        if self.canvas.delete_selected_shape():
            self.status_label.setText("删除了当前选中")
            self._mark_dirty()
        else:
            QApplication.beep()

    def _persist_style_defaults(self):
        if not self.style_callback:
            return
        marker_style = self.canvas.marker_style_state()
        rect_style = self.canvas.rectangle_style_state()
        text_style = self.canvas.text_style_state()
        self.style_callback("marker", marker_style)
        self.style_callback("rectangle", rect_style)
        self.style_callback("text", text_style)

    def maybe_close(self):
        if not self.dirty:
            return True
        reply = QMessageBox.question(
            self,
            "保存截图",
            "当前截图有未保存的修改，是否保存？",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
        )
        if reply == QMessageBox.Yes:
            return self.save_annotated_image()
        if reply == QMessageBox.Cancel:
            return False
        return True


class SelectionOptionsPanel(QFrame):
    def __init__(self, canvas: AnnotationCanvas):
        super().__init__()
        self.canvas = canvas
        self.setObjectName("SelectionPanel")
        self._accent = QColor("#0ea5e9")
        self.setStyleSheet(
            """
            QFrame#SelectionPanel {
                background: rgba(14,165,233,0.08);
                border-radius: 10px;
            }
            QFrame#SelectionPanel QPushButton {
                background: #0ea5e9;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 6px 10px;
                font-weight: 600;
            }
            """
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        title = QLabel("选区操作")
        title.setStyleSheet("font-weight: 700; color: #0f172a;")
        layout.addWidget(title)

        self.color_btn = QPushButton("填充色")
        self.color_btn.clicked.connect(self._choose_color)
        self._update_color_button()
        layout.addWidget(self.color_btn)

        fill_btn = QPushButton("填充选区")
        fill_btn.setStyleSheet("background: #0ea5e9; color: #ffffff; border: none; border-radius: 6px; padding: 6px 10px; font-weight: 600;")
        fill_btn.clicked.connect(lambda: self.canvas.fill_selection_pixels(self.canvas.selection_fill_color))
        layout.addWidget(fill_btn)

        clear_btn = QPushButton("清除选区")
        clear_btn.setStyleSheet("background: #0284c7; color: #ffffff; border: none; border-radius: 6px; padding: 6px 10px; font-weight: 600;")
        clear_btn.clicked.connect(self.canvas.clear_selection_pixels)
        layout.addWidget(clear_btn)

        hint = QLabel("拖拽创建选区后，可清除为透明或填充颜色。")
        hint.setStyleSheet("color: #6b7280;")
        layout.addWidget(hint, 1)

    def _update_color_button(self):
        color = self.canvas.selection_fill_color
        if not color or not color.isValid():
            color = QColor("#FFFFFFFF")
            self.canvas.selection_fill_color = color
        self.color_btn.setStyleSheet(
            f"QPushButton {{ background-color: {color.name(QColor.HexArgb)}; color: #0f172a; border: 1px solid #cbd5e1; border-radius: 6px; padding: 6px 10px; }}"
        )

    def _choose_color(self):
        color = QColorDialog.getColor(self.canvas.selection_fill_color, self, "选择填充颜色")
        if color.isValid():
            self.canvas.selection_fill_color = QColor(color)
            self._update_color_button()


class ShapeOptionsPanel(QFrame):
    def __init__(self, canvas: AnnotationCanvas):
        super().__init__()
        self.canvas = canvas
        self.setObjectName("ShapePanel")
        self._accent = QColor("#5f27cd")
        self.palette_buttons = []
        self._apply_style()

        layout = QVBoxLayout()
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)

        row = QHBoxLayout()
        row.setSpacing(10)

        palette_layout = QHBoxLayout()
        palette_layout.setSpacing(4)
        palette_label = QLabel("颜色")
        palette_label.setStyleSheet("color:#2b3245;font-weight:600;font-size:12px;")
        palette_layout.addWidget(palette_label)
        for hex_color in CLASSIC_COLORS:
            btn = QPushButton()
            btn.setFixedSize(20, 20)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(f"background-color:{hex_color}; border-radius:6px; border:2px solid transparent;")
            btn.clicked.connect(lambda _, c=QColor(hex_color): self._set_palette_color(c))
            self.palette_buttons.append((btn, QColor(hex_color)))
            palette_layout.addWidget(btn)
        row.addLayout(palette_layout, 0)

        options = QHBoxLayout()
        options.setSpacing(6)
        
        def add_label(text):
            label = QLabel(text)
            label.setStyleSheet("color:#1e2433;font-size:12px;")
            options.addWidget(label)
            return label

        self.color_btn = QPushButton()
        self.color_btn.setFixedSize(64, 22)
        self.color_btn.setCursor(Qt.PointingHandCursor)
        self.color_btn.clicked.connect(self._choose_color)
        options.addWidget(self.color_btn)

        add_label("宽度")
        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 20)
        self.width_spin.setFixedWidth(52)
        self.width_spin.valueChanged.connect(self._on_width_changed)
        options.addWidget(self.width_spin)
        
        options.addStretch()
        row.addLayout(options, 1)
        layout.addLayout(row)

        self.setLayout(layout)
        self.canvas.optionsUpdated.connect(self.sync_from_canvas)
        self.sync_from_canvas()

    def _apply_style(self):
        accent = self._accent.name()
        self.setStyleSheet(f"""
            #ShapePanel {{
                background-color: #ffffff;
                border-top: 1px solid #e2e8f0;
            }}
        """)

    def _set_palette_color(self, color):
        self.canvas.update_selected_shape_color(color)
        self.sync_from_canvas()

    def _choose_color(self):
        current = self.canvas.line_color
        if self.canvas.selected_line_index is not None:
            current = self.canvas.lines[self.canvas.selected_line_index]['color']
        elif self.canvas.selected_arrow_index is not None:
            current = self.canvas.arrows[self.canvas.selected_arrow_index]['color']
        
        color = QColorDialog.getColor(current, self, "选择颜色")
        if color.isValid():
            self.canvas.update_selected_shape_color(color)
            self.sync_from_canvas()

    def _on_width_changed(self, val):
        self.canvas._push_undo_state()
        if self.canvas.selected_line_index is not None:
            self.canvas.lines[self.canvas.selected_line_index]['width'] = val
            self.canvas.line_width = val
        elif self.canvas.selected_arrow_index is not None:
            self.canvas.arrows[self.canvas.selected_arrow_index]['width'] = val
            self.canvas.arrow_width = val
        self.canvas.optionsUpdated.emit()
        self.canvas.update()

    def sync_from_canvas(self):
        color = self.canvas.line_color
        width = self.canvas.line_width
        
        if self.canvas.selected_line_index is not None:
            info = self.canvas.lines[self.canvas.selected_line_index]
            color = info['color']
            width = info['width']
        elif self.canvas.selected_arrow_index is not None:
            info = self.canvas.arrows[self.canvas.selected_arrow_index]
            color = info['color']
            width = info['width']
            
        self.color_btn.setStyleSheet(f"background-color: {color.name()}; border: 1px solid #ccc; border-radius: 4px;")
        self.width_spin.blockSignals(True)
        self.width_spin.setValue(width)
        self.width_spin.blockSignals(False)

class MarkerOptionsPanel(QFrame):
    def __init__(self, canvas: AnnotationCanvas):
        super().__init__()
        self.canvas = canvas
        self.setObjectName("MarkerPanel")
        self._accent = QColor(PANEL_ACCENTS["marker"])
        self._active = False
        self.palette_buttons = []
        self._apply_style()

        layout = QVBoxLayout()
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)

        row = QHBoxLayout()
        row.setSpacing(10)

        palette_layout = QHBoxLayout()
        palette_layout.setSpacing(4)
        palette_label = QLabel("经典颜色")
        palette_label.setStyleSheet("color:#2b3245;font-weight:600;font-size:12px;")
        palette_layout.addWidget(palette_label)
        for hex_color in CLASSIC_COLORS:
            btn = QPushButton()
            btn.setProperty("class", "color-chip")
            btn.setFixedSize(20, 20)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(f"background-color:{hex_color}; border-radius:6px; border:2px solid transparent;")
            btn.setProperty("selected", False)
            btn.clicked.connect(lambda _, c=QColor(hex_color): self._set_palette_color(c))
            self.palette_buttons.append((btn, QColor(hex_color)))
            palette_layout.addWidget(btn)
        row.addLayout(palette_layout, 0)

        options = QHBoxLayout()
        options.setSpacing(6)

        def add_label(text):
            label = QLabel(text)
            label.setStyleSheet("color:#1e2433;font-size:12px;")
            options.addWidget(label)
            return label

        add_label("填充色")
        self.color_btn = QPushButton()
        self.color_btn.setFixedSize(64, 22)
        self.color_btn.setCursor(Qt.PointingHandCursor)
        self.color_btn.clicked.connect(self._choose_color)
        options.addWidget(self.color_btn)

        add_label("描边色")
        self.border_color_btn = QPushButton()
        self.border_color_btn.setFixedSize(64, 22)
        self.border_color_btn.setCursor(Qt.PointingHandCursor)
        self.border_color_btn.clicked.connect(self._choose_border_color)
        options.addWidget(self.border_color_btn)

        self.border_checkbox = QCheckBox("描边")
        self.border_checkbox.setStyleSheet("color:#1c263b;font-size:12px;")
        self.border_checkbox.toggled.connect(canvas.set_marker_border_enabled)
        options.addWidget(self.border_checkbox)

        add_label("大小")
        self.size_spin = QSpinBox()
        self.size_spin.setRange(10, 120)
        self.size_spin.setFixedWidth(52)
        self.size_spin.valueChanged.connect(canvas.set_marker_size)
        options.addWidget(self.size_spin)

        add_label("字号")
        self.font_ratio_spin = QDoubleSpinBox()
        self.font_ratio_spin.setRange(0.3, 1.2)
        self.font_ratio_spin.setSingleStep(0.05)
        self.font_ratio_spin.setFixedWidth(64)
        self.font_ratio_spin.valueChanged.connect(canvas.set_marker_font_ratio)
        options.addWidget(self.font_ratio_spin)

        add_label("当前")
        self.current_number_spin = QSpinBox()
        self.current_number_spin.setRange(1, 999)
        self.current_number_spin.setFixedWidth(52)
        self.current_number_spin.valueChanged.connect(canvas.set_current_marker_number)
        options.addWidget(self.current_number_spin)

        add_label("下一个")
        self.number_spin = QSpinBox()
        self.number_spin.setRange(1, 999)
        self.number_spin.setFixedWidth(52)
        self.number_spin.valueChanged.connect(canvas.set_next_marker_number)
        options.addWidget(self.number_spin)
        options.addStretch()
        row.addLayout(options, 1)
        layout.addLayout(row)

        self.setLayout(layout)
        self.canvas.optionsUpdated.connect(self.sync_from_canvas)
        self.sync_from_canvas()

    def _apply_style(self):
        accent = self._accent.name()
        soft = QColor(self._accent).lighter(185).name()
        strong = QColor(self._accent).lighter(150).name()
        button_bg = QColor(self._accent).darker(110).name()
        self.setStyleSheet(
            f"""
            QFrame#MarkerPanel {{
                background-color: {soft};
                border-radius: 6px;
                border: none;
            }}
            QFrame#MarkerPanel[active="true"] {{
                background-color: {strong};
            }}
            QPushButton[class="option-chip"] {{
                padding: 2px 12px;
                border-radius: 6px;
                border: none;
                background: {button_bg};
                font-weight: 600;
                color: #ffffff;
            }}
            QPushButton[class="color-chip"] {{
                border-radius: 6px;
            }}
            QPushButton[class="color-chip"][selected="true"] {{
                border-color: {accent};
            }}
            """
        )

    def set_panel_active(self, active: bool):
        if self._active == active:
            return
        self._active = active
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)
        self.current_number_spin.setEnabled(active)

    def _update_color_button(self):
        color = self.canvas.marker_fill_color
        self.color_btn.setStyleSheet(
            f"background-color: {color.name(QColor.HexArgb)}; border: 1px solid #cfd6e6; border-radius:6px;"
        )

    def _choose_color(self):
        color = QColorDialog.getColor(self.canvas.marker_fill_color, self, "选择顺序标记填充色")
        if color.isValid():
            self.canvas.set_marker_color(color)
            self._update_color_button()
            self._refresh_palette_highlight()

    def _choose_border_color(self):
        color = QColorDialog.getColor(self.canvas.marker_border_color, self, "选择描边颜色")
        if color.isValid():
            self.canvas.set_marker_border_color(color)
            self._update_border_button()

    def _set_palette_color(self, color: QColor):
        self.canvas.set_marker_color(color)
        self._update_color_button()
        self._refresh_palette_highlight()

    def _update_border_button(self):
        color = self.canvas.marker_border_color
        self.border_color_btn.setStyleSheet(
            f"background-color: {color.name(QColor.HexArgb)}; border: 1px solid #cfd6e6; border-radius:6px;"
        )

    def _refresh_palette_highlight(self):
        for btn, palette_color in self.palette_buttons:
            btn.setProperty("selected", palette_color == self.canvas.marker_fill_color)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def sync_from_canvas(self):
        self._update_color_button()
        self._update_border_button()
        self._refresh_palette_highlight()
        self.size_spin.blockSignals(True)
        self.size_spin.setValue(self.canvas.marker_size)
        self.size_spin.blockSignals(False)
        self.font_ratio_spin.blockSignals(True)
        self.font_ratio_spin.setValue(self.canvas.marker_font_ratio)
        self.font_ratio_spin.blockSignals(False)
        self.number_spin.blockSignals(True)
        self.number_spin.setValue(self.canvas.next_marker_number)
        self.number_spin.blockSignals(False)
        self.border_checkbox.blockSignals(True)
        self.border_checkbox.setChecked(self.canvas.marker_border_enabled)
        self.border_checkbox.blockSignals(False)
        active = (
            self.canvas.selected_marker_index is not None
            and not self.canvas.markers_flattened
            and 0 <= self.canvas.selected_marker_index < len(self.canvas.markers)
        )
        self.current_number_spin.setEnabled(active)
        self.current_number_spin.blockSignals(True)
        if active:
            self.current_number_spin.setValue(self.canvas.markers[self.canvas.selected_marker_index]["number"])
        else:
            self.current_number_spin.setValue(self.canvas.next_marker_number)
        self.current_number_spin.blockSignals(False)


class RectangleOptionsPanel(QFrame):
    def __init__(self, canvas: AnnotationCanvas):
        super().__init__()
        self.canvas = canvas
        self.setObjectName("RectanglePanel")
        self._accent = QColor(PANEL_ACCENTS["rectangle"])
        self._active = False
        self.palette_buttons = []
        self._apply_style()

        layout = QVBoxLayout()
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)

        row = QHBoxLayout()
        row.setSpacing(10)

        palette_layout = QHBoxLayout()
        palette_layout.setSpacing(4)
        palette_label = QLabel("经典颜色")
        palette_label.setStyleSheet("color:#2b3245;font-weight:600;font-size:12px;")
        palette_layout.addWidget(palette_label)
        for hex_color in CLASSIC_COLORS:
            btn = QPushButton()
            btn.setProperty("class", "color-chip")
            btn.setFixedSize(20, 20)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(f"background-color:{hex_color}; border-radius:6px; border:2px solid transparent;")
            btn.clicked.connect(lambda _, c=QColor(hex_color): self._apply_palette_color(c))
            self.palette_buttons.append((btn, QColor(hex_color)))
            palette_layout.addWidget(btn)
        row.addLayout(palette_layout, 0)

        options = QHBoxLayout()
        options.setSpacing(6)

        def add_label(text):
            label = QLabel(text)
            label.setStyleSheet("color:#1e2433;font-size:12px;")
            options.addWidget(label)
            return label

        add_label("描边色")
        self.color_btn = QPushButton()
        self.color_btn.setFixedSize(74, 22)
        self.color_btn.setCursor(Qt.PointingHandCursor)
        self.color_btn.clicked.connect(self._choose_color)
        options.addWidget(self.color_btn)

        add_label("线宽")
        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 20)
        self.width_spin.setFixedWidth(52)
        self.width_spin.valueChanged.connect(canvas.set_rectangle_border_width)
        options.addWidget(self.width_spin)

        add_label("圆角")
        self.radius_spin = QSpinBox()
        self.radius_spin.setRange(0, 60)
        self.radius_spin.setFixedWidth(52)
        self.radius_spin.valueChanged.connect(canvas.set_rectangle_corner_radius)
        options.addWidget(self.radius_spin)

        self.square_btn = QPushButton("直角")
        self.square_btn.setProperty("class", "option-chip")
        self.square_btn.setCursor(Qt.PointingHandCursor)
        self.square_btn.clicked.connect(lambda: self._set_radius_preset(0))
        options.addWidget(self.square_btn)

        self.round_btn = QPushButton("圆角 8")
        self.round_btn.setProperty("class", "option-chip")
        self.round_btn.setCursor(Qt.PointingHandCursor)
        self.round_btn.clicked.connect(lambda: self._set_radius_preset(8))
        options.addWidget(self.round_btn)

        options.addStretch()
        row.addLayout(options, 1)
        layout.addLayout(row)

        self.setLayout(layout)
        self.canvas.optionsUpdated.connect(self.sync_from_canvas)
        self.sync_from_canvas()

    def _apply_style(self):
        accent = self._accent.name()
        soft = QColor(self._accent).lighter(185).name()
        strong = QColor(self._accent).lighter(150).name()
        button_bg = QColor(self._accent).darker(105).name()
        self.setStyleSheet(
            f"""
            QFrame#RectanglePanel {{
                background-color: {soft};
                border-radius: 6px;
                border: none;
            }}
            QFrame#RectanglePanel[active="true"] {{
                background-color: {strong};
            }}
            QPushButton[class="option-chip"] {{
                padding: 2px 12px;
                border-radius: 6px;
                border: none;
                background: {button_bg};
                font-weight: 600;
                color: #ffffff;
            }}
            QPushButton[class="color-chip"] {{
                border-radius: 6px;
            }}
            QPushButton[class="color-chip"][selected="true"] {{
                border-color: {accent};
            }}
            """
        )

    def set_panel_active(self, active: bool):
        if getattr(self, "_active", False) == active:
            return
        self._active = active
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)

    def _apply_palette_color(self, color: QColor):
        self.canvas.set_rectangle_border_color(color)
        self.sync_from_canvas()

    def _choose_color(self):
        color = QColorDialog.getColor(self.canvas.rectangle_border_color, self, "选择标注框描边色")
        if color.isValid():
            self.canvas.set_rectangle_border_color(color)
            self.sync_from_canvas()

    def _refresh_palette_highlight(self):
        border_color = self.canvas.rectangle_border_color
        for btn, palette_color in self.palette_buttons:
            btn.setProperty("selected", palette_color == border_color)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def sync_from_canvas(self):
        color = self.canvas.rectangle_border_color
        self.color_btn.setStyleSheet(
            f"background-color: {color.name(QColor.HexArgb)}; border: 1px solid #cfd6e6; padding: 4px; border-radius:6px;"
        )
        self.width_spin.blockSignals(True)
        self.width_spin.setValue(self.canvas.rectangle_border_width)
        self.width_spin.blockSignals(False)
        self.radius_spin.blockSignals(True)
        self.radius_spin.setValue(self.canvas.rectangle_corner_radius)
        self.radius_spin.blockSignals(False)
        self._refresh_palette_highlight()

    def _set_radius_preset(self, value: int):
        self.canvas.set_rectangle_corner_radius(value)
        self.sync_from_canvas()


class ShapeOptionsPanel(QFrame):
    def __init__(self, canvas):
        super().__init__()
        self.canvas = canvas
        self.setObjectName("ShapePanel")
        self._accent = QColor("#5f27cd")
        self._active = False
        
        self.setStyleSheet("""
            QFrame#ShapePanel {
                background: rgba(95,39,205,0.08);
                border-radius: 10px;
            }
            QFrame#ShapePanel QLabel {
                color: #421aab;
                font-weight: 700;
                font-size: 11px;
            }
            QFrame#ShapePanel QSpinBox, QFrame#ShapePanel QLineEdit {
                border: 1px solid #cfd6e6;
                border-radius: 4px;
                padding: 2px 5px;
                background: #ffffff;
                color: #0f172a;
            }
            QPushButton[class="color-chip"] {
                border-radius: 4px;
                border: 1px solid #e2e8f0;
            }
            QPushButton[class="color-chip"][selected="true"] {
                border: 2px solid #5f27cd;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(10)

        # 调色盘
        palette_layout = QHBoxLayout()
        palette_layout.setSpacing(4)
        self.palette_buttons = []
        colors = ["#ff4757", "#2ed3a3", "#5f27cd", "#f7b500", "#ffffff", "#000000"]
        for hex_color in colors:
            btn = QPushButton()
            btn.setFixedSize(18, 18)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setProperty("class", "color-chip")
            btn.setStyleSheet(f"background-color: {hex_color};")
            btn.clicked.connect(lambda _, c=QColor(hex_color): self._apply_palette_color(c))
            self.palette_buttons.append((btn, QColor(hex_color)))
            palette_layout.addWidget(btn)
        layout.addLayout(palette_layout)

        # 颜色预览/选择
        self.color_btn = QPushButton()
        self.color_btn.setFixedSize(24, 24)
        self.color_btn.setCursor(Qt.PointingHandCursor)
        self.color_btn.clicked.connect(self._choose_color)
        layout.addWidget(self.color_btn)

        # 线宽
        width_container = QWidget()
        width_layout = QHBoxLayout(width_container)
        width_layout.setContentsMargins(0,0,0,0)
        width_layout.setSpacing(4)
        self.width_label = QLabel("线宽")
        width_layout.addWidget(self.width_label)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 20)
        self.width_spin.setFixedWidth(45)
        self.width_spin.valueChanged.connect(canvas.update_selected_shape_width)
        width_layout.addWidget(self.width_spin)
        layout.addWidget(width_container)
        self.width_container = width_container

        layout.addStretch()

        self.canvas.optionsUpdated.connect(self.sync_from_canvas)
        self.sync_from_canvas()

    def set_panel_active(self, active: bool):
        self._active = active
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)

    def _apply_palette_color(self, color: QColor):
        self.canvas.update_selected_shape_color(color)
        self.sync_from_canvas()

    def _choose_color(self):
        color = QColorDialog.getColor(self.canvas.line_color, self, "选择颜色")
        if color.isValid():
            self.canvas.update_selected_shape_color(color)
            self.sync_from_canvas()

    def sync_from_canvas(self):
        kind = self.canvas.active_selection_kind()
        
        # 决定哪些控件显示
        is_line_or_arrow = (kind in ("line", "arrow"))
        
        self.width_container.setVisible(is_line_or_arrow)

        # 获取当前选中项的颜色
        current_color = self.canvas.line_color
        if kind == "line":
            if self.canvas.selected_line_index is not None and self.canvas.selected_line_index < len(self.canvas.lines):
                item = self.canvas.lines[self.canvas.selected_line_index]
                current_color = item['color']
                self.width_spin.blockSignals(True)
                self.width_spin.setValue(item['width'])
                self.width_spin.blockSignals(False)
        elif kind == "arrow":
            if self.canvas.selected_arrow_index is not None and self.canvas.selected_arrow_index < len(self.canvas.arrows):
                item = self.canvas.arrows[self.canvas.selected_arrow_index]
                current_color = item['color']
                self.width_spin.blockSignals(True)
                self.width_spin.setValue(item['width'])
                self.width_spin.blockSignals(False)
        
        self.color_btn.setStyleSheet(
            f"background-color: {current_color.name()}; border: 2px solid #ffffff; border-radius: 4px;"
        )
        
        # 同步调色盘高亮
        for btn, c in self.palette_buttons:
            btn.setProperty("selected", c == current_color)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

class TextOptionsPanel(QFrame):
    def __init__(self, tab):
        super().__init__()
        self.tab = tab
        self.canvas = tab.canvas
        self.setObjectName("TextPanel")
        self._accent = QColor(PANEL_ACCENTS["text"])
        self._active = False
        self._apply_style()

        layout = QHBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(10, 8, 10, 8)

        def add_label(text):
            label = QLabel(text)
            label.setStyleSheet("color:#1e2433;font-size:12px;")
            layout.addWidget(label)
            return label

        add_label("字体")
        self.font_combo = QFontComboBox()
        self.font_combo.setFontFilters(QFontComboBox.ScalableFonts)
        self.font_combo.currentFontChanged.connect(lambda f: tab.on_text_font_family_changed(f.family()))
        layout.addWidget(self.font_combo)

        add_label("字体大小")
        self.size_spin = QSpinBox()
        self.size_spin.setRange(8, 72)
        self.size_spin.setFixedWidth(56)
        self.size_spin.valueChanged.connect(tab.on_text_font_size_changed)
        layout.addWidget(self.size_spin)

        add_label("字体颜色")
        self.color_btn = QPushButton()
        self.color_btn.setFixedSize(24, 24)
        self.color_btn.setCursor(Qt.PointingHandCursor)
        self.color_btn.clicked.connect(self._choose_color)
        layout.addWidget(self.color_btn)

        add_label("背景颜色")
        self.bg_color_btn = QPushButton()
        self.bg_color_btn.setFixedSize(24, 24)
        self.bg_color_btn.setCursor(Qt.PointingHandCursor)
        self.bg_color_btn.clicked.connect(self._choose_background_color)
        layout.addWidget(self.bg_color_btn)
        self.transparent_btn = QPushButton("透明")
        self.transparent_btn.setCursor(Qt.PointingHandCursor)
        self.transparent_btn.setProperty("class", "option-chip")
        self.transparent_btn.clicked.connect(self._set_transparent_background)
        layout.addWidget(self.transparent_btn)
        layout.addStretch()
        self.setLayout(layout)

        self.canvas.optionsUpdated.connect(self.sync_from_canvas)
        self.sync_from_canvas()

    def _apply_style(self):
        accent = self._accent.name()
        light = QColor(self._accent).lighter(200).name()
        self.setStyleSheet(
            f"""
            QFrame#TextPanel {{
                background-color: {light};
                border-radius: 6px;
                border: none;
            }}
            QFrame#TextPanel[active="true"] {{
                background-color: {accent};
            }}
            """
        )

    def set_panel_active(self, active: bool):
        if getattr(self, "_active", False) == active:
            return
        self._active = active
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)

    def sync_from_canvas(self):
        self.size_spin.blockSignals(True)
        self.size_spin.setValue(self.canvas.text_font_size)
        self.size_spin.blockSignals(False)
        self.font_combo.blockSignals(True)
        self.font_combo.setCurrentFont(QFont(self.canvas.text_font_family))
        self.font_combo.blockSignals(False)
        self._update_color_button()
        self._update_bg_color_button()

    def _update_color_button(self):
        color = self.canvas.text_color
        self.color_btn.setStyleSheet(
            f"background-color: {color.name(QColor.HexArgb)}; border-radius: 4px; border: 1px solid #cfd6e6;"
        )

    def _update_bg_color_button(self):
        bg = self.canvas.text_background_color
        self.bg_color_btn.setStyleSheet(
            f"background-color: {bg.name(QColor.HexArgb)}; border-radius: 4px; border: 1px solid #cfd6e6;"
        )

    def _choose_color(self):
        color = QColorDialog.getColor(self.canvas.text_color, self, "选择文字颜色")
        if color.isValid():
            self.tab.on_text_color_changed(color)

    def _choose_background_color(self):
        color = QColorDialog.getColor(self.canvas.text_background_color, self, "选择背景颜色")
        if color.isValid():
            self.tab.on_text_background_changed(color)

    def _set_transparent_background(self):
        transparent = QColor(0, 0, 0, 0)
        self.tab.on_text_background_changed(transparent)


class AnnotationWorkspacePage(QWidget):
    def __init__(
        self,
        open_settings_callback,
        open_images_callback,
        style_state,
        style_callback,
        image_quality,
        auto_save_enabled,
        default_zoom=1.0,
        zoom_changed_callback=None,
    ):
        super().__init__()
        self._open_settings_callback = open_settings_callback
        self._open_images_callback = open_images_callback
        self._style_state = style_state
        self._style_callback = style_callback
        self._image_quality = self._clamp_quality(image_quality)
        self._auto_save_enabled = bool(auto_save_enabled)
        self._display_zoom = self._clamp_zoom(default_zoom)
        self._zoom_callback = zoom_changed_callback
        self._updating_zoom = False
        self._last_active_tab_index = -1
        self._tab_scroll_anchor = 0
        self._tab_scroll_offset = 0
        self._tab_scroll_lock = False
        self._tab_scroll_last_cause = "init"
        self._shared_tool = None
        layout = QVBoxLayout()

        action_bar = QHBoxLayout()
        action_bar.setContentsMargins(10, 5, 10, 5)
        action_bar.setSpacing(10)
        
        # 边框设置 (全局)
        def add_bar_label(text):
            lbl = QLabel(text)
            lbl.setStyleSheet("color:#1e2433; font-size:12px; font-weight:bold;")
            action_bar.addWidget(lbl)
            return lbl

        add_bar_label("截图边框:")
        
        self.border_width_spin = QSpinBox()
        self.border_width_spin.setRange(0, 20)
        self.border_width_spin.setSuffix(" px")
        self.border_width_spin.setFixedWidth(70)
        # 初始化值
        initial_border = self._style_state.get("image_border", DEFAULT_IMAGE_BORDER_STYLE)
        self.border_width_spin.setValue(initial_border.get("width", 0))
        self.border_width_spin.valueChanged.connect(self._on_global_border_width_changed)
        action_bar.addWidget(self.border_width_spin)
        
        self.border_color_btn = QPushButton()
        self.border_color_btn.setFixedSize(24, 24)
        self.border_color_btn.setCursor(Qt.PointingHandCursor)
        self.border_color_btn.setToolTip("选择边框颜色")
        initial_color = initial_border.get("color", "#FF7043")
        self.border_color_btn.setStyleSheet(f"background-color: {initial_color}; border: 1px solid #ccc; border-radius: 4px;")
        self.border_color_btn.clicked.connect(self._on_global_border_color_clicked)
        action_bar.addWidget(self.border_color_btn)

        action_bar.addStretch(1)
        self.copy_all_btn = QPushButton("多图复制到剪贴板")
        self.copy_all_btn.setToolTip("将当前打开的所有截图渲染为临时文件并写入剪贴板（微信/QQ 可一次粘贴多图）。")
        self.copy_all_btn.setEnabled(False)
        self.copy_all_btn.clicked.connect(self._copy_all_tabs_to_clipboard)

        self.clear_all_btn = QPushButton("清空所有标签")
        self.clear_all_btn.setToolTip("清空当前所有已打开的截图标签页。")
        self.clear_all_btn.setEnabled(False)
        self.clear_all_btn.clicked.connect(self.clear_all_tabs)

        action_bar.addWidget(self.copy_all_btn, 0, Qt.AlignRight)
        action_bar.addWidget(self.clear_all_btn, 0, Qt.AlignRight)
        layout.addLayout(action_bar)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.currentChanged.connect(self._on_tab_switched)
        self.tabs.tabBar().installEventFilter(self)
        self._install_tab_scroll_button_filters()
        layout.addWidget(self.tabs, 1)

        hint = QLabel("尚未添加截图，使用区域截取或重复截取后会在此显示。")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("color: #777777;")
        self._empty_hint = hint
        layout.addWidget(hint)
        self.setLayout(layout)

        self._update_hint_visibility()
        self._update_tab_scroll_anchor()
        # region agent log
        _agent_debug_log(
            hypothesisId="H6",
            location="screenshot_tool.py:AnnotationWorkspacePage.__init__",
            message="workspace init tab scroll state",
            data={
                "count": self.tabs.count(),
                "offset": self._current_scroll_offset(),
                "anchor": self._tab_scroll_anchor,
                "lock": bool(self._tab_scroll_lock),
            },
        )
        # endregion

    def _on_global_border_width_changed(self, value):
        data = {"width": value}
        self._style_state.setdefault("image_border", {}).update(data)
        self._style_callback("image_border", data)
        # 同步到所有已打开的 tab（边框只影响导出，不影响编辑视图）
        for tab in self._iter_tabs():
            if hasattr(tab, "canvas"):
                tab.canvas.image_border_width = value

    def _on_global_border_color_clicked(self):
        current_color = QColor(self._style_state.get("image_border", {}).get("color", "#FF7043"))
        color = QColorDialog.getColor(current_color, self, "选择截图边框颜色")
        if color.isValid():
            hex_color = color.name()
            self.border_color_btn.setStyleSheet(f"background-color: {hex_color}; border: 1px solid #ccc; border-radius: 4px;")
            data = {"color": hex_color}
            self._style_state.setdefault("image_border", {}).update(data)
            self._style_callback("image_border", data)
            # 同步到所有已打开的 tab（边框只影响导出，不影响编辑视图）
            for tab in self._iter_tabs():
                if hasattr(tab, "canvas"):
                    tab.canvas.image_border_color = color

    def _iter_tabs(self):
        for i in range(self.tabs.count()):
            yield self.tabs.widget(i)

    def _update_hint_visibility(self):
        has_tabs = self.tabs.count() > 0
        self._empty_hint.setVisible(not has_tabs)
        self.tabs.setVisible(has_tabs)
        if hasattr(self, "copy_all_btn"):
            self.copy_all_btn.setEnabled(has_tabs)
        if hasattr(self, "clear_all_btn"):
            self.clear_all_btn.setEnabled(has_tabs)

    def showEvent(self, event):
        super().showEvent(event)
        # 页面显示时不自动触发 restore_last_active_tab 的滚动逻辑
        # QTimer.singleShot(0, self.restore_last_active_tab)

    def _remember_active_tab(self, index):
        try:
            index = int(index)
        except (TypeError, ValueError):
            return
        if index >= 0:
            self._last_active_tab_index = index

    def restore_last_active_tab(self):
        if self._last_active_tab_index < 0:
            return
        max_idx = self.tabs.count() - 1
        if max_idx < 0:
            return
        target = min(self._last_active_tab_index, max_idx)
        if target != self.tabs.currentIndex():
            with self._preserve_tab_scroll():
                self.tabs.setCurrentIndex(target)
        # 移除此处的自动恢复锚点调用，避免切换页面时标签栏跳动
        # self._restore_tab_scroll_anchor()
        self._apply_shared_tool_to_tab(self.tabs.widget(target))

    def _on_tab_switched(self, index):
        self._remember_active_tab(index)
        self._apply_shared_tool_to_tab()
        # 移除切换标签时的自动锚点更新逻辑，让位置保持在用户手动滚动的地方
        # if not self._tab_scroll_lock:
        #    self._tab_scroll_last_cause = "tab_switched"
        #    QTimer.singleShot(0, self._update_tab_scroll_anchor)
        # region agent log
        _agent_debug_log(
            hypothesisId="H6",
            location="screenshot_tool.py:AnnotationWorkspacePage._on_tab_switched",
            message="tab switched",
            data={
                "index": index,
                "count": self.tabs.count(),
                "offset_now": self._current_scroll_offset(),
                "visible_range": self._tab_visible_range(),
                "lock": bool(self._tab_scroll_lock),
            },
        )
        # endregion

    def _install_tab_scroll_button_filters(self):
        left, right = self._tab_scroll_buttons()
        for btn in (left, right):
            if btn:
                btn.installEventFilter(self)

    def _tab_scroll_buttons(self):
        bar = self.tabs.tabBar()
        left = right = None
        for btn in bar.findChildren(QToolButton):
            arrow = btn.arrowType() if hasattr(btn, "arrowType") else None
            if arrow == Qt.LeftArrow:
                left = btn
            elif arrow == Qt.RightArrow:
                right = btn
        return left, right

    def _tab_visible_range(self):
        bar = self.tabs.tabBar()
        count = bar.count()
        if count == 0:
            return (0, -1)
        width = bar.width()
        first = 0
        last = count - 1
        for i in range(count):
            rect = bar.tabRect(i)
            if rect.right() >= 0:
                first = i
                break
        for i in range(count - 1, -1, -1):
            rect = bar.tabRect(i)
            if rect.left() <= width:
                last = i
                break
        return (first, last)

    @contextmanager
    def _preserve_tab_scroll(self):
        if not self._tab_scroll_lock:
            self._update_tab_scroll_anchor()
        anchor = self._tab_scroll_anchor
        offset = self._tab_scroll_offset
        self._tab_scroll_lock = True
        try:
            yield
        finally:
            self._tab_scroll_anchor = anchor
            self._tab_scroll_offset = offset
            self._tab_scroll_lock = False
            QTimer.singleShot(0, self._restore_tab_scroll_anchor)

    def _current_scroll_offset(self):
        bar = self.tabs.tabBar()
        if bar.count() == 0:
            return 0
        try:
            return max(0, -bar.tabRect(0).left())
        except Exception:
            return 0

    def _apply_scroll_offset(self, target_offset, max_steps=400):
        left, right = self._tab_scroll_buttons()
        if not (left or right):
            return
        guard = 0
        def _can_move(direction):
            return (left.isEnabled() if direction < 0 else right.isEnabled()) if (left and right) else True
        while guard < max_steps:
            guard += 1
            current = self._current_scroll_offset()
            delta = target_offset - current
            if abs(delta) <= 2:
                break
            if delta > 0 and right and _can_move(1):
                right.click()
            elif delta < 0 and left and _can_move(-1):
                left.click()
            else:
                break
            QApplication.processEvents()

    def _apply_shared_tool_to_tab(self, tab=None):
        tool = self._shared_tool
        if tool is None:
            return
        target = tab or self.tabs.currentWidget()
        if target and hasattr(target, "_current_tool") and hasattr(target, "_set_tool"):
            if target._current_tool != tool:
                try:
                    target._set_tool(tool)
                except Exception:
                    pass

    def _on_tab_tool_changed(self, tool):
        if tool is None:
            return
        self._shared_tool = tool

    def _update_tab_scroll_anchor(self):
        if self._tab_scroll_lock:
            return
        first, _ = self._tab_visible_range()
        self._tab_scroll_anchor = first
        self._tab_scroll_offset = self._current_scroll_offset()
        # region agent log
        _agent_debug_log(
            hypothesisId="H6",
            location="screenshot_tool.py:AnnotationWorkspacePage._update_tab_scroll_anchor",
            message="update tab scroll anchor",
            data={
                "cause": getattr(self, "_tab_scroll_last_cause", None),
                "count": self.tabs.count(),
                "stored_anchor": self._tab_scroll_anchor,
                "stored_offset": self._tab_scroll_offset,
                "visible_range": self._tab_visible_range(),
                "currentIndex": self.tabs.currentIndex(),
            },
        )
        # endregion

    def _restore_tab_scroll_anchor(self):
        count = self.tabs.count()
        if count == 0:
            return
        anchor = min(max(0, self._tab_scroll_anchor), count - 1)
        # region agent log
        _agent_debug_log(
            hypothesisId="H6",
            location="screenshot_tool.py:AnnotationWorkspacePage._restore_tab_scroll_anchor",
            message="restore tab scroll anchor begin",
            data={
                "count": count,
                "anchor": anchor,
                "stored_offset": self._tab_scroll_offset,
                "offset_now": self._current_scroll_offset(),
                "visible_range": self._tab_visible_range(),
                "currentIndex": self.tabs.currentIndex(),
            },
        )
        # endregion
        self._apply_scroll_offset(self._tab_scroll_offset)
        left, right = self._tab_scroll_buttons()
        first, last = self._tab_visible_range()
        guard = 0
        while anchor < first and left and left.isEnabled() and guard < 200:
            left.click()
            QApplication.processEvents()
            first, last = self._tab_visible_range()
            guard += 1
        while anchor > last and right and right.isEnabled() and guard < 400:
            right.click()
            QApplication.processEvents()
            first, last = self._tab_visible_range()
            guard += 1
        # region agent log
        _agent_debug_log(
            hypothesisId="H6",
            location="screenshot_tool.py:AnnotationWorkspacePage._restore_tab_scroll_anchor",
            message="restore tab scroll anchor end",
            data={
                "offset_now": self._current_scroll_offset(),
                "visible_range": self._tab_visible_range(),
                "guard": guard,
            },
        )
        # endregion

    def eventFilter(self, obj, event):
        if obj in (self.tabs.tabBar(),) or obj in self._tab_scroll_buttons():
            if event.type() in (QEvent.Wheel, QEvent.MouseButtonRelease, QEvent.MouseButtonDblClick):
                if not self._tab_scroll_lock:
                    if obj in self._tab_scroll_buttons():
                        self._tab_scroll_last_cause = "scroll_button_or_release"
                    else:
                        self._tab_scroll_last_cause = "tabbar_input"
                    QTimer.singleShot(0, self._update_tab_scroll_anchor)
        return super().eventFilter(obj, event)

    def _copy_all_tabs_to_clipboard(self):
        tabs = list(self._iter_tabs())
        if not tabs:
            QMessageBox.information(self, "暂无截图", "请先创建或打开截图后再复制。")
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        target_dir = os.path.join(tempfile.gettempdir(), "ctk_snapshot_clipboard")
        payloads = []
        failures = []
        for idx, tab in enumerate(tabs, 1):
            try:
                path, png_bytes, _ = tab._export_clipboard_image(
                    flatten=False,
                    target_dir=target_dir,
                    stamp=stamp,
                    index=idx,
                )
                payloads.append((path, png_bytes))
            except Exception as exc:
                failures.append(str(exc))
        if not payloads:
            QMessageBox.warning(self, "复制失败", "未能生成可复制的图片文件，请检查写入权限。")
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(path) for path, _ in payloads])
        if payloads[0][1]:
            mime.setData("image/png", payloads[0][1])
        if not _set_clipboard_mime_with_retry(mime):
            QMessageBox.warning(self, "复制失败", "无法写入剪贴板，请稍后重试。")
            return
        copied_msg = f"已将 {len(payloads)} 张图片复制为文件到剪贴板"
        current_tab = self.tabs.currentWidget()
        if current_tab and hasattr(current_tab, "status_label"):
            current_tab.status_label.setText(f"{copied_msg}（可直接在微信粘贴多图）")
        if failures:
            detail = "\n".join(failures[:3])
            QMessageBox.warning(self, "部分复制失败", f"{copied_msg}，但有 {len(failures)} 张未生成：\n{detail}")

    def add_capture(self, pixmap: QPixmap, save_dir: str, initial_zoom=1.0):
        self._create_tab(pixmap, save_dir, initial_zoom=initial_zoom)

    def open_image_files(self, file_paths):
        invalid = []
        created = False
        for path in file_paths:
            if not path or not os.path.exists(path):
                invalid.append(path)
                continue
            pixmap = QPixmap(path)
            if pixmap.isNull():
                invalid.append(path)
                continue
            save_dir = os.path.dirname(path) or DEFAULT_SAVE_DIR
            self._create_tab(pixmap, save_dir, source_path=path, initial_zoom=self._display_zoom)
            created = True
        if invalid:
            msg = "\n".join(path for path in invalid if path)
            QMessageBox.warning(self, "无法打开图片", f"以下文件无法加载为图片：\n{msg}")
        if created:
            self._update_hint_visibility()

    def _close_tab(self, index):
        widget = self.tabs.widget(index)
        if widget:
            if hasattr(widget, "maybe_close") and not widget.maybe_close():
                return
            widget.deleteLater()
        with self._preserve_tab_scroll():
            self.tabs.removeTab(index)
            self._remember_active_tab(self.tabs.currentIndex())
        self._install_tab_scroll_button_filters()
        QTimer.singleShot(0, self._restore_tab_scroll_anchor)
        self._update_hint_visibility()

    def maybe_close_all(self):
        dirty_tabs = self.get_dirty_tabs()
        if not dirty_tabs:
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("保存截图")
        box.setText("当前有未保存的截图，选择操作：")
        save_btn = box.addButton("保存全部后退出", QMessageBox.AcceptRole)
        discard_btn = box.addButton("不保存直接退出", QMessageBox.DestructiveRole)
        cancel_btn = box.addButton("取消", QMessageBox.RejectRole)
        box.exec_()
        clicked = box.clickedButton()
        if clicked == cancel_btn:
            return False
        if clicked == save_btn:
            return self.save_all_dirty()
        return True

    def clear_all_tabs(self):
        """清空所有标签页，并根据需要确认保存"""
        dirty_tabs = self.get_dirty_tabs()
        if dirty_tabs:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("清空所有标签")
            box.setText("当前有未保存的截图，选择操作：")
            save_btn = box.addButton("保存并清空", QMessageBox.AcceptRole)
            discard_btn = box.addButton("不保存直接清空", QMessageBox.DestructiveRole)
            cancel_btn = box.addButton("取消", QMessageBox.RejectRole)
            box.exec_()
            clicked = box.clickedButton()
            if clicked == cancel_btn:
                return
            if clicked == save_btn:
                if not self.save_all_dirty():
                    return
        else:
            if self.tabs.count() > 0:
                reply = QMessageBox.question(
                    self,
                    "确认清空",
                    "是否确定清空所有已打开的标签页？",
                    QMessageBox.Yes | QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    return

        # 批量移除所有标签
        with self._preserve_tab_scroll():
            while self.tabs.count() > 0:
                widget = self.tabs.widget(0)
                if widget:
                    widget.deleteLater()
                self.tabs.removeTab(0)
            self._remember_active_tab(-1)

        self._update_hint_visibility()
        self._install_tab_scroll_button_filters()
        QTimer.singleShot(0, self._restore_tab_scroll_anchor)

    def _create_tab(self, pixmap, save_dir, source_path=None, initial_zoom=1.0):
        tab = AnnotationTab(
            pixmap,
            save_dir,
            self._style_state,
            self._style_callback,
            self._image_quality,
            self._auto_save_enabled,
            source_path=source_path,
            initial_zoom=initial_zoom,
        )
        label_path = source_path or tab.auto_saved_path
        full_label = os.path.basename(label_path)
        short_len = _dynamic_tab_label_length(self.tabs)
        short_label = _shorten_label(full_label, short_len)
        tab._base_label = short_label
        tab._full_label = full_label
        with self._preserve_tab_scroll():
            idx = self.tabs.addTab(tab, short_label)
            if hasattr(self.tabs, "tabBar"):
                try:
                    self.tabs.tabBar().setTabToolTip(idx, full_label)
                except Exception:
                    pass
            self._bind_tab_signals(tab)
            self.tabs.setCurrentWidget(tab)
            self._remember_active_tab(self.tabs.currentIndex())
        self._install_tab_scroll_button_filters()
        # 新标签页默认选择标注框工具
        tab._set_tool(Tool.RECTANGLE)
        QTimer.singleShot(0, self._restore_tab_scroll_anchor)
        self._update_hint_visibility()

    def _bind_tab_signals(self, tab):
        tab.dirtyStateChanged.connect(lambda dirty, t=tab: self._update_tab_color(t, dirty))
        self._update_tab_color(tab, tab.dirty)
        if hasattr(tab, "canvas"):
            tab.canvas.zoomChanged.connect(lambda factor, t=tab: self._handle_tab_zoom(factor, t))
        if hasattr(tab, "toolChanged"):
            tab.toolChanged.connect(self._on_tab_tool_changed)

    def _update_tab_color(self, tab, dirty):
        index = self.tabs.indexOf(tab)
        if index == -1:
            return
        
        # 核心改进：不再添加 "*" 前缀，仅通过颜色区分状态。
        # 这样标签的宽度永远保持一致，彻底解决 Qt 重新布局导致的“抖动”问题。
        color = QColor("#f97316") if dirty else QColor("#0f172a")
        self.tabs.tabBar().setTabTextColor(index, color)
        
        # 如果当前标签还残留有旧逻辑添加的 "*"，则清理掉它。
        current_text = self.tabs.tabText(index)
        if current_text.startswith("* "):
            base_label = getattr(tab, "_base_label", current_text.lstrip("* ").strip())
            self.tabs.setTabText(index, base_label)
        
        # 更新 ToolTip (保持不变)
        try:
            full_label = getattr(tab, "_full_label", current_text.lstrip("* ").strip())
            self.tabs.tabBar().setTabToolTip(index, full_label)
        except Exception:
            pass

    def set_image_quality(self, value):
        self._image_quality = self._clamp_quality(value)
        for idx in range(self.tabs.count()):
            widget = self.tabs.widget(idx)
            if hasattr(widget, "set_image_quality"):
                widget.set_image_quality(self._image_quality)

    def set_auto_save_enabled(self, enabled):
        self._auto_save_enabled = bool(enabled)
        for tab in self._iter_tabs():
            if hasattr(tab, "set_auto_save_enabled"):
                tab.set_auto_save_enabled(self._auto_save_enabled)

    def get_dirty_tabs(self):
        return [
            tab for tab in self._iter_tabs() if getattr(tab, "dirty", False)
        ]

    def has_unsaved_tabs(self):
        return bool(self.get_dirty_tabs())

    def save_all_dirty(self):
        for tab in self.get_dirty_tabs():
            if not tab.save_annotated_image():
                return False
        return True

    def _iter_tabs(self):
        for idx in range(self.tabs.count()):
            widget = self.tabs.widget(idx)
            if widget:
                yield widget

    def _clamp_quality(self, value):
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = DEFAULT_IMAGE_QUALITY
        return max(10, min(100, value))

    def _clamp_zoom(self, value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return 1.0
        return max(0.25, min(2.0, value))

    def _handle_tab_zoom(self, factor, source_tab=None):
        if self._updating_zoom:
            return
        factor = self._clamp_zoom(factor)
        if abs(factor - self._display_zoom) < 0.001:
            return
        self._updating_zoom = True
        self._display_zoom = factor
        for tab in self._iter_tabs():
            if tab is source_tab:
                continue
            if hasattr(tab, "canvas"):
                tab.canvas.set_zoom(factor)
        if callable(self._zoom_callback):
            self._zoom_callback(factor)
        self._updating_zoom = False


class CaptureOverlay(QWidget):
    selectionMade = pyqtSignal(QPixmap, QRect, str)
    canceled = pyqtSignal()

    def __init__(self, screenshot: QPixmap, origin: QPoint, screen):
        super().__init__()
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        self.setWindowState(Qt.WindowFullScreen)
        self.setCursor(Qt.CrossCursor)
        self.screenshot = screenshot
        self._screen = screen
        geo = screen.geometry()
        self.setGeometry(geo)
        self.selection = None
        self.origin = None
        self.cursor_pos = None
        self.setMouseTracking(True)
        # 移除 WA_TranslucentBackground，确保它是一个完全不透明的静态显示层
        # self.setAttribute(Qt.WA_TranslucentBackground) 
        
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setInterval(16)
        self._cursor_timer.timeout.connect(self._sync_cursor_position)
        self._cursor_timer.start()
        self._sync_cursor_position(force=True)
        # 核心修复：直接使用屏幕的逻辑几何尺寸 (geo) 进行比例换算，而不是 self.width()
        # 这样可以 100% 准确地适配 100%, 125%, 150%, 200% 等所有缩放比例
        self._scale_x = self._compute_scale(self.screenshot.width(), geo.width())
        self._scale_y = self._compute_scale(self.screenshot.height(), geo.height())
        
        # 彻底移除之前的 _dimmed_screenshot 预计算逻辑，实现瞬时启动

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        
        # 1. 直接绘制原始静态底图
        painter.drawPixmap(self.rect(), self.screenshot)

        # 2. 实时绘制一层半透明黑色遮罩 (利用 GPU 硬件加速)
        overlay_color = QColor(0, 0, 0, 120)
        painter.fillRect(self.rect(), overlay_color)

        # 3. 如果有选区，在高亮区重新绘制原始清晰图
        if self.selection and self.selection.isValid():
            device_rect = self._device_rect(self.selection)
            painter.drawPixmap(self.selection, self.screenshot, device_rect)
            
            # 画选区边框
            painter.setPen(QPen(QColor(30, 144, 255), 2))
            painter.drawRect(self.selection)
        
        self._draw_magnifier(painter)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.origin = event.pos()
            self.selection = QRect(self.origin, self.origin)
            self.cursor_pos = event.pos()
            self.update()

    def mouseMoveEvent(self, event):
        if self.origin:
            self.selection = QRect(self.origin, event.pos()).normalized()
            self.cursor_pos = event.pos()
            self.update()
        else:
            self.cursor_pos = event.pos()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.selection:
            rect = self.selection.normalized()
            if rect.width() > 5 and rect.height() > 5:
                device_rect = self._device_rect(rect)
                cropped = self.screenshot.copy(device_rect)
                self.selectionMade.emit(cropped, rect, self._screen.name())
            self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.canceled.emit()
            self.close()

    def _draw_magnifier(self, painter: QPainter):
        if self.cursor_pos is None:
            return
        
        # 放大镜源代码区域大小（逻辑像素）
        src_half = 16
        size = QSize(src_half * 2, src_half * 2)
        logical_rect = QRect(
            QPoint(self.cursor_pos.x() - src_half, self.cursor_pos.y() - src_half),
            size,
        )
        
        # 转换为物理像素坐标
        source_rect = self._device_rect(logical_rect)
        if source_rect.isEmpty():
            return

        # 截取底图中的对应像素
        snippet = self.screenshot.copy(source_rect)
        if snippet.isNull():
            return

        # 准备缩放
        zoom = 5
        dest_size = QSize(size.width() * zoom, size.height() * zoom)
        magnified = snippet.scaled(
            dest_size, Qt.KeepAspectRatio, Qt.FastTransformation
        )

        # 计算放大镜显示位置（逻辑坐标）
        margin = 20
        dest_top_left = QPoint(self.cursor_pos.x() + margin, self.cursor_pos.y() + margin)
        if dest_top_left.x() + dest_size.width() > self.width():
            dest_top_left.setX(self.cursor_pos.x() - margin - dest_size.width())
        if dest_top_left.y() + dest_size.height() > self.height():
            dest_top_left.setY(self.cursor_pos.y() - margin - dest_size.height())
        dest_rect = QRect(dest_top_left, dest_size)

        # 绘制放大镜外框和图像
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.fillRect(dest_rect.adjusted(-4, -4, 4, 4), QColor(0, 0, 0, 180))
        painter.drawPixmap(dest_rect, magnified)
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.drawRect(dest_rect)

        # 绘制十字线
        center_x = dest_rect.center().x()
        center_y = dest_rect.center().y()
        painter.setPen(QPen(QColor(255, 100, 100), 1, Qt.DashLine))
        painter.drawLine(center_x, dest_rect.top(), center_x, dest_rect.bottom())
        painter.drawLine(dest_rect.left(), center_y, dest_rect.right(), center_y)

    def _device_rect(self, logical_rect: QRect):
        rect = self._logical_device_rect(logical_rect)
        return self._clamp_to_pixmap(rect)

    def _logical_device_rect(self, logical_rect: QRect):
        if logical_rect is None or not logical_rect.isValid():
            return QRect()
        # 使用 float 进行精确乘法，然后再四舍五入到物理像素
        x = int(round(logical_rect.x() * self._scale_x))
        y = int(round(logical_rect.y() * self._scale_y))
        w = int(round(logical_rect.width() * self._scale_x))
        h = int(round(logical_rect.height() * self._scale_y))
        return QRect(x, y, w, h)

    def _logical_device_point(self, logical_point: QPoint):
        if logical_point is None:
            return QPoint()
        x = int(round(logical_point.x() * self._scale_x))
        y = int(round(logical_point.y() * self._scale_y))
        return QPoint(x, y)

    def _sync_cursor_position(self, force=False):
        global_pos = QCursor.pos()
        local_pos = self.mapFromGlobal(global_pos)
        if self.rect().contains(local_pos):
            new_pos = local_pos
        else:
            new_pos = None
        if force or new_pos != self.cursor_pos:
            self.cursor_pos = new_pos
            self.update()

    def _clamp_to_pixmap(self, rect: QRect):
        if self.screenshot.isNull():
            return QRect(rect)
        max_w = self.screenshot.width()
        max_h = self.screenshot.height()
        x = max(0, min(rect.x(), max_w - 1))
        y = max(0, min(rect.y(), max_h - 1))
        w = max(1, min(rect.width(), max_w - x))
        h = max(1, min(rect.height(), max_h - y))
        return QRect(x, y, w, h)

    def _compute_scale(self, device, logical):
        if logical <= 0:
            return 1.0
        return max(1e-6, device / float(logical))


class ScreenSnapApp(QMainWindow):
    def __init__(self, start_minimized=False):
        super().__init__()
        self.setWindowTitle("Snapshot Studio - Windows 截图工具")
        self.setWindowIcon(get_app_icon())
        self._start_minimized = start_minimized
        self.config = load_config()
        self.ai_settings = _normalized_ai_settings(self.config.get("ai_settings", {}))
        self._active_overlays = []
        self._last_capture_screen_name = None
        self.auto_save_enabled = bool(self.config.get("auto_save_enabled", False))
        self.auto_start_enabled = bool(self.config.get("auto_start_enabled", False))
        self._image_quality = int(self.config.get("image_quality", DEFAULT_IMAGE_QUALITY))
        self.workspace_zoom = float(self.config.get("workspace_zoom", 1.0))
        self.workspace_zoom = max(0.25, min(2.0, self.workspace_zoom))
        self.close_behavior = self.config.get("close_behavior", "tray")
        if self.close_behavior not in ("tray", "exit"):
            self.close_behavior = "tray"
        self.exit_unsaved_policy = self.config.get("exit_unsaved_policy", "save_all")
        if self.exit_unsaved_policy not in ("save_all", "discard_all", "ask"):
            self.exit_unsaved_policy = "save_all"
        self.marker_style = self.config.get("marker_style", DEFAULT_MARKER_STYLE.copy())
        self.rectangle_style = self.config.get("rectangle_style", DEFAULT_RECT_STYLE.copy())
        self.text_style = self.config.get("text_style", DEFAULT_TEXT_STYLE.copy())
        self.text_style["font"] = _sanitize_font_family(self.text_style.get("font"))
        self.image_border_style = self.config.get("image_border_style", DEFAULT_IMAGE_BORDER_STYLE.copy())

        self.workspace_page = AnnotationWorkspacePage(
            lambda: self._open_settings_dialog(),
            self._open_images_dialog,
            {
                "marker": self.marker_style,
                "rectangle": self.rectangle_style,
                "text": self.text_style,
                "image_border": self.image_border_style,
            },
            self._on_style_changed,
            self._image_quality,
            self.auto_save_enabled,
            default_zoom=self.workspace_zoom,
            zoom_changed_callback=self._on_workspace_zoom_changed,
        )
        self._hotkey_manager = GlobalHotkeyManager(self)
        app = QApplication.instance()
        if app:
            app.applicationStateChanged.connect(self._on_app_state_changed)
        self._last_selection_rect = None
        self._save_dir = (self.config.get("save_dir") or "").strip()
        self._force_exit_once = False
        self._exiting = False
        self._pending_capture_handler = None

        main_widget = QWidget()
        root_layout = QVBoxLayout(main_widget)

        self.nav_toolbar = QToolBar()
        self.nav_toolbar.setObjectName("PrimaryNav")
        self.nav_toolbar.setMovable(False)
        self.nav_toolbar.setIconSize(QSize(0, 0))
        self.nav_toolbar.setContextMenuPolicy(Qt.PreventContextMenu)
        self.nav_actions = {}
        self.nav_color_map = {
            "home": "#FF8BA7",
            "edit": "#2ED3A3",
            "ai": "#2C7AFA",
            "about": "#BD93FF",
        }
        for text, key in [
            ("首页", "home"),
            ("图片编辑", "edit"),
            ("AI截图翻译", "ai"),
            ("关于", "about"),
        ]:
            action = QAction(text, self)
            action.setCheckable(True)
            action.triggered.connect(lambda _, k=key: self._switch_page(k))
            self.nav_toolbar.addAction(action)
            self.nav_actions[key] = action
            button = self.nav_toolbar.widgetForAction(action)
            if button:
                button.setObjectName(f"Nav_{key}")
        exit_action = QAction("退出", self)
        exit_action.triggered.connect(self._trigger_exit_action)
        self.nav_toolbar.addAction(exit_action)
        exit_button = self.nav_toolbar.widgetForAction(exit_action)
        if exit_button:
            exit_button.setObjectName("Nav_exit")
        
        # 添加spacer将作者信息推到右边
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.nav_toolbar.addWidget(spacer)
        
        # 添加作者信息标签（低调显示在右侧）
        author_label = QLabel("Designed by 乾颐堂 现任明教教主")
        author_label.setObjectName("AuthorLabel")
        author_label.setStyleSheet("color: rgba(255,255,255,0.4); font-size: 11px; padding-right: 8px;")
        self.nav_toolbar.addWidget(author_label)
        
        self._apply_nav_toolbar_style()
        root_layout.addWidget(self.nav_toolbar)

        self.pages = QStackedWidget()
        self.home_page = HomePage(self._save_dir)
        self.edit_page = self.workspace_page
        self.translation_panel = AITranslationPanel()
        self.translation_panel.translationCompleted.connect(lambda: self._switch_page("ai"))
        self.ai_page_widget = QWidget()
        ai_layout = QVBoxLayout(self.ai_page_widget)
        ai_layout.addWidget(self.translation_panel)
        ai_layout.setContentsMargins(0, 0, 0, 0)
        self.pages.addWidget(self.home_page)
        self.pages.addWidget(self.edit_page)
        self.pages.addWidget(self.ai_page_widget)
        self.about_page = AboutPage()
        self.pages.addWidget(self.about_page)
        self._pages = {
            "home": self.home_page,
            "edit": self.edit_page,
            "ai": self.ai_page_widget,
            "about": self.about_page,
        }
        root_layout.addWidget(self.pages, 1)

        self.home_page.openFolderRequested.connect(self._open_save_folder)
        self.home_page.captureRequested.connect(self.initiate_capture)
        self.home_page.repeatRequested.connect(self._repeat_capture)
        self.home_page.aiTranslateRequested.connect(self.start_ai_translation_capture)
        self.home_page.openImagesRequested.connect(self._open_images_dialog)
        self.home_page.openSettingsRequested.connect(self._open_settings_dialog)
        self.home_page.openWorkspaceRequested.connect(self._open_workspace)
        self._current_page = "home"
        self._update_nav_state()

        self.setCentralWidget(main_widget)
        self.resize(1100, 750)

        self.home_page.set_repeat_enabled(False)
        self._register_all_hotkeys()
        self._update_hotkey_summary()
        self._update_ai_feature_state()
        self.tray_icon = None
        self._tray_message_shown = False
        self._closing_via_tray_exit = False
        self._setup_tray_icon()
        self._sync_autostart_entry()
        if start_minimized:
            QTimer.singleShot(0, self._minimize_to_tray)
        else:
            self.show()
        self.translation_panel.set_ai_settings(self.ai_settings)

    def _set_process_priority(self, high=True):
        """设置进程优先级，确保截图过程获得最高优先级资源"""
        if win32process is None:
            return
        try:
            handle = win32api.GetCurrentProcess()
            priority = win32con.HIGH_PRIORITY_CLASS if high else win32con.NORMAL_PRIORITY_CLASS
            win32process.SetPriorityClass(handle, priority)
            logging.info(f"Process priority set to {'HIGH' if high else 'NORMAL'}")
        except Exception as e:
            logging.warning(f"Failed to set process priority: {e}")

    def _switch_page(self, key):
        # region agent log
        # H7: 页面是否被意外切换到 AI（用户感觉在自动 OCR）
        _agent_debug_log(
            hypothesisId="H7",
            location="screenshot_tool.py:ScreenSnapApp._switch_page",
            message="switch page",
            data={"key": key},
        )
        # endregion
        if key not in self._pages:
            return
        self.pages.setCurrentWidget(self._pages[key])
        self._current_page = key
        self._update_nav_state()
        if key == "edit" and hasattr(self, "workspace_page"):
            self.workspace_page.restore_last_active_tab()

    def _on_app_state_changed(self, state):
        # 当应用程序重新获得焦点时，不再强制恢复标签位置，
        # 这样当你去别的窗口“贴图”再回来时，标签栏会保持在你离开时的样子。
        pass

    def _update_nav_state(self):
        for key, action in self.nav_actions.items():
            action.blockSignals(True)
            action.setChecked(key == self._current_page)
            action.blockSignals(False)

    def _apply_nav_toolbar_style(self):
        if not hasattr(self, "nav_toolbar"):
            return
        base = [
            "QToolBar#PrimaryNav { background: #050a1c; border: none; padding: 8px 14px; }",
            "QToolBar#PrimaryNav QToolButton { border-radius: 6px; padding: 4px 14px; font-weight:600; margin-right: 6px; color: #f7f8ff; background: rgba(255,255,255,0.08); }",
            "QToolBar#PrimaryNav QToolButton:checked { color: #0a101d; }",
        ]
        for key, accent in getattr(self, "nav_color_map", {}).items():
            accent_color = QColor(accent)
            soft = accent_color.lighter(180).name()
            base.append(f"QToolBar#PrimaryNav QToolButton#Nav_{key} {{ background: {soft}; color:#0f1527; }}")
            base.append(f"QToolBar#PrimaryNav QToolButton#Nav_{key}:checked {{ background: {accent}; color:#050a12; }}")
        base.append("QToolBar#PrimaryNav QToolButton#Nav_exit { background: transparent; border:1px solid rgba(255,255,255,0.25); color:#f5f6ff; }")
        base.append("QToolBar#PrimaryNav QToolButton#Nav_exit:hover { background: rgba(255,255,255,0.15); }")
        self.nav_toolbar.setStyleSheet("".join(base))

    def _resolved_save_dir(self):
        return self._save_dir or DEFAULT_SAVE_DIR

    def _open_save_folder(self):
        target_dir = self._resolved_save_dir()
        try:
            os.makedirs(target_dir, exist_ok=True)
        except OSError as exc:
            _show_message_box(
                "保存目录不可用",
                f"无法访问保存目录：{target_dir}\n请检查权限或更换目录。\n\n系统信息: {exc}",
                parent=self,
                critical=True,
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(target_dir))

    def _open_workspace(self):
        self._switch_page("edit")

    def _open_images_dialog(self):
        filters = "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff);;所有文件 (*)"
        initial_dir = self._resolved_save_dir()
        files, _ = QFileDialog.getOpenFileNames(self, "选择图片文件", initial_dir, filters)
        if not files:
            return
        self.workspace_page.open_image_files(files)
        self._focus_workspace()

    def _focus_workspace(self):
        self._switch_page("edit")
        self._show_main_window()

    def _show_main_window(self):
        if self.tray_icon and self.tray_icon.isVisible():
            self._restore_from_tray()
            return
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def _trigger_exit_action(self):
        self._force_exit_once = True
        logging.info("Exit action triggered from nav toolbar")
        self.close()

    def _prepare_save_dir(self):
        current_dir = self._resolved_save_dir()
        temp_dir = os.path.join(tempfile.gettempdir(), "ctk_snapshot")
        candidates = [(current_dir, "current")]
        if current_dir != DEFAULT_SAVE_DIR:
            candidates.append((DEFAULT_SAVE_DIR, "default"))
        if temp_dir not in {path for path, _ in candidates}:
            candidates.append((temp_dir, "temp"))
        errors = []
        for path, label in candidates:
            try:
                os.makedirs(path, exist_ok=True)
                if label == "current" and self._save_dir:
                    self.config["save_dir"] = self._save_dir
                    save_config(self.config, parent=self)
                if label == "default" and errors:
                    _show_message_box(
                        "保存目录不可用",
                        f"无法创建保存目录：{errors[0][0]}\n已切换到默认目录：{DEFAULT_SAVE_DIR}\n\n系统信息: {errors[0][1]}",
                        parent=self,
                    )
                    self._save_dir = ""
                    self.config["save_dir"] = self._save_dir
                    save_config(self.config, parent=self)
                elif label == "temp" and errors:
                    combined = "\n".join(f"{p}: {e}" for p, e in errors)
                    _show_message_box(
                        "保存目录不可用",
                        f"无法创建保存目录：{current_dir}\n已切换到临时目录：{temp_dir}\n请在设置中更换到有写入权限的目录。\n\n{combined}",
                        parent=self,
                    )
                    self._save_dir = temp_dir
                    self.config["save_dir"] = self._save_dir
                    save_config(self.config, parent=self)
                return path
            except OSError as exc:
                errors.append((path, exc))
        combined = "\n".join(f"{p}: {e}" for p, e in errors)
        _show_message_box(
            "保存目录不可用",
            f"无法创建任何可用的保存目录，请检查权限或磁盘空间。\n\n{combined}",
            parent=self,
            critical=True,
        )
        return None

    def _start_capture_session(self, handler):
        self._pending_capture_handler = handler
        
        # 核心优化 1：立即提升优先级，并使用透明度瞬间“隐藏”窗口，不再死等 150ms
        self._set_process_priority(True)
        was_visible = self.isVisible() and not self.isMinimized()
        if was_visible:
            self.setWindowOpacity(0.0) # 视觉上瞬间消失
            self.hide()
            # 仅在必要时给系统极短的处理时间 (10ms) 刷新 DWM
            QApplication.processEvents()
        
        logging.info("Start capture session; was_visible=%s, handler=%s", 
                     was_visible, handler.__name__ if hasattr(handler, "__name__") else str(handler))
        
        # 核心优化 2：立即进入截屏，不再使用 QTimer 延迟触发
        self._start_overlay_capture()
        
        # 恢复透明度以便下次显示
        if was_visible:
            self.setWindowOpacity(1.0)

    def initiate_capture(self):
        # region agent log
        _agent_debug_log(
            hypothesisId="H7",
            location="screenshot_tool.py:ScreenSnapApp.initiate_capture",
            message="initiate_capture called",
            data={},
        )
        # endregion
        save_dir = self._prepare_save_dir()
        if not save_dir:
            return
        logging.info("Hotkey capture triggered")
        self._start_capture_session(self._handle_annotation_capture)

    def start_ai_translation_capture(self):
        # region agent log
        _agent_debug_log(
            hypothesisId="H7",
            location="screenshot_tool.py:ScreenSnapApp.start_ai_translation_capture",
            message="start_ai_translation_capture called",
            data={},
        )
        # endregion
        if not self._ensure_ai_ready():
            return
        save_dir = self._prepare_save_dir()
        if not save_dir:
            return
        logging.info("AI capture triggered")
        self._start_capture_session(self._handle_ai_translation_capture)

    def _start_overlay_capture(self):
        # 记录启动开始时间用于调试，正式版可以移除
        start_time = time.time()
        
        screens = QGuiApplication.screens()
        if not screens:
            _show_message_box("错误", "未找到可用的屏幕设备，无法截图。", parent=self, critical=True)
            self._show_main_window()
            return
        
        self._clear_overlays()
        
        # 批量抓取所有屏幕像素
        screen_data = []
        for s in screens:
            try:
                pix = s.grabWindow(0)
                pix.setDevicePixelRatio(1.0)
                screen_data.append((s, pix))
            except Exception as e:
                logging.error(f"Failed to grab screen {s.name()}: {e}")

        # 快速构建并一键显出
        for screen, pixmap in screen_data:
            overlay = CaptureOverlay(pixmap, screen.geometry().topLeft(), screen)
            overlay.selectionMade.connect(self._on_overlay_selection)
            overlay.canceled.connect(self._on_capture_cancel)
            overlay.show()
            self._active_overlays.append(overlay)
            
        logging.info(f"Screen grab and overlay creation took {(time.time() - start_time):.3f}s")

    def _on_capture_cancel(self):
        self._clear_overlays()
        # 截图流程结束（用户取消），恢复正常优先级
        self._set_process_priority(False)
        self._pending_capture_handler = None
        self._show_main_window()
        logging.info("Capture canceled")

    def _on_overlay_selection(self, pixmap: QPixmap, selection_rect: QRect, screen_name: str):
        self._clear_overlays()
        # 截图流程结束，恢复正常优先级
        self._set_process_priority(False)
        handler = self._pending_capture_handler or self._handle_annotation_capture
        self._pending_capture_handler = None
        logging.info(
            "Overlay selection made; rect=%s, screen=%s, handler=%s",
            selection_rect,
            screen_name,
            handler.__name__ if hasattr(handler, "__name__") else str(handler),
        )
        handler(pixmap, selection_rect, screen_name)

    def _handle_annotation_capture(self, pixmap: QPixmap, selection_rect: QRect, screen_name: str):
        self._last_selection_rect = QRect(selection_rect)
        self._last_capture_screen_name = screen_name
        self.workspace_page.add_capture(pixmap, self._resolved_save_dir(), self.workspace_zoom)
        self.home_page.set_repeat_enabled(True)
        self._focus_workspace()
        self._resize_for_image(pixmap.size())
        _set_clipboard_pixmap_with_retry(pixmap)
        # region agent log
        # H4: “区域截图后直接 Ctrl+V”走 setPixmap，记录以对照 PPT 报错概率
        _agent_debug_log(
            hypothesisId="H4",
            location="screenshot_tool.py:ScreenSnapApp._handle_annotation_capture",
            message="annotation_capture wrote clipboard via setPixmap",
            data={"size": (pixmap.width(), pixmap.height()) if pixmap else None, "screen": screen_name},
        )
        # endregion
        logging.info("Annotation capture handled; size=%s", pixmap.size())

    def _handle_ai_translation_capture(self, pixmap: QPixmap, selection_rect: QRect, screen_name: str):
        # region agent log
        _agent_debug_log(
            hypothesisId="H7",
            location="screenshot_tool.py:ScreenSnapApp._handle_ai_translation_capture",
            message="handle_ai_translation_capture",
            data={
                "screen": screen_name,
                "rect": str(selection_rect),
                "pixmap_size": (pixmap.width(), pixmap.height()) if pixmap else None,
            },
        )
        # endregion
        self._last_selection_rect = QRect(selection_rect)
        self._last_capture_screen_name = screen_name
        self.home_page.set_repeat_enabled(True)
        self._switch_page("ai")
        self._show_main_window()
        self.translation_panel.add_capture(pixmap)
        logging.info("AI capture handled; size=%s", pixmap.size())

    def _hotkey_display_text(self, action_id):
        hotkey_info = self.config.get("hotkeys", {}).get(action_id, {})
        display = hotkey_info.get("display")
        shortcut = hotkey_info.get("shortcut")
        if not shortcut:
            return "未设置"
        if display:
            return display
        return _format_display_shortcut(shortcut)

    def _update_hotkey_summary(self):
        hotkeys = self.config.get("hotkeys", {})
        summary_lines = []
        for action_id, action_name in HOTKEY_ACTIONS:
            info = hotkeys.get(action_id, {})
            shortcut = info.get("shortcut")
            if shortcut:
                display = info.get("display") or _format_display_shortcut(shortcut)
            else:
                display = "未设置"
            summary_lines.append(f"{action_name}: {display}")
        summary_text = "\n".join(summary_lines) if summary_lines else "尚未配置快捷键。"
        if hasattr(self, "home_page"):
            self.home_page.set_hotkey_summary(summary_text)

    def _ensure_ai_ready(self):
        if not AITranslationService.is_configured(self.ai_settings):
            self._prompt_configure_ai()
            return False
        if OpenAI is None:
            QMessageBox.warning(self, "缺少依赖", "未安装 openai SDK，请先在命令行执行 pip install openai。")
            return False
        return True

    def _prompt_configure_ai(self):
        box = QMessageBox(self)
        box.setWindowTitle("AI 功能未配置")
        box.setText("请先在“系统设置 -> AI 设置”中填入 base_url、API Key 和模型名称。")
        settings_btn = box.addButton("打开设置", QMessageBox.AcceptRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec_()
        if box.clickedButton() == settings_btn:
            self._open_settings_dialog()

    def _update_ai_feature_state(self):
        enabled = AITranslationService.is_configured(self.ai_settings) and OpenAI is not None
        if hasattr(self, "home_page"):
            self.home_page.set_ai_translate_enabled(enabled)


    def _on_workspace_zoom_changed(self, factor):
        try:
            factor = float(factor)
        except (TypeError, ValueError):
            return
        clamped = max(0.25, min(2.0, factor))
        if abs(clamped - self.workspace_zoom) < 0.001:
            return
        self.workspace_zoom = clamped
        self.config["workspace_zoom"] = self.workspace_zoom
        save_config(self.config, parent=self)

    def _open_settings_dialog(self, parent=None):
        dialog = SettingsDialog(parent or self, self.config)
        if dialog.exec_() == QDialog.Accepted:
            self.config["hotkeys"] = dialog.get_hotkeys()
            self.config["image_quality"] = dialog.get_image_quality()
            self._image_quality = int(self.config["image_quality"])
            self.ai_settings = _normalized_ai_settings(dialog.get_ai_settings())
            self.config["ai_settings"] = self.ai_settings
            general_settings = dialog.get_general_settings()
            self.auto_save_enabled = bool(general_settings.get("auto_save_enabled", False))
            new_dir = general_settings.get("save_dir")
            if new_dir is None:
                new_dir = self._save_dir
            self._save_dir = (new_dir or "").strip()
            self.config["save_dir"] = self._save_dir
            self.config["auto_save_enabled"] = self.auto_save_enabled
            new_auto_start = bool(general_settings.get("auto_start_enabled", False))
            self.auto_start_enabled = new_auto_start
            self.config["auto_start_enabled"] = self.auto_start_enabled
            self.close_behavior = general_settings.get("close_behavior", self.close_behavior)
            if self.close_behavior not in ("tray", "exit"):
                self.close_behavior = "tray"
            self.exit_unsaved_policy = general_settings.get("exit_unsaved_policy", self.exit_unsaved_policy)
            if self.exit_unsaved_policy not in ("save_all", "discard_all"):
                self.exit_unsaved_policy = "save_all"
            self.config["close_behavior"] = self.close_behavior
            self.config["exit_unsaved_policy"] = self.exit_unsaved_policy
            save_config(self.config, parent=self)
            self.workspace_page.set_image_quality(self._image_quality)
            self.workspace_page.set_auto_save_enabled(self.auto_save_enabled)
            self._sync_autostart_entry()
            self._register_all_hotkeys()
            self._update_hotkey_summary()
            self._update_ai_feature_state()
            self.translation_panel.set_ai_settings(self.ai_settings)

    def _on_style_changed(self, style_type, data):
        if style_type == "marker":
            self.marker_style.update(data)
            self.config["marker_style"] = self.marker_style
        elif style_type == "rectangle":
            self.rectangle_style.update(data)
            self.config["rectangle_style"] = self.rectangle_style
        elif style_type == "text":
            self.text_style.update(data)
            self.config["text_style"] = self.text_style
        elif style_type == "image_border":
            self.image_border_style.update(data)
            self.config["image_border_style"] = self.image_border_style
        save_config(self.config, parent=self)

    def _register_all_hotkeys(self):
        self._hotkey_manager.unregister_all()
        hotkeys = self.config.get("hotkeys", {})
        for action_id, _ in HOTKEY_ACTIONS:
            shortcut = hotkeys.get(action_id, {}).get("shortcut")
            self._try_register_hotkey(action_id, shortcut)

    def _try_register_hotkey(self, action_id, shortcut):
        self._hotkey_manager.unregister(action_id)
        if not shortcut:
            return
        try:
            self._hotkey_manager.register(action_id, shortcut)
        except ValueError as exc:
            QMessageBox.warning(
                self,
                "热键无效",
                str(exc),
            )
        except OSError as exc:
            QMessageBox.warning(
                self,
                "热键注册失败",
                f"无法在系统中注册热键 ({shortcut})，请尝试更换组合或以管理员身份运行。\n\n系统信息: {exc}",
            )

    def _teardown_hotkeys(self):
        self._hotkey_manager.unregister_all()

    def _trigger_hotkey_action(self, action_id):
        # region agent log
        _agent_debug_log(
            hypothesisId="H7",
            location="screenshot_tool.py:ScreenSnapApp._trigger_hotkey_action",
            message="trigger hotkey action",
            data={"action_id": action_id},
        )
        # endregion
        if action_id == "capture":
            self.initiate_capture()
        elif action_id == "repeat_capture":
            self._repeat_capture()
        elif action_id == "ai_translate":
            self.start_ai_translation_capture()

    def _on_hotkey_trigger(self, action_id):
        if self._active_overlays:
            return
        logging.info("Hotkey fired: %s", action_id)
        # region agent log
        _agent_debug_log(
            hypothesisId="H7",
            location="screenshot_tool.py:ScreenSnapApp._on_hotkey_trigger",
            message="hotkey fired",
            data={"action_id": action_id},
        )
        # endregion
        QTimer.singleShot(0, lambda: self._trigger_hotkey_action(action_id))

    def nativeEvent(self, eventType, message):
        if eventType in ("windows_generic_MSG", "windows_dispatcher_MSG"):
            try:
                msg = wintypes.MSG.from_address(message.__int__())
                if msg.message == WM_HOTKEY:
                    self._hotkey_manager.handle_message(msg.wParam)
                    return True, 0
            except KeyboardInterrupt:
                logging.warning("KeyboardInterrupt received inside nativeEvent; quitting")
                QTimer.singleShot(0, self.close)
                return False, 0
            except Exception as exc:
                logging.error("nativeEvent error: %s", exc, exc_info=True)
        return super().nativeEvent(eventType, message)

    def _repeat_capture(self):
        if not self._last_selection_rect:
            QMessageBox.information(self, "重复截图", "请先进行一次截图，随后才能使用重复截图功能。")
            return
        self.hide()
        logging.info("Repeat capture triggered")
        QTimer.singleShot(200, self._do_repeat_capture)

    def _do_repeat_capture(self):
        target_screen = self._screen_by_name(self._last_capture_screen_name) or self._screen_for_cursor()
        screen = target_screen or QGuiApplication.primaryScreen()
        if not screen:
            QMessageBox.critical(self, "错误", "未找到可用的屏幕设备，无法截图。")
            self.show()
            return
        rect = QRect(self._last_selection_rect)
        if rect.width() < 5 or rect.height() < 5:
            QMessageBox.warning(self, "重复截图失败", "上次的截图区域无效，请重新截取。")
            self.show()
            return
        screenshot = self._grab_screen_pixmap(screen)
        cropped = self._copy_from_pixmap(screenshot, rect, screen)
        self.workspace_page.add_capture(cropped, self._resolved_save_dir(), self.workspace_zoom)
        self._focus_workspace()
        self._resize_for_image(cropped.size())
        _set_clipboard_pixmap_with_retry(cropped)
        # region agent log
        # H4: “重复截图后直接 Ctrl+V”走 setPixmap，记录路径以区分 MIME 写法 vs Pixmap 写法
        _agent_debug_log(
            hypothesisId="H4",
            location="screenshot_tool.py:ScreenSnapApp._do_repeat_capture",
            message="repeat_capture wrote clipboard via setPixmap",
            data={"size": (cropped.width(), cropped.height()) if cropped else None},
        )
        # endregion
        logging.info("Repeat capture done; size=%s", cropped.size())

    def closeEvent(self, event):
        if self._exiting:
            event.accept()
            return
        behavior = self.close_behavior
        tray_available = bool(self.tray_icon and QSystemTrayIcon.isSystemTrayAvailable())
        if behavior == "tray" and not tray_available:
            behavior = "exit"
        if self._closing_via_tray_exit:
            behavior = "exit"
        if getattr(self, "_force_exit_once", False):
            behavior = "exit"
        logging.info(
            "closeEvent fired; behavior=%s tray_available=%s closing_via_tray=%s force_exit=%s dirty_tabs=%s",
            behavior,
            tray_available,
            getattr(self, "_closing_via_tray_exit", False),
            getattr(self, "_force_exit_once", False),
            len(self.workspace_page.get_dirty_tabs()) if hasattr(self, "workspace_page") else "n/a",
        )
        if behavior == "tray":
            event.ignore()
            self._minimize_to_tray()
            return
        if not self._handle_unsaved_before_exit():
            event.ignore()
            logging.info("closeEvent canceled by unsaved prompt")
            self._closing_via_tray_exit = False
            self._force_exit_once = False
            return
        self._cleanup_before_exit()
        self._closing_via_tray_exit = False
        self._force_exit_once = False
        if behavior == "exit":
            self._exiting = True
            QTimer.singleShot(0, QApplication.instance().quit)
        logging.info("closeEvent accepted, app will quit")
        super().closeEvent(event)

    def _handle_unsaved_before_exit(self):
        dirty_tabs = self.workspace_page.get_dirty_tabs()
        if not dirty_tabs:
            return True
        return self._prompt_exit_decision(True)

    def _prompt_exit_decision(self, has_dirty):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning if has_dirty else QMessageBox.Question)
        box.setWindowTitle("退出确认")
        if has_dirty:
            box.setText("检测到存在未保存的图片，退出前请选择操作。")
        else:
            box.setText("当前图片已保存，仍然要退出吗？")
        save_btn = box.addButton("保存全部后退出", QMessageBox.AcceptRole)
        discard_btn = box.addButton("不保存直接退出", QMessageBox.DestructiveRole)
        cancel_btn = box.addButton("取消", QMessageBox.RejectRole)
        default_policy = self.exit_unsaved_policy
        if default_policy == "save_all":
            box.setDefaultButton(save_btn)
        elif default_policy == "discard_all":
            box.setDefaultButton(discard_btn)
        else:
            box.setDefaultButton(cancel_btn)
        box.exec_()
        clicked = box.clickedButton()
        if clicked == save_btn:
            if has_dirty:
                return self.workspace_page.save_all_dirty()
            return True
        if clicked == discard_btn:
            return True
        return False

    def _resize_for_image(self, image_size: QSize):
        return

    def _setup_tray_icon(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray_icon = None
            return
        self.tray_icon = QSystemTrayIcon(get_app_icon(), self)
        self.tray_icon.setToolTip("CTK Snapshot")
        tray_menu = QMenu()
        restore_action = QAction("显示窗口", self)
        exit_action = QAction("退出", self)
        tray_menu.addAction(restore_action)
        tray_menu.addAction(exit_action)
        restore_action.triggered.connect(self._restore_from_tray)
        exit_action.triggered.connect(self._exit_from_tray)
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_icon_activated)
        self.tray_icon.hide()

    def _minimize_to_tray(self):
        if not self.tray_icon:
            self.hide()
            return
        self.tray_icon.show()
        if not self._tray_message_shown:
            self.tray_icon.showMessage("CTK Snapshot", "程序已最小化到系统托盘", QSystemTrayIcon.Information, 3000)
            self._tray_message_shown = True
        self.hide()
        logging.info("Window minimized to tray")

    def _restore_from_tray(self):
        if self.tray_icon:
            self.tray_icon.hide()
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_tray_icon_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._restore_from_tray()

    def _exit_from_tray(self):
        self._closing_via_tray_exit = True
        if self.tray_icon:
            self.tray_icon.hide()
        logging.info("Exit triggered from tray menu")
        self.close()

    def _cleanup_before_exit(self):
        self._clear_overlays()
        self._teardown_hotkeys()
        if self.tray_icon:
            self.tray_icon.hide()
        self.tray_icon = None
        logging.info("Cleanup before exit completed")

    def _sync_autostart_entry(self):
        command = self._autostart_command()
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_REG_PATH, 0, winreg.KEY_ALL_ACCESS)
        except FileNotFoundError:
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_REG_PATH)
        try:
            if self.auto_start_enabled:
                winreg.SetValueEx(key, RUN_REG_NAME, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, RUN_REG_NAME)
                except FileNotFoundError:
                    pass
        except OSError as exc:
            QMessageBox.warning(self, "自动启动", f"无法更新系统启动项，请手动配置或以管理员身份运行。\n\n系统信息: {exc}")
        finally:
            winreg.CloseKey(key)

    def _autostart_command(self):
        if getattr(sys, "frozen", False):
            exe_path = sys.executable
            return f"\"{exe_path}\" --minimized"
        exe_path = sys.executable
        script_path = os.path.abspath(sys.argv[0])
        return f"\"{exe_path}\" \"{script_path}\" --minimized"

    def _clear_overlays(self):
        while self._active_overlays:
            overlay = self._active_overlays.pop()
            try:
                overlay.selectionMade.disconnect(self._on_overlay_selection)
                overlay.canceled.disconnect(self._on_capture_cancel)
            except Exception:
                pass
            overlay.close()
            overlay.deleteLater()

    def _screen_for_cursor(self):
        pos = QCursor.pos()
        screen = QGuiApplication.screenAt(pos)
        if not screen:
            screen = QGuiApplication.primaryScreen()
        return screen

    def _screen_by_name(self, name):
        if not name:
            return None
        for screen in QGuiApplication.screens():
            if screen.name() == name:
                return screen
        return None

    def _copy_from_pixmap(self, pixmap, rect: QRect, screen):
        device_rect = self._logical_rect_to_device(rect, pixmap, screen)
        return pixmap.copy(device_rect)

    def _logical_rect_to_device(self, rect: QRect, pixmap: QPixmap, screen):
        if rect is None:
            return QRect()
        logical_width = screen.geometry().width() if screen else pixmap.width()
        logical_height = screen.geometry().height() if screen else pixmap.height()
        logical_width = max(1, logical_width)
        logical_height = max(1, logical_height)
        pix_w = max(1, pixmap.width())
        pix_h = max(1, pixmap.height())
        scale_x = pix_w / float(logical_width)
        scale_y = pix_h / float(logical_height)
        x = int(round(rect.x() * scale_x))
        y = int(round(rect.y() * scale_y))
        w = max(1, int(round(rect.width() * scale_x)))
        h = max(1, int(round(rect.height() * scale_y)))
        x = max(0, min(x, pix_w - 1))
        y = max(0, min(y, pix_h - 1))
        if x + w > pix_w:
            w = pix_w - x
        if y + h > pix_h:
            h = pix_h - y
        return QRect(x, y, w, h)

    def _display_zoom_factor(self, pixmap: QPixmap, logical_rect: QRect):
        if pixmap.isNull() or logical_rect.width() <= 0 or logical_rect.height() <= 0:
            return 1.0
        pix_w = pixmap.width()
        pix_h = pixmap.height()
        ratio_w = logical_rect.width() / float(pix_w) if pix_w else 1.0
        ratio_h = logical_rect.height() / float(pix_h) if pix_h else 1.0
        ratio = min(ratio_w, ratio_h)
        if ratio <= 0:
            return 1.0
        return max(0.1, min(1.0, ratio))

    def _grab_screen_pixmap(self, screen):
        native = screen.grabWindow(0)
        native.setDevicePixelRatio(1.0)
        return native


def main():
    _setup_logging()
    logging.info("Starting Snapshot app with args: %s", sys.argv)
    _ensure_qt_plugins_path()
    guard = SingleInstanceGuard()
    if guard.already_running:
        _notify_instance_running()
        guard.release()
        return
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    start_minimized = False
    qt_args = []
    for arg in sys.argv:
        if arg == "--minimized":
            start_minimized = True
        else:
            qt_args.append(arg)
    sys.argv = qt_args
    app = QApplication(qt_args)
    logging.info("QApplication created")
    try:
        app.aboutToQuit.connect(lambda: logging.info("aboutToQuit emitted"))
        app.lastWindowClosed.connect(lambda: logging.info("lastWindowClosed emitted"))
    except Exception:
        pass
    try:
        app.setQuitOnLastWindowClosed(False)
        logging.info("Set quitOnLastWindowClosed=False to keep app alive when overlays close")
    except Exception as exc:
        logging.warning("Failed to set quitOnLastWindowClosed: %s", exc)
    if _USER_DATA_ERROR:
        _show_message_box(
            "存储目录受限",
            f"无法创建用户数据目录，将尝试使用程序所在目录保存数据，可能需要管理员权限。\n\n系统信息: {_USER_DATA_ERROR}",
            critical=False,
        )
    font = QFont("Microsoft YaHei", 10, QFont.Light)
    app.setFont(font)
    app.setWindowIcon(get_app_icon())
    window = ScreenSnapApp(start_minimized=start_minimized)
    if not start_minimized:
        window.show()
    try:
        logging.info("Entering Qt event loop")
        exit_code = 0
        try:
            exit_code = app.exec_()
            logging.info("Event loop exited code=%s", exit_code)
        except KeyboardInterrupt:
            logging.warning("KeyboardInterrupt received; quitting app")
            try:
                app.quit()
                QApplication.processEvents()
            except Exception:
                pass
            exit_code = 1
        sys.exit(exit_code)
    finally:
        logging.info("Releasing single instance mutex")
        guard.release()


if __name__ == "__main__":
    main()
