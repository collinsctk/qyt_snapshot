import base64
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
import sys
import winreg
from datetime import datetime
from enum import Enum, auto

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - optional dependency
    OpenAI = None

from PyQt5.QtCore import (
    QPoint,
    QRect,
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
)
from PyQt5.QtGui import (
    QColor,
    QGuiApplication,
    QPainter,
    QPen,
    QPixmap,
    QFont,
    QIcon,
    QDesktopServices,
    QKeySequence,
    QCursor,
)
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QColorDialog,
    QFileDialog,
    QLabel,
    QLineEdit,
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
    QSystemTrayIcon,
    QMenu,
    QPlainTextEdit,
    QFormLayout,
    QSplitter,
)


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
USER_CONFIG_FILE = os.path.join(BASE_DIR, "user.json")
DEFAULT_SAVE_DIR = os.path.join(BASE_DIR, "screenshots")
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

DEFAULT_IMAGE_QUALITY = 95
AI_TRANSLATION_PROMPT = (
    "请识别这张截图里的所有文字（保留原始顺序、标点和空格），"
    "然后将识别结果和翻译合并到 JSON："
    "{\"original\": \"原文\", \"translation\": \"翻译\"}。"
)
DEFAULT_AI_SETTINGS = {
    "base_url": "https://aihubmix.com/v1",
    "api_key": "",
    "model": "gpt-4.1-nano",
}
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
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            try:
                return json.load(handle)
            except json.JSONDecodeError:
                return {}
    return {}


def load_config():
    defaults = _load_json_file(CONFIG_FILE)
    overrides = _load_json_file(USER_CONFIG_FILE)
    config = {}
    if isinstance(defaults, dict):
        config.update(defaults)
    if isinstance(overrides, dict):
        config.update(overrides)
    return config


def save_config(data):
    with open(USER_CONFIG_FILE, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)


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
            "下面是识别到的英文原文，请把它翻译为简洁流畅的中文：\n\n"
            + text.strip()
            + "\n\n只返回翻译文本。"
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
        try:
            service = AITranslationService(self._settings)
            original, translation, _ = service.translate_image(self._image_bytes)
            self.completed.emit(original, translation)
        except Exception as exc:
            self.failed.emit(str(exc))


class Tool(Enum):
    NONE = auto()
    RECTANGLE = auto()
    MARKER = auto()

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
        desc = QLabel("设置后可以在任意窗口通过快捷键触发截图动作。您可以随时进行调整或清除。")
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

        title = QLabel("控制导出的截图品质")
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

        title = QLabel("配置 AI 截图翻译能力")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(title)

        desc = QLabel("在此输入供应商提供的 base_url、API Key 和模型名称，并可点击“测试”验证是否支持图片识别。")
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #4a4a4a;")
        layout.addWidget(desc)

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
        QApplication.clipboard().setText(self.translation_edit.toPlainText())

    def _copy_original(self):
        QApplication.clipboard().setText(self.original_edit.toPlainText())

    def _copy_combined(self):
        original = self.original_edit.toPlainText()
        translation = self.translation_edit.toPlainText()
        combined = original or ""
        if translation:
            combined = f"{original} [{translation}]"
        QApplication.clipboard().setText(combined)


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
        if not AITranslationService.is_configured(self._ai_settings):
            self.status_label.setText("请先在系统设置 -> AI 设置中填写 base_url/API Key/模型。")
            return
        try:
            image_bytes = pixmap_to_png_bytes(pixmap)
        except Exception as exc:
            self.status_label.setText(f"图片处理失败：{exc}")
            return
        self._tab_counter += 1
        tab = TranslationTab(self._tab_counter)
        title = f"{self._tab_counter}. 识别中"
        self.tabs.addTab(tab, title)
        self.tabs.setCurrentWidget(tab)
        self.status_label.setText("AI 正在识别并翻译截图...")
        worker = AITranslationWorker(image_bytes, self._ai_settings)
        worker.completed.connect(lambda original, translation, tab=tab: self._on_translation_success(tab, original, translation))
        worker.failed.connect(lambda message, tab=tab: self._on_translation_failure(tab, message))
        worker.finished.connect(lambda w=worker: self._cleanup_worker(w))
        self._workers.append(worker)
        worker.start()

    def _on_translation_success(self, tab, original, translation):
        tab.set_texts(original, translation)
        idx = self.tabs.indexOf(tab)
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.tabs.setTabText(idx, f"{idx + 1}. {timestamp}")
        if translation:
            QApplication.clipboard().setText(translation)
            self.status_label.setText("识别完成，译文已复制到剪贴板。")
        else:
            self.status_label.setText("识别完成，请手动复制需要的内容。")
        self.translationCompleted.emit()

    def _on_translation_failure(self, tab, message):
        idx = self.tabs.indexOf(tab)
        if idx >= 0:
            self.tabs.setTabText(idx, f"{idx + 1}. 失败")
        self.status_label.setText(f"AI 识别失败：{message}")

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

    def __init__(self, pixmap: QPixmap):
        super().__init__()
        self.base_pixmap = pixmap
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
        self.dragging_marker_index = None
        self.selected_marker_index = None
        self.hover_marker_index = None
        self.markers_flattened = True
        self.rectangle_fill_color = QColor(DEFAULT_RECT_STYLE["fill"])
        self.rectangle_border_color = QColor(DEFAULT_RECT_STYLE["border"])
        self.rectangle_border_enabled = DEFAULT_RECT_STYLE["border_enabled"]
        self.rectangle_border_width = DEFAULT_RECT_STYLE["width"]
        self.rectangle_corner_radius = DEFAULT_RECT_STYLE["radius"]
        self.rectangles_flattened = True
        self.selected_rectangle_index = None
        self.rect_drag_mode = None
        self.rect_drag_handle = None
        self.rect_initial_rect = QRect()
        self.rect_drag_origin = QPoint()
        self.creating_new_rect = False
        self._marker_dragging = False
        self._apply_zoom()

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
        return QSize(
            max(1, int(round(self.base_pixmap.width() * self._zoom))),
            max(1, int(round(self.base_pixmap.height() * self._zoom))),
        )

    def _apply_zoom(self):
        size = self._scaled_size()
        self.setFixedSize(size)
        self.update()

    def _view_to_scene(self, point: QPoint):
        if self._zoom == 0:
            return QPoint(point)
        return QPoint(
            int(round(point.x() / self._zoom)),
            int(round(point.y() / self._zoom)),
        )

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
        self._update_default_cursor()

    def clear_annotations(self):
        self.rectangles.clear()
        self.markers.clear()
        self.next_marker_number = 1
        self.markers_flattened = True
        self.rectangles_flattened = True
        self.dragging_marker_index = None
        self.selected_marker_index = None
        self.hover_marker_index = None
        self.selected_rectangle_index = None
        self._reset_rect_drag()
        self._marker_dragging = False
        self.update()
        self.optionsUpdated.emit()

    def _has_active_marker(self):
        return (
            self.selected_marker_index is not None
            and not self.markers_flattened
            and 0 <= self.selected_marker_index < len(self.markers)
        )

    def set_marker_color(self, color: QColor):
        if color.isValid():
            self.marker_fill_color = color
            if self._has_active_marker():
                self.markers[self.selected_marker_index]['fill'] = QColor(color)
            self.update()
            self.optionsUpdated.emit()

    def set_marker_size(self, size: int):
        self.marker_size = max(10, min(120, size))
        if self._has_active_marker():
            self.markers[self.selected_marker_index]['size'] = self.marker_size
        self.update()
        self.optionsUpdated.emit()

    def set_next_marker_number(self, number: int):
        self.next_marker_number = max(1, number)
        self.optionsUpdated.emit()

    def set_current_marker_number(self, number: int):
        if self._has_active_marker():
            self.markers[self.selected_marker_index]['number'] = max(1, number)
            self.update()
            self.optionsUpdated.emit()

    def set_marker_border_enabled(self, enabled: bool):
        self.marker_border_enabled = enabled
        if self._has_active_marker():
            self.markers[self.selected_marker_index]['border_enabled'] = enabled
        self.update()
        self.optionsUpdated.emit()

    def set_marker_border_color(self, color: QColor):
        if color.isValid():
            self.marker_border_color = color
            if self._has_active_marker():
                self.markers[self.selected_marker_index]['border_color'] = QColor(color)
            self.update()
            self.optionsUpdated.emit()

    def set_marker_font_ratio(self, ratio: float):
        self.marker_font_ratio = max(0.3, min(1.2, ratio))
        if self._has_active_marker():
            self.markers[self.selected_marker_index]['font_ratio'] = self.marker_font_ratio
        self.update()
        self.optionsUpdated.emit()

    def flatten_markers(self):
        self.dragging_marker_index = None
        self.markers_flattened = True
        self.selected_marker_index = None
        self.hover_marker_index = None
        self.next_marker_number = 1
        self.optionsUpdated.emit()

    def duplicate_marker(self):
        if not self._has_active_marker():
            return False
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
            self.rectangle_fill_color = color
            if self._has_active_rectangle():
                self.rectangles[self.selected_rectangle_index]['fill'] = QColor(color)
            self.update()
            self.optionsUpdated.emit()

    def set_rectangle_border_color(self, color: QColor):
        if color.isValid():
            self.rectangle_border_color = color
            if self._has_active_rectangle():
                self.rectangles[self.selected_rectangle_index]['border'] = QColor(color)
            self.update()
            self.optionsUpdated.emit()

    def set_rectangle_border_width(self, width: int):
        self.rectangle_border_width = max(1, min(20, width))
        if self._has_active_rectangle():
            self.rectangles[self.selected_rectangle_index]['width'] = self.rectangle_border_width
        self.update()
        self.optionsUpdated.emit()

    def set_rectangle_corner_radius(self, radius: int):
        self.rectangle_corner_radius = max(0, min(60, radius))
        if self._has_active_rectangle():
            self.rectangles[self.selected_rectangle_index]['radius'] = self.rectangle_corner_radius
        self.update()
        self.optionsUpdated.emit()

    def set_rectangle_border_enabled(self, enabled: bool):
        self.rectangle_border_enabled = enabled
        if self._has_active_rectangle():
            self.rectangles[self.selected_rectangle_index]['border_enabled'] = enabled
        self.update()
        self.optionsUpdated.emit()

    def flatten_rectangle(self):
        if self._has_active_rectangle():
            self.rectangles[self.selected_rectangle_index]['flattened'] = True
            self.selected_rectangle_index = None
            if all(r['flattened'] for r in self.rectangles):
                self.rectangles_flattened = True
            self.update()
            self.optionsUpdated.emit()

    def duplicate_rectangle(self):
        if not self._has_active_rectangle():
            return False
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
        return False

    def apply_style_defaults(self, marker_style, rect_style):
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

    def active_selection_kind(self):
        if self._has_active_marker():
            return "marker"
        if self._has_active_rectangle():
            return "rectangle"
        return "none"

    def clear_active_selection(self, emit=True):
        changed = False
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
        if changed:
            self._update_default_cursor()
            if emit:
                self.optionsUpdated.emit()
        self.update()
        return changed

    def delete_selected_shape(self):
        if self._has_active_marker():
            self.markers.pop(self.selected_marker_index)
            self.selected_marker_index = None
            self.dragging_marker_index = None
            self._set_hover_marker(None)
            self.update()
            self.optionsUpdated.emit()
            return True
        if self._has_active_rectangle():
            self.rectangles.pop(self.selected_rectangle_index)
            self.selected_rectangle_index = None
            self.rect_drag_mode = None
            self.rect_drag_handle = None
            self.update()
            self.optionsUpdated.emit()
            self._update_default_cursor()
            return True
        return False

    def flatten_all_annotations(self):
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
        if self.markers and not self.markers_flattened:
            self.markers.pop()
            self.selected_marker_index = None
            self.update()
            self.optionsUpdated.emit()
            return True
        if self.rectangles and not self.rectangles_flattened:
            self.rectangles.pop()
            self.selected_rectangle_index = None
            self.update()
            self.optionsUpdated.emit()
            return True
        return False

    def _reset_rect_drag(self):
        self.rect_drag_mode = None
        self.rect_drag_handle = None
        self.rect_initial_rect = QRect()
        self.rect_drag_origin = QPoint()
        self.creating_new_rect = False

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        pos = self._view_to_scene(event.pos())
        if self._handle_rect_press(pos, allow_creation=False, handles_only=True):
            return
        if self._handle_marker_press(pos, allow_creation=self.tool == Tool.MARKER):
            return
        if self._handle_rect_press(pos, allow_creation=self.tool == Tool.RECTANGLE):
            return

    def mouseMoveEvent(self, event):
        pos = self._view_to_scene(event.pos())
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
                rect = self._resize_rect(self.rect_initial_rect, self.rect_drag_handle, delta, event.modifiers())
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
        self._update_pointer_feedback(pos)

    def _handle_marker_press(self, pos: QPoint, allow_creation=True):
        idx = self._marker_hit_test(pos)
        if idx is not None and not self.markers_flattened:
            self.dragging_marker_index = idx
            self.selected_marker_index = idx
            self.selected_rectangle_index = None
            self.rect_drag_mode = None
            self._set_hover_marker(None)
            self._begin_marker_drag()
            self.optionsUpdated.emit()
            self.update()
            return True
        if allow_creation:
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

    def _begin_marker_drag(self):
        if not self._marker_dragging:
            self._marker_dragging = True
            self.setCursor(Qt.BlankCursor)

    def _end_marker_drag(self):
        if self._marker_dragging:
            self._marker_dragging = False
            view_pos = self.mapFromGlobal(QCursor.pos())
            self._update_pointer_feedback(self._view_to_scene(view_pos))

    def _handle_rect_press(self, pos: QPoint, allow_creation=True, handles_only=False):
        idx, handle = self._rect_handle_hit_test(pos)
        if idx is not None:
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
            self.selected_rectangle_index = idx
            self.selected_marker_index = None
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
        self.dragging_marker_index = None
        self.rect_drag_mode = 'resize'
        self.rect_drag_handle = 'bottom-right'
        self.rect_initial_rect = QRect(rect_info['rect'])
        self.rect_drag_origin = QPoint(pos)
        self.rectangles_flattened = False
        self.creating_new_rect = True
        self._set_hover_marker(None)
        self.optionsUpdated.emit()
        self._update_cursor(Qt.SizeFDiagCursor)
        return True

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.scale(self._zoom, self._zoom)
        painter.drawPixmap(0, 0, self.base_pixmap)
        for idx, info in enumerate(self.rectangles):
            painter.setBrush(info['fill'])
            if info['border_enabled']:
                painter.setPen(QPen(info['border'], info['width']))
            else:
                painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(info['rect'], info['radius'], info['radius'])
        painter.setPen(Qt.NoPen)
        font = QFont()
        font.setBold(True)
        for idx, marker in enumerate(self.markers):
            radius = marker['size']
            font.setPixelSize(int(radius * marker['font_ratio']))
            painter.setFont(font)
            ellipse_rect = QRect(marker['pos'].x() - radius, marker['pos'].y() - radius, radius * 2, radius * 2)
            painter.setBrush(marker['fill'])
            painter.drawEllipse(ellipse_rect)
            if marker['border_enabled']:
                painter.setPen(QPen(marker['border_color'], max(2, radius * 0.2)))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(ellipse_rect)
                painter.setPen(Qt.NoPen)
            painter.setBrush(marker['fill'])
            painter.setPen(Qt.white)
            painter.drawText(ellipse_rect, Qt.AlignCenter, str(marker['number']))
            painter.setPen(Qt.NoPen)
            should_draw = (
                not self.markers_flattened
                and self.dragging_marker_index is None
                and idx == self.selected_marker_index
            )
            if should_draw:
                glow_rect = ellipse_rect.adjusted(-int(radius * 0.2), -int(radius * 0.2), int(radius * 0.2), int(radius * 0.2))
                color = QColor(marker['fill'])
                color.setAlpha(120)
                painter.setPen(Qt.NoPen)
                painter.setBrush(color)
                painter.drawEllipse(glow_rect)
                painter.setPen(Qt.NoPen)
                painter.setBrush(marker['fill'])

    def export_pixmap(self):
        annotated = QPixmap(self.base_pixmap.size())
        annotated.fill(Qt.transparent)
        painter = QPainter(annotated)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.drawPixmap(0, 0, self.base_pixmap)
        for info in self.rectangles:
            painter.setBrush(info['fill'])
            if info['border_enabled']:
                painter.setPen(QPen(info['border'], info['width']))
            else:
                painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(info['rect'], info['radius'], info['radius'])
        painter.setPen(Qt.NoPen)
        font = QFont()
        font.setBold(True)
        for marker in self.markers:
            radius = marker['size']
            font.setPixelSize(int(radius * marker['font_ratio']))
            painter.setFont(font)
            ellipse_rect = QRect(marker['pos'].x() - radius, marker['pos'].y() - radius, radius * 2, radius * 2)
            painter.setBrush(marker['fill'])
            painter.drawEllipse(ellipse_rect)
            if marker['border_enabled']:
                painter.setPen(QPen(marker['border_color'], max(2, radius * 0.2)))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(ellipse_rect)
                painter.setPen(Qt.NoPen)
            painter.setBrush(marker['fill'])
            painter.setPen(Qt.white)
            painter.drawText(ellipse_rect, Qt.AlignCenter, str(marker['number']))
            painter.setPen(Qt.NoPen)
        painter.end()
        return annotated

    def _marker_hit_test(self, pos: QPoint):
        for idx, marker in enumerate(self.markers):
            radius = marker['size']
            ellipse_rect = QRect(marker['pos'].x() - radius, marker['pos'].y() - radius, radius * 2, radius * 2)
            if ellipse_rect.contains(pos):
                return idx
        return None

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

    def _rect_handle_hit_test(self, pos: QPoint):
        handles = ['top-left', 'top-right', 'bottom-left', 'bottom-right']
        for idx in reversed(range(len(self.rectangles))):
            info = self.rectangles[idx]
            if info['flattened']:
                continue
            for handle_name, handle_rect in zip(handles, self._handle_rects(info['rect'])):
                if handle_rect.contains(pos):
                    return idx, handle_name
        return None, None

    def _resize_rect(self, initial_rect: QRect, handle: str, delta: QPoint, modifiers):
        rect = QRect(initial_rect)
        dx, dy = delta.x(), delta.y()
        if handle == 'top-left':
            rect.setTopLeft(rect.topLeft() + delta)
        elif handle == 'top-right':
            rect.setTopRight(rect.topRight() + QPoint(dx, dy))
        elif handle == 'bottom-left':
            rect.setBottomLeft(rect.bottomLeft() + QPoint(dx, dy))
        else:
            rect.setBottomRight(rect.bottomRight() + delta)
        if modifiers & Qt.ControlModifier:
            width = rect.width()
            height = rect.height()
            size = min(abs(width), abs(height))
            if width < 0:
                rect.setLeft(rect.right() - size)
            else:
                rect.setRight(rect.left() + size)
            if height < 0:
                rect.setTop(rect.bottom() - size)
            else:
                rect.setBottom(rect.top() + size)
        return rect

    def _set_hover_marker(self, idx):
        if self.hover_marker_index != idx:
            self.hover_marker_index = idx
            self.update()

    def _update_pointer_feedback(self, pos: QPoint):
        if self._marker_dragging:
            return
        allow_rect_cursor = self.tool != Tool.MARKER
        if allow_rect_cursor:
            idx, handle = self._rect_handle_hit_test(pos)
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

    def _update_default_cursor(self):
        if self.tool == Tool.MARKER:
            self._update_cursor(Qt.CrossCursor)
        else:
            self._update_cursor(Qt.ArrowCursor)

    def _update_cursor(self, cursor_shape):
        self.setCursor(cursor_shape)

class AnnotationTab(QWidget):
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
        self.canvas.apply_style_defaults(style_state.get("marker"), style_state.get("rectangle"))
        self.save_dir = save_dir
        if source_path:
            self.auto_saved_path = source_path
            self._external_source = True
        else:
            self.auto_saved_path = self._auto_save_pixmap(pixmap)
            self._external_source = False
        self.style_state = style_state
        self.style_callback = style_callback
        self._current_tool = Tool.NONE
        self.base_status_text = self._default_base_status_text()
        self.dirty = False
        layout = QVBoxLayout()

        toolbar = QToolBar("工具栏")
        toolbar.setObjectName("AnnotationToolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(0, 0))
        toolbar.setStyleSheet(
            """
            QToolBar#AnnotationToolbar {
                border: none;
                padding: 0;
                margin-bottom: 2px;
            }
            QToolBar#AnnotationToolbar QToolButton {
                border-radius: 6px;
                padding: 2px 10px;
                font-weight: 600;
                background: rgba(14,19,37,0.08);
                color: #1b2130;
                margin-right: 2px;
                min-height: 24px;
            }
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
            """
        )

        rect_action = QAction("标注框", self)
        rect_action.setCheckable(True)
        rect_action.triggered.connect(lambda: self._set_tool(Tool.RECTANGLE))
        toolbar.addAction(rect_action)
        rect_button = toolbar.widgetForAction(rect_action)
        if rect_button:
            rect_button.setObjectName("Tool_rect")

        marker_action = QAction("顺序标记", self)
        marker_action.setCheckable(True)
        marker_action.triggered.connect(lambda: self._set_tool(Tool.MARKER))
        toolbar.addAction(marker_action)
        marker_button = toolbar.widgetForAction(marker_action)
        if marker_button:
            marker_button.setObjectName("Tool_marker")

        clear_action = QAction("清除标注", self)
        clear_action.triggered.connect(self.canvas.clear_annotations)
        toolbar.addAction(clear_action)

        delete_action = QAction("删除选中", self)
        delete_action.triggered.connect(self._delete_selected)
        toolbar.addAction(delete_action)

        duplicate_action = QAction("复制当前", self)
        duplicate_action.triggered.connect(self._duplicate_active_shape)
        toolbar.addAction(duplicate_action)

        flatten_action = QAction("平化", self)
        flatten_action.triggered.connect(self.canvas.flatten_markers)
        toolbar.addAction(flatten_action)

        save_action = QAction("保存标注图", self)
        save_action.triggered.connect(self.save_annotated_image)
        toolbar.addAction(save_action)

        self._tool_actions = {Tool.RECTANGLE: rect_action, Tool.MARKER: marker_action}
        layout.addWidget(toolbar)

        self.marker_panel = MarkerOptionsPanel(self.canvas)
        self.rectangle_panel = RectangleOptionsPanel(self.canvas)
        self._options_placeholder = QWidget()
        self._options_placeholder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.panel_stack = QStackedWidget()
        self.panel_stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.panel_stack.addWidget(self._options_placeholder)
        self.panel_stack.addWidget(self.marker_panel)
        self.panel_stack.addWidget(self.rectangle_panel)

        stack_height = max(self.marker_panel.sizeHint().height(), self.rectangle_panel.sizeHint().height())
        self.panel_stack.setFixedHeight(stack_height)
        self._options_placeholder.setFixedHeight(stack_height)
        self.marker_panel.setMinimumHeight(stack_height)
        self.rectangle_panel.setMinimumHeight(stack_height)

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

    def _clamp_quality(self, value):
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = DEFAULT_IMAGE_QUALITY
        return max(10, min(100, value))

    def set_image_quality(self, value):
        self.image_quality = self._clamp_quality(value)

    def _auto_save_pixmap(self, pixmap: QPixmap):
        os.makedirs(self.save_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"screenshot_{timestamp}.jpg"
        path = os.path.join(self.save_dir, filename)
        if self.auto_save_enabled:
            pixmap.save(path, "JPG", self.image_quality)
        return path

    def save_annotated_image(self):
        if self.canvas.markers and not self.canvas.markers_flattened:
            self.canvas.flatten_all_annotations()
        annotated = self.canvas.export_pixmap()
        base, _ = os.path.splitext(os.path.basename(self.auto_saved_path))
        annotated_path = os.path.join(self.save_dir, f"{base}_annotated.jpg")
        if annotated.save(annotated_path, "JPG", self.image_quality):
            self.status_label.setText(f"标注图已保存: {annotated_path}")
            self._set_dirty(False)
            return True
        QMessageBox.warning(self, "保存失败", "无法写入标注截图，请检查保存路径。")
        return False

    def _set_tool(self, tool: Tool):
        self._current_tool = tool
        self.canvas.clear_active_selection(emit=False)
        self.canvas.set_tool(tool)
        self._sync_tool_action_checks(tool)
        self._update_panel_visibility(preferred=tool)

    def _handle_canvas_update(self):
        self._mark_dirty()
        self._update_panel_visibility()
    
    def _handle_escape(self):
        if self._current_tool in (Tool.MARKER,):
            self._set_tool(Tool.NONE)

    def _on_zoom_changed(self, factor):
        if hasattr(self, "zoom_label"):
            percent = int(round(factor * 100))
            self.zoom_label.setText(f"{percent}%")

    def _update_panel_visibility(self, preferred=None):
        kind = self.canvas.active_selection_kind()
        if kind == "marker":
            target = Tool.MARKER
        elif kind == "rectangle":
            target = Tool.RECTANGLE
        else:
            target = preferred or self._current_tool
        if target == Tool.MARKER:
            self.panel_stack.setCurrentWidget(self.marker_panel)
            self._set_panel_active_state(marker=True)
        elif target == Tool.RECTANGLE:
            self.panel_stack.setCurrentWidget(self.rectangle_panel)
            self._set_panel_active_state(rectangle=True)
        else:
            self.panel_stack.setCurrentWidget(self._options_placeholder)
            self._set_panel_active_state()
        tracking = target if target in (Tool.MARKER, Tool.RECTANGLE) else Tool.NONE
        self._sync_tool_action_checks(tracking)

    def _set_panel_active_state(self, marker=False, rectangle=False):
        if hasattr(self.marker_panel, "set_panel_active"):
            self.marker_panel.set_panel_active(bool(marker))
        if hasattr(self.rectangle_panel, "set_panel_active"):
            self.rectangle_panel.set_panel_active(bool(rectangle))

    def _sync_tool_action_checks(self, active_tool: Tool):
        actions = getattr(self, "_tool_actions", {})
        for tool, action in actions.items():
            action.blockSignals(True)
            action.setChecked(tool == active_tool)
            action.blockSignals(False)

    def _duplicate_active_shape(self):
        if not self.canvas.duplicate_active_shape():
            QApplication.beep()

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
            self.auto_saved_path = self._auto_save_pixmap(self.canvas.base_pixmap)
        self.base_status_text = self._default_base_status_text()
        if not self.dirty:
            self.status_label.setText(self.base_status_text)

    def _default_base_status_text(self):
        if self._external_source:
            return f"原始文件: {self.auto_saved_path}"
        if self.auto_save_enabled:
            return f"自动保存: {self.auto_saved_path}"
        return f"尚未保存: {self.auto_saved_path}"


    def _undo_last_action(self):
        if self.canvas.undo_last_shape():
            self.status_label.setText("已撤销上一次操作")
            self._mark_dirty()
        else:
            QMessageBox.information(self, "无法撤销", "当前没有可撤销的操作。")

    def _copy_to_clipboard(self):
        self.canvas.flatten_all_annotations()
        pix = self.canvas.export_pixmap()
        QApplication.clipboard().setPixmap(pix)
        self.status_label.setText("已平化并复制到剪贴板")
        self._mark_dirty()

    def _delete_selected(self):
        if self.canvas.delete_selected_shape():
            self.status_label.setText("已删除当前选择")
            self._mark_dirty()
        else:
            QApplication.beep()

    def _persist_style_defaults(self):
        if not self.style_callback:
            return
        marker_style = self.canvas.marker_style_state()
        rect_style = self.canvas.rectangle_style_state()
        self.style_callback("marker", marker_style)
        self.style_callback("rectangle", rect_style)

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
        layout = QVBoxLayout()

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        layout.addWidget(self.tabs, 1)

        hint = QLabel("尚未添加截图，使用区域截取或重复截取后会在此显示。")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("color: #777777;")
        self._empty_hint = hint
        layout.addWidget(hint)
        self.setLayout(layout)

        self._update_hint_visibility()

    def _update_hint_visibility(self):
        has_tabs = self.tabs.count() > 0
        self._empty_hint.setVisible(not has_tabs)
        self.tabs.setVisible(has_tabs)

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
        self.tabs.removeTab(index)
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
        label = os.path.basename(label_path)
        tab._base_label = label
        self.tabs.addTab(tab, label)
        self._bind_tab_signals(tab)
        self.tabs.setCurrentWidget(tab)
        self._update_hint_visibility()

    def _bind_tab_signals(self, tab):
        tab.dirtyStateChanged.connect(lambda dirty, t=tab: self._update_tab_color(t, dirty))
        self._update_tab_color(tab, tab.dirty)
        if hasattr(tab, "canvas"):
            tab.canvas.zoomChanged.connect(lambda factor, t=tab: self._handle_tab_zoom(factor, t))

    def _update_tab_color(self, tab, dirty):
        index = self.tabs.indexOf(tab)
        if index == -1:
            return
        color = QColor("#f97316") if dirty else QColor("#0f172a")
        self.tabs.tabBar().setTabTextColor(index, color)
        base_label = getattr(tab, "_base_label", self.tabs.tabText(index).lstrip("* ").strip())
        prefix = "* " if dirty else ""
        self.tabs.setTabText(index, f"{prefix}{base_label}")

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
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)
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
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setInterval(16)
        self._cursor_timer.timeout.connect(self._sync_cursor_position)
        self._cursor_timer.start()
        self._sync_cursor_position(force=True)
        self._scale_x = self._compute_scale(self.screenshot.width(), self.width())
        self._scale_y = self._compute_scale(self.screenshot.height(), self.height())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(self.rect(), self.screenshot)

        overlay_color = QColor(0, 0, 0, 120)
        painter.fillRect(self.rect(), overlay_color)

        if self.selection:
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.fillRect(self.selection, Qt.transparent)
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
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
        src_half = 16
        size = QSize(src_half * 2, src_half * 2)
        x = max(src_half, min(self.cursor_pos.x(), self.width() - src_half - 1))
        y = max(src_half, min(self.cursor_pos.y(), self.height() - src_half - 1))
        logical_rect = QRect(QPoint(x - src_half, y - src_half), size)
        source_rect = self._device_rect(logical_rect)
        snippet = self.screenshot.copy(source_rect)
        zoom = 5
        dest_size = QSize(size.width() * zoom, size.height() * zoom)
        magnified = snippet.scaled(dest_size, Qt.KeepAspectRatio, Qt.FastTransformation)

        margin = 20
        dest_top_left = QPoint(self.cursor_pos.x() + margin, self.cursor_pos.y() + margin)
        if dest_top_left.x() + dest_size.width() > self.width():
            dest_top_left.setX(self.cursor_pos.x() - margin - dest_size.width())
        if dest_top_left.y() + dest_size.height() > self.height():
            dest_top_left.setY(self.cursor_pos.y() - margin - dest_size.height())
        dest_rect = QRect(dest_top_left, dest_size)

        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.fillRect(dest_rect.adjusted(-4, -4, 4, 4), QColor(0, 0, 0, 180))
        painter.drawPixmap(dest_rect, magnified)
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.drawRect(dest_rect)

        center_x = dest_rect.center().x()
        center_y = dest_rect.center().y()
        painter.setPen(QPen(QColor(255, 100, 100), 1, Qt.DashLine))
        painter.drawLine(center_x, dest_rect.top(), center_x, dest_rect.bottom())
        painter.drawLine(dest_rect.left(), center_y, dest_rect.right(), center_y)

    def _device_rect(self, logical_rect: QRect):
        if logical_rect is None:
            return QRect()
        x = int(round(logical_rect.x() * self._scale_x))
        y = int(round(logical_rect.y() * self._scale_y))
        w = max(1, int(round(logical_rect.width() * self._scale_x)))
        h = max(1, int(round(logical_rect.height() * self._scale_y)))
        rect = QRect(x, y, w, h)
        return self._clamp_to_pixmap(rect)

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

        self.workspace_page = AnnotationWorkspacePage(
            lambda: self._open_settings_dialog(),
            self._open_images_dialog,
            {"marker": self.marker_style, "rectangle": self.rectangle_style},
            self._on_style_changed,
            self._image_quality,
            self.auto_save_enabled,
            default_zoom=self.workspace_zoom,
            zoom_changed_callback=self._on_workspace_zoom_changed,
        )
        self._hotkey_manager = GlobalHotkeyManager(self)
        self._last_selection_rect = None
        self._save_dir = (self.config.get("save_dir") or "").strip()
        self._force_exit_once = False
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

    def _switch_page(self, key):
        if key not in self._pages:
            return
        self.pages.setCurrentWidget(self._pages[key])
        self._current_page = key
        self._update_nav_state()

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
        os.makedirs(target_dir, exist_ok=True)
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
        self.close()

    def _prepare_save_dir(self):
        save_dir = self._resolved_save_dir()
        os.makedirs(save_dir, exist_ok=True)
        if self._save_dir:
            self.config["save_dir"] = self._save_dir
            save_config(self.config)
        return save_dir

    def _start_capture_session(self, handler):
        self._pending_capture_handler = handler
        self.hide()
        QTimer.singleShot(200, self._start_overlay_capture)

    def initiate_capture(self):
        self._prepare_save_dir()
        self._start_capture_session(self._handle_annotation_capture)

    def start_ai_translation_capture(self):
        if not self._ensure_ai_ready():
            return
        self._prepare_save_dir()
        self._start_capture_session(self._handle_ai_translation_capture)

    def _start_overlay_capture(self):
        screens = QGuiApplication.screens()
        if not screens:
            QMessageBox.critical(self, "错误", "未找到可用的屏幕设备，无法截图。")
            self.show()
            return
        self._clear_overlays()
        for screen in screens:
            self._create_overlay_for_screen(screen)

    def _on_capture_cancel(self):
        self._clear_overlays()
        self._pending_capture_handler = None
        self._show_main_window()

    def _on_overlay_selection(self, pixmap: QPixmap, selection_rect: QRect, screen_name: str):
        self._clear_overlays()
        handler = self._pending_capture_handler or self._handle_annotation_capture
        self._pending_capture_handler = None
        handler(pixmap, selection_rect, screen_name)

    def _handle_annotation_capture(self, pixmap: QPixmap, selection_rect: QRect, screen_name: str):
        self._last_selection_rect = QRect(selection_rect)
        self._last_capture_screen_name = screen_name
        self.workspace_page.add_capture(pixmap, self._resolved_save_dir(), self.workspace_zoom)
        self.home_page.set_repeat_enabled(True)
        self._focus_workspace()
        self._resize_for_image(pixmap.size())
        QApplication.clipboard().setPixmap(pixmap)

    def _handle_ai_translation_capture(self, pixmap: QPixmap, selection_rect: QRect, screen_name: str):
        self._last_selection_rect = QRect(selection_rect)
        self._last_capture_screen_name = screen_name
        self.home_page.set_repeat_enabled(True)
        self._switch_page("ai")
        self._show_main_window()
        self.translation_panel.add_capture(pixmap)

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
        save_config(self.config)

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
            save_config(self.config)
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
        save_config(self.config)

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
        if action_id == "capture":
            self.initiate_capture()
        elif action_id == "repeat_capture":
            self._repeat_capture()
        elif action_id == "ai_translate":
            self.start_ai_translation_capture()

    def _on_hotkey_trigger(self, action_id):
        if self._active_overlays:
            return
        QTimer.singleShot(0, lambda: self._trigger_hotkey_action(action_id))

    def nativeEvent(self, eventType, message):
        if eventType in ("windows_generic_MSG", "windows_dispatcher_MSG"):
            msg = wintypes.MSG.from_address(message.__int__())
            if msg.message == WM_HOTKEY:
                self._hotkey_manager.handle_message(msg.wParam)
                return True, 0
        return super().nativeEvent(eventType, message)

    def _repeat_capture(self):
        if not self._last_selection_rect:
            QMessageBox.information(self, "重复截图", "请先进行一次截图，随后才能使用重复截图功能。")
            return
        self.hide()
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
        QApplication.clipboard().setPixmap(cropped)

    def closeEvent(self, event):
        behavior = self.close_behavior
        tray_available = bool(self.tray_icon and QSystemTrayIcon.isSystemTrayAvailable())
        if behavior == "tray" and not tray_available:
            behavior = "exit"
        if self._closing_via_tray_exit:
            behavior = "exit"
        if getattr(self, "_force_exit_once", False):
            behavior = "exit"
        if behavior == "tray":
            event.ignore()
            self._minimize_to_tray()
            return
        if not self._handle_unsaved_before_exit():
            event.ignore()
            self._closing_via_tray_exit = False
            self._force_exit_once = False
            return
        self._cleanup_before_exit()
        self._closing_via_tray_exit = False
        self._force_exit_once = False
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
        self.close()

    def _cleanup_before_exit(self):
        self._clear_overlays()
        self._teardown_hotkeys()
        if self.tray_icon:
            self.tray_icon.hide()
        self.tray_icon = None

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

    def _create_overlay_for_screen(self, screen):
        screenshot = self._grab_screen_pixmap(screen)
        overlay = CaptureOverlay(screenshot, screen.geometry().topLeft(), screen)
        overlay.selectionMade.connect(self._on_overlay_selection)
        overlay.canceled.connect(self._on_capture_cancel)
        overlay.show()
        self._active_overlays.append(overlay)

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
    font = QFont("Microsoft YaHei", 10, QFont.Light)
    app.setFont(font)
    app.setWindowIcon(get_app_icon())
    window = ScreenSnapApp(start_minimized=start_minimized)
    if not start_minimized:
        window.show()
    try:
        sys.exit(app.exec_())
    finally:
        guard.release()


if __name__ == "__main__":
    main()
