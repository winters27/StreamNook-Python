import json
import os
import sys
import shlex
import time
import subprocess
import re
import random
import secrets
import base64
import hashlib
import webbrowser
import http.server
import socket
import threading
import atexit
from pathlib import Path
from functools import lru_cache
from contextlib import contextmanager
from toast import ToastManager, LoadingToast

import psutil
import ctypes
from ctypes import wintypes
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtWidgets import QScrollArea
from PySide6.QtGui import QWindow, QFontDatabase, QMovie
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
import urllib.request
import urllib.parse
import urllib.error

try:
    import shiboken6
except Exception:
    shiboken6 = None

from discord_game_matcher import resolve_discord_game_image
from discord_presence import DiscordPresenceClient


# ============================================================================
# Thread Pool Management
# ============================================================================
class ManagedThreadPool:
    """Context manager for thread pools to ensure proper cleanup"""
    def __init__(self, max_workers, name="ThreadPool"):
        self.pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=name)
        self.name = name

    def submit(self, fn, *args, **kwargs):
        return self.pool.submit(fn, *args, **kwargs)

    def shutdown(self, wait=True):
        """Gracefully shutdown the thread pool"""
        try:
            self.pool.shutdown(wait=wait)
        except Exception as e:
            print(f"Error shutting down {self.name}: {e}")


# Initialize thread pools
TWITCH_LOGIN_POOL = ManagedThreadPool(max_workers=1, name="TwitchLogin")
IMAGE_LOAD_POOL = ManagedThreadPool(max_workers=4, name="ImageLoad")
EMOTE_LOAD_POOL = ManagedThreadPool(max_workers=2, name="EmoteLoad")
LIVE_STREAM_LOAD_POOL = ManagedThreadPool(max_workers=1, name="LiveStream")

# Register cleanup on exit
def cleanup_thread_pools():
    """Cleanup all thread pools on application exit"""
    for pool in [TWITCH_LOGIN_POOL, IMAGE_LOAD_POOL, EMOTE_LOAD_POOL, LIVE_STREAM_LOAD_POOL]:
        pool.shutdown(wait=True)

atexit.register(cleanup_thread_pools)


# ============================================================================
# Path helpers - Cached for performance
# ============================================================================
@lru_cache(maxsize=1)
def get_base_path() -> Path:
    """
    Determines the base path for the application, handling both script and PyInstaller executable formats.
    Cached to avoid repeated checks.
    """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return Path(sys._MEIPASS)
    return Path(__file__).parent


@lru_cache(maxsize=1)
def get_persistent_data_path() -> Path:
    """
    Determines the persistent application data path for StreamNook.
    Cached to avoid repeated environment variable lookups.
    """
    appdata = os.getenv("APPDATA")
    if appdata:
        return Path(appdata) / "StreamNook"
    return Path.home() / ".streamnook"


@lru_cache(maxsize=1)
def _default_mpv_config_dir() -> Path:
    """Cached MPV config directory path"""
    appdata = os.getenv("APPDATA")
    if appdata:
        return Path(appdata) / "mpv"
    return Path.home() / "AppData" / "Roaming" / "mpv"


# ============================================================================
# App storage
# ============================================================================
BASE_PATH = get_base_path()
ASSETS_DIR = BASE_PATH / "assets"

APP_DIR = get_persistent_data_path() if getattr(sys, 'frozen', False) else BASE_PATH / "data"
APP_DIR.mkdir(parents=True, exist_ok=True)

THUMBNAIL_CACHE_DIR = APP_DIR / "thumbnails"
THUMBNAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

SETTINGS_FILE = APP_DIR / "settings.json"


# ============================================================================
# Discord "detectable app" cache
# ============================================================================
_DETECTABLE_CACHE = APP_DIR / "discord_detectables_cache.json"
_CACHE_EXPIRY_SECONDS = 12 * 3600  # 12 hours


def _load_detectable_cache() -> dict:
    """Load Discord detectables cache with error handling"""
    try:
        if _DETECTABLE_CACHE.exists():
            data = json.loads(_DETECTABLE_CACHE.read_text("utf-8"))
            # Validate cache structure
            if isinstance(data, dict) and "fetched_at" in data and "apps" in data:
                return data
    except (json.JSONDecodeError, IOError) as e:
        print(f"Error loading detectable cache: {e}")
    return {"fetched_at": 0, "apps": []}


def _save_detectable_cache(cache: dict):
    """Save Discord detectables cache with error handling"""
    try:
        _DETECTABLE_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except IOError as e:
        print(f"Error saving detectable cache: {e}")


def _is_cache_valid(cache: dict) -> bool:
    """Check if cache is still valid"""
    age = time.time() - cache.get("fetched_at", 0)
    return age < _CACHE_EXPIRY_SECONDS and cache.get("apps")


def _fetch_detectables_from_discord(timeout=6) -> list[dict]:
    """
    Fetch the public 'detectable' applications list from Discord.
    Returns cached data if still valid, otherwise fetches fresh data.
    """
    cache = _load_detectable_cache()

    # Return cached data if valid
    if _is_cache_valid(cache):
        return cache["apps"]

    # Fetch fresh data
    try:
        req = urllib.request.Request(
            "https://discord.com/api/v9/applications/detectable",
            headers={"User-Agent": "StreamNook/1.0", "Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            apps = json.loads(r.read().decode("utf-8", "ignore")) or []

        # Update cache
        cache = {"fetched_at": int(time.time()), "apps": apps}
        _save_detectable_cache(cache)
        return apps
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"Error fetching detectables: {e}")
        return cache.get("apps", [])


# ============================================================================
# Helper functions
# ============================================================================
def _norm(s: str) -> str:
    """Normalize string for comparison"""
    return s.strip().casefold() if s else ""


# Content for the default mpv.conf file
DEFAULT_MPV_CONF_CONTENT = """# --- High-Quality Preset ---
# Use the built-in high-quality profile as a base
profile=high-quality

# --- Upscaling ---
# Use a high-quality, sharp upscaler. Good for 720p/1080p streams on a 1440p+ monitor.
scale=ewa_lanczossharp
cscale=ewa_lanczossharp

# --- Debanding / Artifact Reduction ---
# This is the key setting to reduce "blockiness" and color banding.
deband=yes

# --- Hardware Decoding ---
# Use D3D11 hardware decoding for best performance on Windows
hwdec=d3D11va
gpu-api=d3D11
"""

DEFAULTS = {
    "streamlink_path": r"C:\Program Files\Streamlink\bin\streamlinkw.exe",
    "mpv_path": r"C:\Program Files\mpv\mpv.exe",
    "player_args": "--force-window=immediate --keep-open=no --no-border --cache=yes --demuxer-max-bytes=150M --demuxer-max-back-bytes=75M --video-unscaled=no --keepaspect-window=yes --video-aspect-override=16:9 --osc --input-ipc-server=\\\\.\\pipe\\streamnook-mpv",
    "streamlink_args": "--twitch-proxy-playlist=https://lb-na.cdn-perfprod.com,https://eu.luminous.dev --twitch-proxy-playlist-fallback",
    "quality": "best",
    "chatterino_path": r"C:\Program Files\Chatterino\chatterino.exe",
    "mpv_title": "Stream Nook",
    "use_mpv_config": True,
    "mpv_config_dir": str(_default_mpv_config_dir()),
    "chat_placement": "right",
    "accounts": {"current": ""},
    "hide_search_bar_on_startup": True,
    "discord_rpc_enabled": True,
}

TWITCH_CLIENT_ID = "1qgws7yzcp21g5ledlzffw3lmqdvie"
TWITCH_SCOPES = ["user:read:follows", "user:read:email"]

# Default geometry constants
PLAYER_DEFAULT_W = 1080
PLAYER_DEFAULT_H = 608
CHAT_DEFAULT_W = 390
CHAT_DEFAULT_H = 320
SPLITTER_HANDLE_PX = 2


# ============================================================================
# Settings management
# ============================================================================
def load_settings() -> dict:
    """Load settings with proper error handling and validation"""
    try:
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Merge with defaults to ensure all keys exist
                return {**DEFAULTS, **data}
    except (json.JSONDecodeError, IOError) as e:
        print(f"Error loading settings: {e}")
    return DEFAULTS.copy()


def save_settings(data: dict):
    """Save settings with error handling"""
    try:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except IOError as e:
        print(f"Error saving settings: {e}")


def normalize_input(user_text: str) -> str:
    """Normalize user input for Twitch URLs"""
    t = user_text.strip() if user_text else ""
    if not t:
        return ""
    if "://" not in t:
        return f"https://twitch.tv/{t}"
    return t


# ============================================================================
# Win32 helpers
# ============================================================================
user32 = ctypes.windll.user32

# Function prototypes
EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
GetWindowThreadProcessId = user32.GetWindowThreadProcessId
GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
GetWindowThreadProcessId.restype = wintypes.DWORD

IsWindowVisible = user32.IsWindowVisible
GetWindowTextW = user32.GetWindowTextW
GetWindowTextLengthW = user32.GetWindowTextLengthW
SetParent = user32.SetParent
SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
SetParent.restype = wintypes.HWND

GetWindowLongW = user32.GetWindowLongW
SetWindowLongW = user32.SetWindowLongW

SetWindowPos = user32.SetWindowPos
SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
SetWindowPos.restype = ctypes.c_bool

RedrawWindow = user32.RedrawWindow
RedrawWindow.argtypes = [wintypes.HWND, wintypes.LPRECT, wintypes.HRGN, wintypes.UINT]
RedrawWindow.restype = wintypes.BOOL

# Styles / flags
GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_CHILD = 0x40000000
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_BORDER = 0x00000080
WS_CLIPCHILDREN = 0x02000000
WS_EX_CLIENTEDGE = 0x00000200
WS_EX_WINDOWEDGE = 0x00000100
WS_EX_NOACTIVATE = 0x08000000

# SetWindowPos flags
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040

# RedrawWindow flags
RDW_INVALIDATE = 0x0001
RDW_UPDATENOW = 0x0100
RDW_ALLCHILDREN = 0x0080

# WM_NCHITTEST return values
HTLEFT = 10
HTRIGHT = 11
HTTOP = 12
HTTOPLEFT = 13
HTTOPRIGHT = 14
HTBOTTOM = 15
HTBOTTOMLEFT = 16
HTBOTTOMRIGHT = 17
HTCAPTION = 2
HTCLIENT = 1


def find_main_window_for_pid(pid: int) -> int:
    """Return HWND (int) for the first visible top-level window belonging to pid."""
    result = []

    @EnumWindowsProc
    def _enum(hwnd, lParam):
        if not IsWindowVisible(hwnd):
            return True
        pid_out = wintypes.DWORD(0)
        GetWindowThreadProcessId(hwnd, ctypes.byref(pid_out))
        if pid_out.value == pid:
            result.append(hwnd)
            return False
        return True

    user32.EnumWindows(_enum, 0)
    return int(result[0]) if result else 0


def add_clip_children(hwnd_parent: int):
    """Helps prevent overdraw artifacts during resize by clipping child repaints."""
    try:
        style = GetWindowLongW(hwnd_parent, GWL_STYLE)
        if (style & WS_CLIPCHILDREN) == 0:
            SetWindowLongW(hwnd_parent, GWL_STYLE, style | WS_CLIPCHILDREN)
            SetWindowPos(
                hwnd_parent, 0, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
            )
    except Exception as e:
        print(f"Error adding clip children: {e}")


# ============================================================================
# Network helpers
# ============================================================================
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# Compiled regex for better performance
_EMOTE_SRC_RE = re.compile(
    r'(?:(?:https?:)?\/\/)cdn\.7tv\.app\/emote\/(?P<id>[A-Za-z0-9]+)\/3x\.avif',
    re.IGNORECASE
)


def _is_animated_url(url: str) -> bool:
    """Check if URL points to animated content"""
    return ".gif" in url or ("_static." not in url and (".webp" in url or ".avif" in url))


def _scale_rank(scale: str) -> int:
    """Rank scale preference for image quality"""
    return {"3x": 4, "4x": 3, "2x": 2, "1x": 1}.get(scale, 0)


def twitch_boxart_url(category_name: str, width: int = 512, height: int = 680) -> str:
    """Build a direct Twitch box art URL for a category."""
    if not category_name:
        return "https://static-cdn.jtvnw.net/ttv-static/404_boxart-285x380.jpg"
    encoded = urllib.parse.quote(category_name, safe="")
    return f"https://static-cdn.jtvnw.net/ttv-boxart/{encoded}-{width}x{height}.jpg"


def twitch_logo_url() -> str:
    """Return Twitch logo URL"""
    return "https://raw.githubusercontent.com/winters27/StreamNook/refs/heads/main/assets/logo_1704751143960.JPG"

# https://img.freepik.com/premium-vector/twitch-vector-logo-twitch-vector-icon-logotype-social-media_901408-474.jpg
# https://s3-us-west-1.amazonaws.com/upload.comparably.com/15378/companies/15378/logo_1704751143960.jpg


def _normalize_url(u: str) -> str:
    """Normalize CDN URLs"""
    if not u:
        return u
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("/"):
        return "https://7tv.app" + u
    return u


def fetch_monthly_top_animated_from_html(
    page_url="https://raw.githubusercontent.com/winters27/StreamNook/refs/heads/main/data/trending_emotes.txt",
    timeout=8,
) -> list[str]:
    """
    Load emote URLs from GitHub list; prefer WebP over AVIF; drop *_static variants; shuffle.
    
    """
    try:
        req = urllib.request.Request(page_url, headers=_UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode("utf-8", "ignore")

        # Process URLs more efficiently
        urls = []
        for line in text.splitlines():
            u = _normalize_url(line.strip())
            if not u or "_static." in u:
                continue
            # Prefer animated WebP for QMovie
            if u.endswith(".avif"):
                u = u[:-5] + ".webp"
            urls.append(u)

        random.shuffle(urls)
        return urls
    except Exception as e:
        print(f"Error fetching emotes from GitHub: {e}")
        return []


# ============================================================================
# UI COMPONENTS
# ============================================================================

class LiveStreamCard(QtWidgets.QFrame):
    """A card widget displaying a live stream with thumbnail, title, viewer count, etc."""
    clicked = QtCore.Signal(str)

    def __init__(self, stream_data: dict, parent=None):
        super().__init__(parent)
        self.stream_data = stream_data
        self.user_login = stream_data.get("user_login", "")

        self.setFrameStyle(QtWidgets.QFrame.NoFrame)
        self.setCursor(QtCore.Qt.PointingHandCursor)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.thumbnail_label = QtWidgets.QLabel()
        self.thumbnail_label.setFixedSize(320, 180)
        self.thumbnail_label.setAlignment(QtCore.Qt.AlignCenter)
        self.thumbnail_label.setText("Loading...")
        layout.addWidget(self.thumbnail_label)

        info_widget = QtWidgets.QWidget()
        info_layout = QtWidgets.QVBoxLayout(info_widget)
        info_layout.setContentsMargins(8, 8, 8, 8)
        info_layout.setSpacing(4)

        channel_label = QtWidgets.QLabel(stream_data.get("user_name", "Unknown"))
        channel_label.setObjectName("CardTitle")
        info_layout.addWidget(channel_label)

        title = stream_data.get("title", "")
        title_label = QtWidgets.QLabel(title[:50] + "..." if len(title) > 50 else title)
        title_label.setObjectName("CardText")
        title_label.setWordWrap(True)
        info_layout.addWidget(title_label)

        game = stream_data.get("game_name", "")
        viewers = stream_data.get("viewer_count", 0)
        meta_label = QtWidgets.QLabel(f"{game} • {viewers:,} viewers")
        meta_label.setObjectName("CardSubtleText")
        info_layout.addWidget(meta_label)

        layout.addWidget(info_widget)

        self._load_thumbnail()

    def _load_thumbnail(self):
        """Load thumbnail asynchronously with caching"""
        thumb_url = self.stream_data.get("thumbnail_url", "")
        if not thumb_url:
            return

        thumb_url = thumb_url.replace("{width}", "640").replace("{height}", "360")
        user_id = self.stream_data.get("user_id", "unknown")
        cache_file = THUMBNAIL_CACHE_DIR / f"{user_id}_640x360.jpg"

        def load_image():
            try:
                # Check cache (5 min expiry)
                if cache_file.exists():
                    age = time.time() - cache_file.stat().st_mtime
                    if age < 300:
                        return cache_file.read_bytes()

                # Download
                req = urllib.request.Request(thumb_url, headers=_UA)
                with urllib.request.urlopen(req, timeout=5) as response:
                    image_data = response.read()

                # Cache
                cache_file.write_bytes(image_data)
                return image_data
            except Exception:
                return None

        def on_loaded(future):
            try:
                image_data = future.result()
                if image_data:
                    pixmap = QtGui.QPixmap()
                    pixmap.loadFromData(image_data)
                    if not pixmap.isNull():
                        self.thumbnail_label.setPixmap(
                            pixmap.scaled(320, 180, QtCore.Qt.KeepAspectRatioByExpanding, QtCore.Qt.SmoothTransformation)
                        )
                        self.thumbnail_label.setText("")
                    else:
                        self.thumbnail_label.setText("No Preview")
                else:
                    self.thumbnail_label.setText("No Preview")
            except Exception:
                self.thumbnail_label.setText("Error")

        future = IMAGE_LOAD_POOL.submit(load_image)
        future.add_done_callback(on_loaded)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.clicked.emit(self.user_login)
        super().mousePressEvent(event)


class LiveStreamsOverlay(QtWidgets.QWidget):
    """Overlay showing live followed channels"""
    stream_selected = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint | QtCore.Qt.Tool)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)

        self.container = QtWidgets.QFrame()
        self.container.setObjectName("OverlayContainer")
        self.container.setFixedWidth(1032)
        container_layout = QtWidgets.QVBoxLayout(self.container)
        container_layout.setContentsMargins(20, 20, 20, 20)

        header_layout = QtWidgets.QHBoxLayout()
        title_label = QtWidgets.QLabel("Live Followed Channels")
        title_label.setObjectName("OverlayTitle")
        header_layout.addWidget(title_label)
        header_layout.addStretch()

        close_btn = QtWidgets.QPushButton("✕")
        close_btn.setObjectName("OverlayCloseButton")
        close_btn.setFixedSize(32, 32)
        close_btn.clicked.connect(self.hide)
        header_layout.addWidget(close_btn)
        container_layout.addLayout(header_layout)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)

        self.grid_widget = QtWidgets.QWidget()
        self.grid_layout = QtWidgets.QGridLayout(self.grid_widget)
        self.grid_layout.setSpacing(16)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)

        scroll.setWidget(self.grid_widget)
        container_layout.addWidget(scroll)
        main_layout.addWidget(self.container)
        self.stream_cards = []

    def set_streams(self, streams: list):
        """Set streams to display"""
        for card in self.stream_cards:
            card.deleteLater()
        self.stream_cards.clear()

        columns = 3
        for idx, stream in enumerate(streams):
            card = LiveStreamCard(stream, self.grid_widget)
            card.clicked.connect(self._on_stream_clicked)
            self.grid_layout.addWidget(card, idx // columns, idx % columns)
            self.stream_cards.append(card)

    def _on_stream_clicked(self, user_login: str):
        self.stream_selected.emit(user_login)
        self.hide()

    def showEvent(self, event):
        super().showEvent(event)
        if self.parent():
            self.setGeometry(self.parent().geometry())


class LogWindow(QtWidgets.QDialog):
    """Log window for debugging"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Logs")
        self.resize(780, 360)
        v = QtWidgets.QVBoxLayout(self)
        self.text = QtWidgets.QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        v.addWidget(self.text)
        self.chk_autoscroll = QtWidgets.QCheckBox("Auto-scroll")
        self.chk_autoscroll.setChecked(True)
        v.addWidget(self.chk_autoscroll)

    def append(self, msg: str):
        self.text.appendPlainText(msg)
        if self.chk_autoscroll.isChecked():
            self.text.verticalScrollBar().setValue(self.text.verticalScrollBar().maximum())


class LoadingSpinner(QtWidgets.QWidget):
    """Animated loading spinner widget"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.angle = 0
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.update_angle)
        self.setFixedSize(50, 50)

    def update_angle(self):
        self.angle = (self.angle + 10) % 360
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.translate(self.width() / 2, self.height() / 2)

        pen = QtGui.QPen(QtGui.QColor("#5285a6"))
        pen.setWidth(4)
        painter.setPen(pen)

        painter.drawArc(
            QtCore.QRectF(-20, -20, 40, 40),
            self.angle * 16,
            90 * 16
        )


class LoadingWidget(QtWidgets.QWidget):
    """Loading widget with animated emote"""
    emote_data_loaded = QtCore.Signal(bytes)
    trending_emotes_ready = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.trending_emotes = []
        self._buffer = None  # Track buffer lifecycle
        self.fetch_trending_emotes()

        self.emote_data_loaded.connect(self._process_emote_data_on_main_thread)
        self.trending_emotes_ready.connect(self._on_trending_emotes_ready_on_main_thread)

        self.layout = QtWidgets.QVBoxLayout(self)
        self.layout.setAlignment(QtCore.Qt.AlignCenter)
        self.layout.setSpacing(20)

        self.spinner = LoadingSpinner(self)
        self.emote_label = QtWidgets.QLabel(self)
        self.emote_label.setFixedSize(128, 128)
        self.emote_label.setAlignment(QtCore.Qt.AlignCenter)

        self.animation_stack = QtWidgets.QStackedWidget()
        self.animation_stack.addWidget(self.spinner)
        self.animation_stack.addWidget(self.emote_label)
        self.layout.addWidget(self.animation_stack, 0, QtCore.Qt.AlignCenter)

        self.text_label = QtWidgets.QLabel("Loading...", self)
        self.text_label.setObjectName("Placeholder")
        self.layout.addWidget(self.text_label, 0, QtCore.Qt.AlignCenter)

        self.movie = QMovie(self)
        self.emote_label.setMovie(self.movie)

    def _fetch_trending_emotes_task(self):
        """Fetch trending emotes in background thread"""
        try:
            urls = fetch_monthly_top_animated_from_html()
            if urls:
                return urls
        except Exception as e:
            print(f"Monthly HTML scrape failed: {e}")

        # Fallback to v3 global set
        try:
            req = urllib.request.Request("https://7tv.io/v3/emote-sets/global", headers=_UA)
            with urllib.request.urlopen(req, timeout=6) as r:
                payload = json.loads(r.read())
            emotes = payload.get("emotes") or []
            animated = [e for e in emotes if (e.get("data") or {}).get("animated") is True]

            def pick(em):
                d = em.get("data") or {}
                host = d.get("host") or {}
                base = host.get("url")
                files = host.get("files") or []
                if not base or not files:
                    return None

                # Prefer 3x WebP/AVIF
                prefer = [
                    ("3x", "webp"), ("3x", "avif"),
                    ("4x", "webp"), ("4x", "avif"),
                    ("2x", "webp"), ("2x", "avif"),
                    ("1x", "webp"), ("1x", "avif"),
                ]

                for scale, ext in prefer:
                    name = f"{scale}.{ext}"
                    for f in files:
                        if f.get("name") == name:
                            return f"{base}/{name}"
                return f"{base}/{files[0]['name']}"

            return [u for u in (pick(e) for e in animated) if u]
        except Exception as e:
            print(f"Global fallback failed: {e}")
            return []

    def fetch_trending_emotes(self):
        """Initiate fetching trending emotes"""
        future = EMOTE_LOAD_POOL.submit(self._fetch_trending_emotes_task)
        future.add_done_callback(self._on_trending_emotes_fetched)

    def _on_trending_emotes_fetched(self, future):
        try:
            self.trending_emotes = future.result()
        except Exception as e:
            print(f"Error fetching trending emotes: {e}")
            self.trending_emotes = []
        self.trending_emotes_ready.emit()

    def _on_trending_emotes_ready_on_main_thread(self):
        if self.isVisible() and self.animation_stack.currentWidget() == self.spinner:
            self.start(self.text_label.text())

    def _load_emote_data_task(self, emote_url: str):
        """Download emote data - prefer WebP over AVIF"""
        candidates = [emote_url]
        if emote_url.endswith(".avif"):
            candidates = [emote_url[:-5] + ".webp", emote_url]

        for url in candidates:
            try:
                req = urllib.request.Request(url, headers=_UA)
                with urllib.request.urlopen(req, timeout=6) as response:
                    return response.read()
            except Exception:
                continue
        print(f"Emote data load failed: {emote_url}")
        return None

    def _on_emote_data_loaded(self, future):
        """Callback for emote data loading"""
        try:
            data = future.result()
            self.emote_data_loaded.emit(data)
        except Exception as e:
            print(f"Error retrieving emote data: {e}")
            self.emote_data_loaded.emit(None)

    @QtCore.Slot(bytes)
    def _process_emote_data_on_main_thread(self, data: bytes):
        """Process emote data on main thread"""
        if data:
            self.movie.stop()

            # Clean up old buffer
            if self._buffer:
                self._buffer.close()

            self._buffer = QtCore.QBuffer(self)
            self._buffer.setData(data)
            self._buffer.open(QtCore.QIODevice.ReadOnly)
            self.movie.setDevice(self._buffer)
            self.movie.start()

            # Fallback to static if animation doesn't start
            QtCore.QTimer.singleShot(150, lambda: (
                None if self.movie.state() == QMovie.Running else self._set_static_pixmap(data)
            ))
        else:
            self.animation_stack.setCurrentWidget(self.spinner)
            self.spinner.timer.start()

    def start(self, text="Loading..."):
        """Start loading animation"""
        self.text_label.setText(text)

        if self.trending_emotes:
            self.animation_stack.setCurrentWidget(self.emote_label)
            emote_url = _normalize_url(random.choice(self.trending_emotes))

            future = EMOTE_LOAD_POOL.submit(self._load_emote_data_task, emote_url)
            future.add_done_callback(self._on_emote_data_loaded)
        else:
            self.animation_stack.setCurrentWidget(self.spinner)
            self.spinner.timer.start()
            self.fetch_trending_emotes()

        self.setVisible(True)

    def _set_static_pixmap(self, data: bytes, size=128):
        """Set static pixmap if animation fails"""
        pix = QtGui.QPixmap()
        if pix.loadFromData(data):
            self.emote_label.setPixmap(
                pix.scaled(size, size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            )
        else:
            self.animation_stack.setCurrentWidget(self.spinner)
            self.spinner.timer.start()

    def stop(self):
        """Stop loading animation"""
        self.movie.stop()
        self.spinner.timer.stop()
        # Clean up buffer
        if self._buffer:
            self._buffer.close()
            self._buffer = None
        self.setVisible(False)


class PlayerContainerWidget(QtWidgets.QWidget):
    """Player container maintaining 16:9 aspect ratio"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.aspect_ratio = 16.0 / 9.0
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)

        w = self.width()
        h = self.height()

        if w <= 0 or h <= 0:
            return

        # Calculate optimal player size maintaining 16:9
        if w / h > self.aspect_ratio:
            target_h = h
            target_w = int(h * self.aspect_ratio)
        else:
            target_w = w
            target_h = int(w / self.aspect_ratio)

        # Center the player
        x = (w - target_w) // 2
        y = (h - target_h) // 2

        # Position child widgets
        for i in range(self.layout().count()):
            child = self.layout().itemAt(i).widget()
            if child:
                child.setGeometry(x, y, target_w, target_h)


# ============================================================================
# TWITCH LOGIN WORKER
# ============================================================================
class TwitchLoginWorker(QtCore.QObject):
    """Twitch OAuth login worker"""
    login_success = QtCore.Signal(str, str, str, str, str, int, str)
    login_failure = QtCore.Signal(str)
    device_code_ready = QtCore.Signal(str, str, int)

    def __init__(self, client_id: str, scopes: list[str], parent=None):
        super().__init__(parent)
        self._client_id = client_id
        self._scopes = scopes
        self._server = None
        self._auth_code = None
        self._auth_state = None

    @staticmethod
    def _now_s() -> int:
        """Get current timestamp"""
        return int(time.time())

    def _twitch_start_device_flow(self) -> dict:
        """POST /oauth2/device to start Device Code Flow."""
        data = urllib.parse.urlencode({
            "client_id": self._client_id,
            "scopes": " ".join(self._scopes),
        }).encode("ascii")

        req = urllib.request.Request(
            "https://id.twitch.tv/oauth2/device",
            data=data,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            payload = json.loads(r.read().decode("utf-8"))

        # Cache for poller
        self._twitch_device_code = payload["device_code"]
        self._twitch_device_poll_interval = int(payload.get("interval", 5))
        self._twitch_device_expires_at = self._now_s() + int(payload.get("expires_in", 1800))
        self._twitch_verification_uri = payload.get("verification_uri", "https://www.twitch.tv/activate")
        self._twitch_user_code = payload.get("user_code", "")

        return payload

    def _twitch_poll_device_token(self) -> dict:
        """Poll /oauth2/token until user authorizes"""
        interval = max(1, getattr(self, "_twitch_device_poll_interval", 5))
        dev_code = getattr(self, "_twitch_device_code", None)
        if not dev_code:
            raise RuntimeError("Device flow not started")

        while self._now_s() < getattr(self, "_twitch_device_expires_at", 0):
            body = urllib.parse.urlencode({
                "client_id": self._client_id,
                "scopes": " ".join(self._scopes),
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": dev_code,
            }).encode("ascii")

            req = urllib.request.Request(
                "https://id.twitch.tv/oauth2/token",
                data=body,
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                msg = e.read().decode("utf-8", errors="replace")
                if "authorization_pending" in msg:
                    time.sleep(interval)
                    continue
                if "slow_down" in msg:
                    interval += 2
                    time.sleep(interval)
                    continue
                if "expired_token" in msg:
                    raise RuntimeError("Device code expired. Please start login again.")
                raise RuntimeError(f"Device token poll failed {e.code}: {msg}")

        raise RuntimeError("Device code timed out. Please start login again.")

    def run_device_flow(self):
        """Run complete device flow"""
        try:
            info = self._twitch_start_device_flow()
            self.device_code_ready.emit(
                info.get("user_code", ""),
                info.get("verification_uri", "https://www.twitch.tv/activate"),
                int(info.get("expires_in", 1800)),
            )

            tokens = self._twitch_poll_device_token()
            access_token = tokens.get("access_token")
            refresh_token = tokens.get("refresh_token")
            expires_in = int(tokens.get("expires_in", 0))
            if not access_token:
                raise RuntimeError("No access_token in response")

            headers = {"Client-Id": self._client_id, "Authorization": f"Bearer {access_token}"}
            req_user = urllib.request.Request("https://api.twitch.tv/helix/users", headers=headers)
            with urllib.request.urlopen(req_user, timeout=10) as r_user:
                user_data = json.loads(r_user.read().decode("utf-8"))

            user = (user_data.get("data") or [{}])[0]
            user_id = user.get("id")
            username = user.get("login")
            display_name = user.get("display_name")
            profile_image_url = user.get("profile_image_url")

            if user_id and username:
                self.login_success.emit(
                    user_id, username, display_name, access_token, refresh_token, expires_in, profile_image_url
                )
            else:
                self.login_failure.emit("Failed to retrieve user info after login.")
        except Exception as e:
            self.login_failure.emit(f"Login failed.\n{e}")


# ============================================================================
# ANIMATED COMPONENTS
# ============================================================================
class AnimatedToggleSwitch(QtWidgets.QCheckBox):
    """Animated toggle switch widget"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(60, 30)
        self.setCursor(QtCore.Qt.PointingHandCursor)

        self._bg_color = QtGui.QColor("#555")
        self._handle_color = QtGui.QColor("#ddd")
        self._checked_bg_color = QtGui.QColor("#5285a6")
        self._checked_handle_color = QtGui.QColor("#fff")

        self._handle_pos = 15
        self.animation = QtCore.QPropertyAnimation(self, b"handle_pos", self)
        self.animation.setDuration(200)
        self.animation.setEasingCurve(QtCore.QEasingCurve.InOutQuad)

        self.stateChanged.connect(self._on_state_changed)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtCore.Qt.NoPen)

        rect = QtCore.QRect(0, 0, self.width(), self.height())

        bg_color = self._checked_bg_color if self.isChecked() else self._bg_color
        painter.setBrush(QtGui.QBrush(bg_color))
        painter.drawRoundedRect(rect, 15, 15)

        handle_color = self._checked_handle_color if self.isChecked() else self._handle_color
        painter.setBrush(QtGui.QBrush(handle_color))
        painter.drawEllipse(QtCore.QPointF(self.handle_pos, self.height() / 2), 12, 12)

    def _on_state_changed(self, state):
        self.animation.stop()
        self.animation.setEndValue(45 if self.isChecked() else 15)
        self.animation.start()

    @QtCore.Property(int)
    def handle_pos(self):
        return self._handle_pos

    @handle_pos.setter
    def handle_pos(self, pos):
        self._handle_pos = pos
        self.update()

    def setChecked(self, checked):
        super().setChecked(checked)
        self._handle_pos = 45 if checked else 15
        self.update()

    def sizeHint(self):
        return QtCore.QSize(60, 30)

    def minimumSizeHint(self):
        return QtCore.QSize(60, 30)

    def hitButton(self, pos: QtCore.QPoint):
        return self.contentsRect().contains(pos)


class AnimatedBurgerMenuButton(QtWidgets.QPushButton):
    """Animated burger menu button"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(40, 40)
        self.setCheckable(True)
        self.setObjectName("SidebarToggleButton")

        self._is_open = False
        self._line_length = 20
        self._line_thickness = 3
        self._line_spacing = 6

        # Initial burger state
        self._line1_y = -self._line_spacing
        self._line1_rotation = 0
        self._line1_x_offset = 0
        self._line2_opacity = 1.0
        self._line3_y = self._line_spacing
        self._line3_rotation = 0
        self._line3_x_offset = 0

        # Animation group
        self._animation_group = QtCore.QParallelAnimationGroup(self)

        # Line animations
        self._line1_y_anim = QtCore.QPropertyAnimation(self, b"line1_y")
        self._line1_rot_anim = QtCore.QPropertyAnimation(self, b"line1_rotation")
        self._line1_x_offset_anim = QtCore.QPropertyAnimation(self, b"line1_x_offset")
        self._line2_opacity_anim = QtCore.QPropertyAnimation(self, b"line2_opacity")
        self._line3_y_anim = QtCore.QPropertyAnimation(self, b"line3_y")
        self._line3_rot_anim = QtCore.QPropertyAnimation(self, b"line3_rotation")
        self._line3_x_offset_anim = QtCore.QPropertyAnimation(self, b"line3_x_offset")

        for anim in [self._line1_y_anim, self._line1_rot_anim, self._line1_x_offset_anim,
                     self._line2_opacity_anim,
                     self._line3_y_anim, self._line3_rot_anim, self._line3_x_offset_anim]:
            anim.setDuration(250)
            anim.setEasingCurve(QtCore.QEasingCurve.InOutQuad)
            self._animation_group.addAnimation(anim)

        self.toggled.connect(self._on_toggled)

    def _on_toggled(self, checked: bool):
        self._is_open = checked
        self._animation_group.stop()

        if checked:  # Burger to Arrow
            self._line1_y_anim.setStartValue(self._line1_y)
            self._line1_y_anim.setEndValue(0)
            self._line1_rot_anim.setStartValue(self._line1_rotation)
            self._line1_rot_anim.setEndValue(45)
            self._line1_x_offset_anim.setStartValue(self._line1_x_offset)
            self._line1_x_offset_anim.setEndValue(-5)
            self._line2_opacity_anim.setStartValue(self._line2_opacity)
            self._line2_opacity_anim.setEndValue(0.0)
            self._line3_y_anim.setStartValue(self._line3_y)
            self._line3_y_anim.setEndValue(0)
            self._line3_rot_anim.setStartValue(self._line3_rotation)
            self._line3_rot_anim.setEndValue(-45)
            self._line3_x_offset_anim.setStartValue(self._line3_x_offset)
            self._line3_x_offset_anim.setEndValue(-5)
        else:  # Arrow to Burger
            self._line1_y_anim.setStartValue(self._line1_y)
            self._line1_y_anim.setEndValue(-self._line_spacing)
            self._line1_rot_anim.setStartValue(self._line1_rotation)
            self._line1_rot_anim.setEndValue(0)
            self._line1_x_offset_anim.setStartValue(self._line1_x_offset)
            self._line1_x_offset_anim.setEndValue(0)
            self._line2_opacity_anim.setStartValue(self._line2_opacity)
            self._line2_opacity_anim.setEndValue(1.0)
            self._line3_y_anim.setStartValue(self._line3_y)
            self._line3_y_anim.setEndValue(self._line_spacing)
            self._line3_rot_anim.setStartValue(self._line3_rotation)
            self._line3_rot_anim.setEndValue(0)
            self._line3_x_offset_anim.setStartValue(self._line3_x_offset)
            self._line3_x_offset_anim.setEndValue(0)

        self._animation_group.start()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        text_color = QtGui.QColor("white" if self.underMouse() else "gray")

        pen = QtGui.QPen(text_color)
        pen.setWidth(self._line_thickness)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        painter.setPen(pen)

        center_x = self.width() / 2
        center_y = self.height() / 2

        # Line 1
        painter.save()
        painter.translate(center_x + self._line1_x_offset, center_y + self._line1_y)
        painter.rotate(self._line1_rotation)
        painter.drawLine(int(-self._line_length / 2), 0, int(self._line_length / 2), 0)
        painter.restore()

        # Line 2
        painter.setOpacity(self._line2_opacity)
        painter.drawLine(int(center_x - self._line_length / 2), int(center_y),
                        int(center_x + self._line_length / 2), int(center_y))
        painter.setOpacity(1.0)

        # Line 3
        painter.save()
        painter.translate(center_x + self._line3_x_offset, center_y + self._line3_y)
        painter.rotate(self._line3_rotation)
        painter.drawLine(int(-self._line_length / 2), 0, int(self._line_length / 2), 0)
        painter.restore()

    # Property definitions for animations
    def get_line1_y(self): return self._line1_y
    def set_line1_y(self, y): self._line1_y = y; self.update()
    line1_y = QtCore.Property(float, get_line1_y, set_line1_y)

    def get_line1_rotation(self): return self._line1_rotation
    def set_line1_rotation(self, rot): self._line1_rotation = rot; self.update()
    line1_rotation = QtCore.Property(float, get_line1_rotation, set_line1_rotation)

    def get_line1_x_offset(self): return self._line1_x_offset
    def set_line1_x_offset(self, offset): self._line1_x_offset = offset; self.update()
    line1_x_offset = QtCore.Property(float, get_line1_x_offset, set_line1_x_offset)

    def get_line2_opacity(self): return self._line2_opacity
    def set_line2_opacity(self, opacity): self._line2_opacity = opacity; self.update()
    line2_opacity = QtCore.Property(float, get_line2_opacity, set_line2_opacity)

    def get_line3_y(self): return self._line3_y
    def set_line3_y(self, y): self._line3_y = y; self.update()
    line3_y = QtCore.Property(float, get_line3_y, set_line3_y)

    def get_line3_rotation(self): return self._line3_rotation
    def set_line3_rotation(self, rot): self._line3_rotation = rot; self.update()
    line3_rotation = QtCore.Property(float, get_line3_rotation, set_line3_rotation)

    def get_line3_x_offset(self): return self._line3_x_offset
    def set_line3_x_offset(self, offset): self._line3_x_offset = offset; self.update()
    line3_x_offset = QtCore.Property(float, get_line3_x_offset, set_line3_x_offset)


# ============================================================================
# NATIVE CHILD CONTAINER
# ============================================================================
class NativeChildContainer(QtWidgets.QWidget):
    """Native window child container"""
    def __init__(self, hwnd_child: int, parent=None):
        super().__init__(parent)
        self.hwnd_child = int(hwnd_child)
        self.setContentsMargins(0, 0, 0, 0)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

        try:
            style = GetWindowLongW(self.hwnd_child, GWL_STYLE)
            exstyle = GetWindowLongW(self.hwnd_child, GWL_EXSTYLE)
            style &= ~(WS_CAPTION | WS_THICKFRAME | WS_BORDER)
            exstyle &= ~(WS_EX_CLIENTEDGE | WS_EX_WINDOWEDGE | WS_EX_NOACTIVATE)
            SetWindowLongW(self.hwnd_child, GWL_STYLE, style)
            SetWindowLongW(self.hwnd_child, GWL_EXSTYLE, exstyle)
            SetWindowPos(self.hwnd_child, 0, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)
        except Exception:
            pass

        self.winId()
        SetParent(self.hwnd_child, int(self.winId()))

        self._resize_timer = QtCore.QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self.fit_child)

    def mousePressEvent(self, event: QtGui.QMouseEvent):
        if event.button() == QtCore.Qt.LeftButton:
            user32.SetFocus(self.hwnd_child)
        super().mousePressEvent(event)

    def device_pixel_ratio(self) -> float:
        return self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0

    def fit_child(self):
        w, h, ratio = self.width(), self.height(), self.device_pixel_ratio()
        pw, ph = max(1, int(w * ratio)), max(1, int(h * ratio))
        SetWindowPos(self.hwnd_child, 0, 0, 0, pw, ph, SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW)
        user32.RedrawWindow(self.hwnd_child, None, None, RDW_INVALIDATE | RDW_UPDATENOW | RDW_ALLCHILDREN)

    def showEvent(self, ev: QtGui.QShowEvent) -> None:
        super().showEvent(ev)
        self.fit_child()
        QtCore.QTimer.singleShot(0, self.fit_child)

    def resizeEvent(self, ev: QtGui.QResizeEvent) -> None:
        self.fit_child()
        self._resize_timer.start(60)
        super().resizeEvent(ev)


# ============================================================================
# SETTINGS OVERLAY
# ============================================================================
class SettingsOverlay(QtWidgets.QWidget):
    """Settings overlay"""
    MAX_INPUT_WIDTH = 400
    MAX_BUTTON_WIDTH = 150

    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)

        self._initial_settings = settings
        self._parent_obj = parent if isinstance(parent, QtCore.QObject) else None
        self._parent_alive = True

        if self._parent_obj is not None:
            try:
                self._parent_obj.destroyed.connect(self._on_parent_destroyed)
            except Exception:
                pass

        self.setWindowFlags(QtCore.Qt.FramelessWindowHint | QtCore.Qt.Tool)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.settings = settings.copy()
        self.setObjectName("SettingsOverlay")

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)

        self.container = QtWidgets.QFrame()
        self.container.setObjectName("OverlayContainer")
        self.container.setFixedWidth(1032)
        container_layout = QtWidgets.QVBoxLayout(self.container)
        container_layout.setContentsMargins(20, 20, 20, 20)

        header_layout = QtWidgets.QHBoxLayout()
        title_label = QtWidgets.QLabel("Settings")
        title_label.setObjectName("OverlayTitle")
        header_layout.addWidget(title_label)
        header_layout.addStretch()

        close_btn = QtWidgets.QPushButton("✕")
        close_btn.setObjectName("OverlayCloseButton")
        close_btn.setFixedSize(32, 32)
        close_btn.clicked.connect(self.hide)
        header_layout.addWidget(close_btn)
        container_layout.addLayout(header_layout)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)

        form_container = QtWidgets.QWidget()
        form_container.setObjectName("SettingsFormContainer")

        form = QtWidgets.QFormLayout(form_container)
        form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapAllRows)
        form.setContentsMargins(25, 20, 25, 20)
        form.setVerticalSpacing(18)
        form.setHorizontalSpacing(15)
        form.setLabelAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

        # Twitch Accounts Section
        twitch_title = QtWidgets.QLabel("Twitch Accounts")
        twitch_title.setObjectName("SettingsSectionTitle")
        form.addRow(twitch_title)

        self.account_selector = QtWidgets.QComboBox()
        self.account_selector.setMinimumHeight(30)
        self.account_selector.setMaximumWidth(self.MAX_INPUT_WIDTH)
        self.account_selector.currentIndexChanged.connect(self._on_account_selected)

        self.add_account_btn = QtWidgets.QPushButton("Add Account")
        self.add_account_btn.setMaximumWidth(self.MAX_BUTTON_WIDTH)
        self.add_account_btn.clicked.connect(self._add_account)
        self.remove_account_btn = QtWidgets.QPushButton("Remove Account")
        self.remove_account_btn.setMaximumWidth(self.MAX_BUTTON_WIDTH)
        self.remove_account_btn.clicked.connect(self._remove_account)

        account_buttons_layout = QtWidgets.QHBoxLayout()
        account_buttons_layout.addWidget(self.add_account_btn)
        account_buttons_layout.addWidget(self.remove_account_btn)
        account_buttons_layout.addStretch()

        form.addRow("Current Account", self.account_selector)
        form.addRow("", account_buttons_layout)
        self._populate_accounts()
        form.addRow(QtWidgets.QLabel(""))

        # Discord Rich Presence Section
        discord_rpc_title = QtWidgets.QLabel("Discord Rich Presence")
        discord_rpc_title.setObjectName("SettingsSectionTitle")
        form.addRow(discord_rpc_title)

        self.discord_rpc_toggle = AnimatedToggleSwitch()
        self.discord_rpc_toggle.setChecked(self.settings.get("discord_rpc_enabled", True))
        form.addRow("", self.discord_rpc_toggle)
        form.addRow(QtWidgets.QLabel(""))

        # Paths & Executables Section
        paths_title = QtWidgets.QLabel("Paths & Executables")
        paths_title.setObjectName("SettingsSectionTitle")
        form.addRow(paths_title)

        self.streamlink_edit = QtWidgets.QLineEdit(self.settings.get("streamlink_path", ""))
        self.mpv_edit = QtWidgets.QLineEdit(self.settings.get("mpv_path", ""))
        self.chatterino_edit = QtWidgets.QLineEdit(self.settings.get("chatterino_path", ""))

        def browse_file(target):
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, "Select Executable", "", "Executable (*.exe);;All Files (*.*)"
            )
            if path:
                target.setText(path)

        form.addRow("Streamlink path", self._row(self.streamlink_edit, browse_file))
        form.addRow("mpv path", self._row(self.mpv_edit, browse_file))
        form.addRow("Chatterino path", self._row(self.chatterino_edit, browse_file))
        form.addRow(QtWidgets.QLabel(""))

        # Player Configuration Section
        player_title = QtWidgets.QLabel("Player Configuration")
        player_title.setObjectName("SettingsSectionTitle")
        form.addRow(player_title)

        self.player_args_edit = QtWidgets.QLineEdit(self.settings.get("player_args", ""))
        self.player_args_edit.setMaximumWidth(self.MAX_INPUT_WIDTH)
        self.mpv_title_edit = QtWidgets.QLineEdit(self.settings.get("mpv_title", "StreamNookMPV"))
        self.mpv_title_edit.setMaximumWidth(self.MAX_INPUT_WIDTH)
        self.quality_selector = QtWidgets.QComboBox()
        self.quality_selector.addItems(["best", "1440p", "1080p", "720p", "480p", "360p", "160p"])
        self.quality_selector.setMaximumWidth(self.MAX_INPUT_WIDTH)
        current_quality = self.settings.get("quality", "best")
        if current_quality in ["best", "1440p", "1080p", "720p", "480p", "360p", "160p"]:
            self.quality_selector.setCurrentText(current_quality)
        else:
            self.quality_selector.setCurrentText("best")

        form.addRow("MPV player args", self.player_args_edit)
        form.addRow("Quality", self.quality_selector)
        form.addRow("mpv window title", self.mpv_title_edit)
        form.addRow(QtWidgets.QLabel(""))

        # Streamlink Configuration Section
        streamlink_title = QtWidgets.QLabel("Streamlink Configuration")
        streamlink_title.setObjectName("SettingsSectionTitle")
        form.addRow(streamlink_title)

        self.sl_args_edit = QtWidgets.QLineEdit(self.settings.get("streamlink_args", ""))
        self.sl_args_edit.setMaximumWidth(self.MAX_INPUT_WIDTH)
        form.addRow("Streamlink args", self.sl_args_edit)
        form.addRow(QtWidgets.QLabel(""))

        # MPV Configuration Section
        mpv_config_title = QtWidgets.QLabel("MPV Configuration")
        mpv_config_title.setObjectName("SettingsSectionTitle")
        form.addRow(mpv_config_title)

        self.use_mpvconf_chk = QtWidgets.QCheckBox("Use mpv.conf from config dir")
        self.use_mpvconf_chk.setChecked(bool(self.settings.get("use_mpv_config", True)))
        self.mpv_dir_edit = QtWidgets.QLineEdit(
            self.settings.get("mpv_config_dir", str(_default_mpv_config_dir()))
        )

        def browse_dir(target):
            path = QtWidgets.QFileDialog.getExistingDirectory(
                self,
                "Select mpv config directory",
                self.mpv_dir_edit.text() or str(_default_mpv_config_dir())
            )
            if path:
                target.setText(path)

        form.addRow(self.use_mpvconf_chk)
        form.addRow("mpv config dir", self._row(self.mpv_dir_edit, browse_dir))
        form.addRow(QtWidgets.QLabel(""))

        # UI Settings Section
        ui_title = QtWidgets.QLabel("UI Settings")
        ui_title.setObjectName("SettingsSectionTitle")
        form.addRow(ui_title)

        self.show_logs_btn = QtWidgets.QPushButton("Show Logs")
        self.show_logs_btn.setMaximumWidth(self.MAX_BUTTON_WIDTH)
        form.addRow(self.show_logs_btn)
        self.show_logs_btn.clicked.connect(lambda: self.parent().toggle_logs(True))
        form.addRow(QtWidgets.QLabel(""))

        # Dialog buttons
        form.addRow(QtWidgets.QLabel(""))

        self.save_btn = QtWidgets.QPushButton("Save")
        self.save_btn.setMaximumWidth(self.MAX_BUTTON_WIDTH)
        self.save_btn.clicked.connect(self._save_settings)

        form.addRow(self.save_btn)

        scroll.setWidget(form_container)
        container_layout.addWidget(scroll)
        main_layout.addWidget(self.container)

    @QtCore.Slot()
    def _on_parent_destroyed(self):
        self._parent_alive = False

    def _alive_parent(self):
        """Returns parent if still valid"""
        p = self._parent_obj
        if p is None or not self._parent_alive:
            return None
        if shiboken6 is not None:
            try:
                if not shiboken6.isValid(p):
                    return None
            except Exception:
                return None
        return p

    def _save_settings(self):
        """Save settings with safe parent access"""
        parent = self._alive_parent()
        if parent is None or getattr(parent, "_closing", False):
            self.hide()
            return

        old_rpc = bool(getattr(parent, "settings", {}).get("discord_rpc_enabled", True))
        new_settings = self.get_data()
        new_rpc = bool(new_settings.get("discord_rpc_enabled", True))

        try:
            parent.settings = new_settings
            save_settings(parent.settings)
            if hasattr(parent, "_log"):
                parent._log("Settings saved.")
        except Exception as e:
            if hasattr(parent, "_log"):
                parent._log(f"Failed to save settings: {e}")

        # Handle Discord RPC state changes
        try:
            if old_rpc and not new_rpc and hasattr(parent, "stop_discord_client"):
                parent.stop_discord_client()
            elif not old_rpc and new_rpc and hasattr(parent, "start_discord_client"):
                parent.start_discord_client()
        except Exception as e:
            if hasattr(parent, "_log"):
                parent._log(f"Error managing Discord RPC: {e}")

        self.hide()

    def _populate_accounts(self):
        """Populate account selector"""
        self.account_selector.clear()
        accounts = self.settings.get("accounts", {})
        current_username = accounts.get("current", "")
        
        usernames = []
        for uid, data in accounts.items():
            if uid != "current":
                username = data.get("username", f"UID: {uid}")
                usernames.append(username)

        if not usernames:
            self.account_selector.addItem("No accounts")
            self.remove_account_btn.setEnabled(False)
        else:
            self.account_selector.addItems(usernames)
            if current_username in usernames:
                self.account_selector.setCurrentText(current_username)
            self.remove_account_btn.setEnabled(True)

    def _on_account_selected(self, index):
        """Handle account selection"""
        if index >= 0 and self.account_selector.itemText(index) != "No accounts":
            username = self.account_selector.itemText(index)
            self.settings["accounts"]["current"] = username
        else:
            self.settings["accounts"]["current"] = ""


    def _add_account(self):
        """Add new Twitch account"""
        parent = self._alive_parent()
        if parent and hasattr(parent, "login_with_twitch"):
            self.hide()
            parent.login_with_twitch()

    def _remove_account(self):
        """Remove selected account"""
        current_text = self.account_selector.currentText()
        if not current_text or current_text == "No accounts":
            return

        reply = QtWidgets.QMessageBox.question(
            self, "Remove Account",
            f"Remove account '{current_text}'?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No
        )

        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            accounts = self.settings.get("accounts", {})
            uid_to_remove = None
            for uid, data in accounts.items():
                if uid != "current" and data.get("username") == current_text:
                    uid_to_remove = uid
                    break

            if uid_to_remove:
                del accounts[uid_to_remove]
                if accounts.get("current") == current_text:
                    accounts["current"] = ""
                self.settings["accounts"] = accounts
                save_settings(self.settings)
                self._populate_accounts()

                parent = self._alive_parent()
                if parent:
                    parent._load_current_twitch_account()
                    if hasattr(parent.menu_bar, '_set_default_user_icon'):
                        parent.menu_bar._set_default_user_icon()

    def _row(self, line_edit, browse_cb):
        """Helper to create row with browse button"""
        w = QtWidgets.QWidget()
        w.setMaximumWidth(self.MAX_INPUT_WIDTH + self.MAX_BUTTON_WIDTH + 10)
        h = QtWidgets.QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        line_edit.setMaximumWidth(self.MAX_INPUT_WIDTH)
        h.addWidget(line_edit, 1)
        b = QtWidgets.QPushButton("Browse…")
        b.setMaximumWidth(self.MAX_BUTTON_WIDTH)
        b.clicked.connect(lambda: browse_cb(line_edit))
        h.addWidget(b)
        return w

    def get_data(self):
        """Collect all settings data"""
        self.settings["streamlink_path"] = self.streamlink_edit.text()
        self.settings["mpv_path"] = self.mpv_edit.text()
        self.settings["player_args"] = self.player_args_edit.text()
        self.settings["streamlink_args"] = self.sl_args_edit.text()
        self.settings["quality"] = self.quality_selector.currentText()
        self.settings["chatterino_path"] = self.chatterino_edit.text()
        self.settings["mpv_title"] = self.mpv_title_edit.text()
        self.settings["use_mpv_config"] = self.use_mpvconf_chk.isChecked()
        self.settings["mpv_config_dir"] = self.mpv_dir_edit.text()
        self.settings["discord_rpc_enabled"] = self.discord_rpc_toggle.isChecked()
        return self.settings

    def showEvent(self, event):
        super().showEvent(event)
        if self.parent():
            self.setGeometry(self.parent().geometry())


# ============================================================================
# MENU BAR
# ============================================================================
class MenuBar(QtWidgets.QWidget):
    play_stream_signal = QtCore.Signal(str)
    toggle_sidebar_signal = QtCore.Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.main_window = parent
        self.setFixedHeight(40)
        self.setObjectName("MenuBar")

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, -8, 0)
        layout.setSpacing(4)

        # Animated menu button
        self.sidebar_toggle_btn = AnimatedBurgerMenuButton()
        self.sidebar_toggle_btn.clicked.connect(self._toggle_icon_buttons)
        layout.addWidget(self.sidebar_toggle_btn, 0, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft)

        # Create icon buttons that will animate in
        self.icon_buttons = []
        self.icon_animations = []
        self.icons_visible = False

        # Define SVG templates
        settings_icon_svg = '<svg xmlns="http://www.w3.org/2000/svg" fill="{color}" viewBox="0 0 512 512"><path d="M456.7,242.27l-26.08-4.2a8,8,0,0,1-6.6-6.82c-.5-3.2-1-6.41-1.7-9.51a8.08,8.08,0,0,1,3.9-8.62l23.09-12.82a8.05,8.05,0,0,0,3.9-9.92l-4-11a7.94,7.94,0,0,0-9.4-5l-25.89,5a8,8,0,0,1-8.59-4.11q-2.25-4.2-4.8-8.41a8.16,8.16,0,0,1,.7-9.52l17.29-19.94a8,8,0,0,0,.3-10.62l-7.49-9a7.88,7.88,0,0,0-10.5-1.51l-22.69,13.63a8,8,0,0,1-9.39-.9c-2.4-2.11-4.9-4.21-7.4-6.22a8,8,0,0,1-2.5-9.11l9.4-24.75A8,8,0,0,0,365,78.77l-10.2-5.91a8,8,0,0,0-10.39,2.21L327.77,95.91a7.15,7.15,0,0,1-8.5,2.5s-5.6-2.3-9.8-3.71A8,8,0,0,1,304,87l.4-26.45a8.07,8.07,0,0,0-6.6-8.42l-11.59-2a8.07,8.07,0,0,0-9.1,5.61l-8.6,25.05a8,8,0,0,1-7.79,5.41h-9.8a8.07,8.07,0,0,1-7.79-5.41l-8.6-25.05a8.07,8.07,0,0,0-9.1-5.61l-11.59,2a8.07,8.07,0,0,0-6.6,8.42l.4,26.45a8,8,0,0,1-5.49,7.71c-2.3.9-7.3,2.81-9.7,3.71-2.8,1-6.1.2-8.8-2.91L167.14,75.17A8,8,0,0,0,156.75,73l-10.2,5.91A7.94,7.94,0,0,0,143.25,89l9.4,24.75a8.06,8.06,0,0,1-2.5,9.11c-2.5,2-5,4.11-7.4,6.22a8,8,0,0,1-9.39.9L111,116.14a8,8,0,0,0-10.5,1.51l-7.49,9a8,8,0,0,0,.3,10.62l17.29,19.94a8,8,0,0,1,.7,9.52q-2.55,4-4.8,8.41a8.11,8.11,0,0,1-8.59,4.11l-25.89-5a8,8,0,0,0-9.4,5l-4,11a8.05,8.05,0,0,0,3.9,9.92L85.58,213a7.94,7.94,0,0,1,3.9,8.62c-.6,3.2-1.2,6.31-1.7,9.51a8.08,8.08,0,0,1-6.6,6.82l-26.08,4.2a8.09,8.09,0,0,0-7.1,7.92v11.72a7.86,7.86,0,0,0,7.1,7.92l26.08,4.2a8,8,0,0,1,6.6,6.82c.5,3.2,1,6.41,1.7,9.51a8.08,8.08,0,0,1-3.9,8.62L62.49,311.7a8.05,8.05,0,0,0-3.9,9.92l4,11a7.94,7.94,0,0,0,9.4,5l25.89-5a8,8,0,0,1,8.59,4.11q2.25,4.2,4.8,8.41a8.16,8.16,0,0,1-.7,9.52L93.28,374.62a8,8,0,0,0-.3,10.62l7.49,9a7.88,7.88,0,0,0,10.5,1.51l22.69-13.63a8,8,0,0,1,9.39.9c2.4,2.11,4.9,4.21,7.4,6.22a8,8,0,0,1,2.5,9.11l-9.4,24.75a8,8,0,0,0,3.3,10.12l10.2,5.91a8,8,0,0,0,10.39-2.21l16.79-20.64c2.1-2.6,5.5-3.7,8.2-2.6,3.4,1.4,5.7,2.2,9.9,3.61a8,8,0,0,1,5.49,7.71l-.4,26.45a8.07,8.07,0,0,0,6.6,8.42l11.59,2a8.07,8.07,0,0,0,9.1-5.61l8.6-25a8,8,0,0,1,7.79-5.41h9.8a8.07,8.07,0,0,1,7.79,5.41l8.6,25a8.07,8.07,0,0,0,9.1,5.61l11.59-2a8.07,8.07,0,0,0,6.6-8.42l-.4-26.45a8,8,0,0,1,5.49-7.71c4.2-1.41,7-2.51,9.6-3.51s5.8-1,8.3,2.1l17,20.94A8,8,0,0,0,355,439l10.2-5.91a7.93,7.93,0,0,0,3.3-10.12l-9.4-24.75a8.08,8.08,0,0,1,2.5-9.12c2.5-2,5-4.1,7.4-6.21a8,8,0,0,1,9.39-.9L401,395.66a8,8,0,0,0,10.5-1.51l7.49-9a8,8,0,0,0-.3-10.62l-17.29-19.94a8,8,0,0,1-.7-9.52q2.55-4.05,4.8-8.41a8.11,8.11,0,0,1,8.59-4.11l25.89,5a8,8,0,0,0,9.4-5l4-11a8.05,8.05,0,0,0-3.9-9.92l-23.09-12.82a7.94,7.94,0,0,1-3.9-8.62c.6-3.2,1.2-6.31,1.7-9.51a8.08,8.08,0,0,1,6.6-6.82l26.08-4.2a8.09,8.09,0,0,0,7.1-7.92V250A8.25,8.25,0,0,0,456.7,242.27ZM256,112A143.82,143.82,0,0,1,395.38,220.12,16,16,0,0,1,379.85,240l-105.24,0a16,16,0,0,1-13.91-8.09l-52.1-91.71a16,16,0,0,1,9.85-23.39A146.94,146.94,0,0,1,256,112ZM112,256a144,144,0,0,1,43.65-103.41,16,16,0,0,1,25.17,3.47L233.06,248a16,16,0,0,1,0,15.87l-52.67,91.7a16,16,0,0,1-25.18,3.36A143.94,143.94,0,0,1,112,256ZM256,400a146.9,146.9,0,0,1-38.19-4.95,16,16,0,0,1-9.76-23.44l52.58-91.55a16,16,0,0,1,13.88-8H379.9a16,16,0,0,1,15.52,19.88A143.84,143.84,0,0,1,256,400Z"/></svg>'
        user_icon_svg = '<svg xmlns="http://www.w3.org/2000/svg" fill="{color}" viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 3c1.66 0 3 1.34 3 3s-1.34 3-3 3-3-1.34-3-3 1.34-3 3-3zm0 14.2c-2.5 0-4.71-1.28-6-3.22.03-1.99 4-3.08 6-3.08 1.99 0 5.97 1.09 6 3.08-1.29 1.94-3.5 3.22-6 3.22z"/></svg>'
        live_icon_svg = '<svg xmlns="http://www.w3.org/2000/svg" fill="{color}" viewBox="0 0 24 24" width="24px" height="24px"><path d="M5.98959236,4.92893219 C6.28248558,5.22182541 6.28248558,5.69669914 5.98959236,5.98959236 C2.67013588,9.30904884 2.67013588,14.6909512 5.98959236,18.0104076 C6.28248558,18.3033009 6.28248558,18.7781746 5.98959236,19.0710678 C5.69669914,19.363961 5.22182541,19.363961 4.92893219,19.0710678 C1.02368927,15.1658249 1.02368927,8.83417511 4.92893219,4.92893219 C5.22182541,4.63603897 5.69669914,4.63603897 5.98959236,4.92893219 Z M19.0710678,4.92893219 C22.9763107,8.83417511 22.9763107,15.1658249 19.0710678,19.0710678 C18.7781746,19.363961 18.3033009,19.363961 18.0104076,19.0710678 C17.7175144,18.7781746 17.7175144,18.3033009 18.0104076,18.0104076 C21.3298641,14.6909512 21.3298641,9.30904884 18.0104076,5.98959236 C17.7175144,5.69669914 17.7175144,5.22182541 18.0104076,4.92893219 C18.3033009,4.63603897 18.7781746,4.63603897 19.0710678,4.92893219 Z M8.81801948,7.75735931 C9.1109127,8.05025253 9.1109127,8.52512627 8.81801948,8.81801948 C7.06066017,10.5753788 7.06066017,13.4246212 8.81801948,15.1819805 C9.1109127,15.4748737 9.1109127,15.9497475 8.81801948,16.2426407 C8.52512627,16.5355339 8.05025253,16.5355339 7.75735931,16.2426407 C5.41421356,13.8994949 5.41421356,10.1005051 7.75735931,7.75735931 C8.05025253,7.46446609 8.52512627,7.46446609 8.81801948,7.75735931 Z M16.2426407,7.75735931 C18.5857864,10.1005051 18.5857864,13.8994949 16.2426407,16.2426407 C15.9497475,16.5355339 15.4748737,16.5355339 15.1819805,16.2426407 C14.8890873,15.9497475 14.8890873,15.4748737 15.1819805,15.1819805 C16.9393398,13.4246212 16.9393398,10.5753788 15.1819805,8.81801948 C14.8890873,8.52512627 14.8890873,8.05025253 15.1819805,7.75735931 C15.4748737,7.46446609 15.9497475,7.46446609 16.2426407,7.75735931 Z M12,10.5 C12.8284271,10.5 13.5,11.1715729 13.5,12 C13.5,12.8284271 12.8284271,13.5 12,13.5 C11.1715729,13.5 10.5,12.8284271 10.5,12 C10.5,11.1715729 11.1715729,10.5 12,10.5 Z"/></svg>'
        down_arrow_svg = '<svg xmlns="http://www.w3.org/2000/svg" fill="{color}" viewBox="0 0 24 24"><path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6 1.41-1.41z"/></svg>'
        hide_icon_svg = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" stroke="{color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><path d="M2 12c2.6-4.5 7.2-7 10-7s7.4 2.5 10 7c-2.6 4.5-7.2 7-10 7s-7.4-2.5-10-7z"/><circle cx="12" cy="12" r="2.5"/><path d="M3 3l18 18"/></svg>'
        right_arrow_svg = '<svg xmlns="http://www.w3.org/2000/svg" fill="{color}" viewBox="0 0 24 24"><path d="M8.59 16.59L13.17 12 8.59 7.41 10 6l6 6-6 6-1.41-1.41z"/></svg>'

        # Twitch account button
        self.btn_twitch = self._create_icon_button(user_icon_svg, "Twitch Account")
        self.btn_twitch.clicked.connect(lambda: self._open_account_manager())

        # Settings button
        self.btn_settings = self._create_icon_button(settings_icon_svg, "Settings")
        self.btn_settings.clicked.connect(lambda: self.main_window.open_settings())

        # Live channels button
        self.btn_live = self._create_icon_button(live_icon_svg, "Live Channels")
        self.btn_live.clicked.connect(lambda: self.main_window.show_live_overlay())

        # Chat placement buttons
        self.btn_chat_down = self._create_icon_button(down_arrow_svg, "Chat Below", checkable=True, placement_id="bottom")
        self.btn_chat_down.clicked.connect(lambda: self.main_window.set_chat_placement("bottom"))
        self.btn_chat_hide = self._create_icon_button(hide_icon_svg, "Hide Chat", checkable=True, placement_id="hidden")
        self.btn_chat_hide.clicked.connect(lambda: self.main_window.set_chat_placement("hidden"))
        self.btn_chat_right = self._create_icon_button(right_arrow_svg, "Chat on Right", checkable=True, placement_id="right")
        self.btn_chat_right.clicked.connect(lambda: self.main_window.set_chat_placement("right"))

        self.icon_buttons = [self.btn_twitch, self.btn_live, self.btn_settings, self.btn_chat_down, self.btn_chat_hide, self.btn_chat_right]

        # Group chat placement buttons for exclusive checking
        self.chat_placement_button_group = QtWidgets.QButtonGroup(self)
        self.chat_placement_button_group.setExclusive(True)
        self.chat_placement_button_group.addButton(self.btn_chat_down)
        self.chat_placement_button_group.addButton(self.btn_chat_hide)
        self.chat_placement_button_group.addButton(self.btn_chat_right)

        # Set initial checked state based on current settings
        initial_placement = self.main_window.settings.get("chat_placement", "right")
        if initial_placement == "bottom": self.btn_chat_down.setChecked(True)
        elif initial_placement == "hidden": self.btn_chat_hide.setChecked(True)
        else: self.btn_chat_right.setChecked(True)

        # Add all icon buttons to layout (they start hidden)
        for btn in self.icon_buttons:
            btn.setMaximumWidth(0)  # Start hidden
            btn.setVisible(False)
            layout.addWidget(btn, 0, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft)

        # Search input
        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setPlaceholderText("Enter channel or URL...")
        self.search_input.setFixedHeight(24)
        # Initialize visibility based on settings
        hide_on_startup = parent.settings.get("hide_search_bar_on_startup", True)
        self.search_input.setVisible(not hide_on_startup)
        self.search_input.returnPressed.connect(self._emit_play_signal)
        layout.addWidget(self.search_input, 1)

        # Loading indicator container (centered, initially hidden)
        self.loading_container = QtWidgets.QWidget()
        loading_layout = QtWidgets.QHBoxLayout(self.loading_container)
        loading_layout.setContentsMargins(0, 0, 0, 0)
        loading_layout.setSpacing(8)
        loading_layout.setAlignment(QtCore.Qt.AlignCenter)

        # Loading text
        self.loading_label = QtWidgets.QLabel()
        self.loading_label.setAlignment(QtCore.Qt.AlignCenter)
        self.loading_label.setStyleSheet("color: #efeff1; font-size: 14px; font-weight: bold;")
        loading_layout.addWidget(self.loading_label)

        # Right emote
        self.loading_emote_right = QtWidgets.QLabel()
        self.loading_emote_right.setFixedSize(32, 32)
        self.loading_emote_right.setAlignment(QtCore.Qt.AlignCenter)
        self.loading_emote_right.setScaledContents(True)
        loading_layout.addWidget(self.loading_emote_right)

        self.loading_container.setVisible(False)
        layout.addWidget(self.loading_container, 2)

        # Movie for animated emote
        self.loading_movie_right = QMovie(self)
        self.loading_emote_right.setMovie(self.loading_movie_right)

        # Buffer for loading emote data
        self.loading_buffer_right = None

        layout.addStretch()

    def _create_icon_button(self, svg_template: str, tooltip: str, checkable: bool = False, placement_id: str = None):
        """Create an icon button with SVG icon, with optional checkable state and placement_id"""
        btn = QtWidgets.QPushButton()
        btn.setObjectName("TitlebarIconButton")
        btn.setFixedSize(32, 32)
        btn.setToolTip(tooltip)
        btn.setCursor(QtCore.Qt.PointingHandCursor)
        btn.setCheckable(checkable)
        if placement_id: btn.setProperty("placement_id", placement_id)

        # Create icon label with normal color
        icon_label = QtWidgets.QLabel()
        icon_label.setAlignment(QtCore.Qt.AlignCenter)

        # Create pixmaps for different states
        normal_pixmap = self._create_icon_pixmap(svg_template, "#97b1b9")
        hover_pixmap = self._create_icon_pixmap(svg_template, "#efeff1")
        checked_pixmap = self._create_icon_pixmap(svg_template, "#5285a6") if checkable else None

        icon_label.setPixmap(normal_pixmap)

        # Store pixmaps on button for state changes
        btn._icon_label = icon_label
        btn._normal_pixmap = normal_pixmap
        btn._hover_pixmap = hover_pixmap
        btn._checked_pixmap = checked_pixmap

        # Layout for centering icon
        layout = QtWidgets.QHBoxLayout(btn)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(icon_label, 0, QtCore.Qt.AlignCenter)

        btn.setStyleSheet("""
            QPushButton#TitlebarIconButton {
                background-color: transparent;
                border: none;
                border-radius: 4px;
            }
            QPushButton#TitlebarIconButton:hover {
                background-color: rgba(255, 255, 255, 0.1);
            }
            QPushButton#TitlebarIconButton:checked {
                background-color: rgba(82, 133, 166, 0.3); /* A subtle background for checked state */
                border: 1px solid #5285a6;
            }
        """)

        # Install event filter for hover effects and checked state
        btn.installEventFilter(self)

        return btn

    @lru_cache(maxsize=30)
    def _create_icon_pixmap(self, svg_template: str, color: str):
        """Create a QPixmap from SVG template with specified color. Cached."""
        svg = svg_template.format(color=color)
        pixmap = QtGui.QPixmap.fromImage(QtGui.QImage.fromData(svg.encode("utf-8")))
        return pixmap.scaled(20, 20, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)

    def update_twitch_button_icon(self, profile_image_url: str = None, user_id: str = None):
        """
        Updates the Twitch account button with the user's profile picture.
        Falls back to the default user icon if profile picture is unavailable.
        """
        if profile_image_url and user_id:
            # Download profile picture in background thread
            def download_task():
                return self.main_window._download_profile_picture(profile_image_url, user_id)

            future = IMAGE_LOAD_POOL.submit(download_task)
            future.add_done_callback(lambda f: self._on_profile_picture_loaded(f.result()))
        else:
            # Reset to default user icon
            self._set_default_user_icon()

    def _on_profile_picture_loaded(self, pixmap: QtGui.QPixmap):
        """
        Callback when profile picture is loaded. Updates the button icon on main thread.
        """
        if pixmap and not pixmap.isNull():
            # Create circular pixmap for normal state
            circular_pixmap = self._create_circular_pixmap(pixmap, 32)

            # Create a slightly brighter version for hover state
            hover_pixmap = self._create_circular_pixmap_with_brightness(pixmap, 32, 1.3)

            # Update button icon with profile picture
            if hasattr(self.btn_twitch, '_icon_label'):
                # Replace the stored pixmaps with profile picture versions
                self.btn_twitch._normal_pixmap = circular_pixmap
                self.btn_twitch._hover_pixmap = hover_pixmap

                # Set the current displayed icon
                self.btn_twitch._icon_label.setPixmap(circular_pixmap)

                # Mark that this button has a profile picture
                self.btn_twitch._has_profile_picture = True
        else:
            self._set_default_user_icon()

    def _create_circular_pixmap(self, source_pixmap: QtGui.QPixmap, size: int) -> QtGui.QPixmap:
        """
        Creates a circular pixmap from a square source pixmap.
        """
        # Scale source to size
        scaled = source_pixmap.scaled(size, size, QtCore.Qt.KeepAspectRatioByExpanding, QtCore.Qt.SmoothTransformation)

        # Create circular mask
        target = QtGui.QPixmap(size, size)
        target.fill(QtCore.Qt.transparent)

        painter = QtGui.QPainter(target)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)

        # Create circular clipping path
        path = QtGui.QPainterPath()
        path.addEllipse(0, 0, size, size)
        painter.setClipPath(path)

        # Draw the scaled pixmap
        painter.drawPixmap(0, 0, scaled)
        painter.end()

        return target

    def _create_circular_pixmap_with_brightness(self, source_pixmap: QtGui.QPixmap, size: int, brightness_factor: float = 1.3) -> QtGui.QPixmap:
        """
        Creates a circular pixmap from a square source pixmap with brightness adjustment.
        brightness_factor > 1.0 makes it brighter (for hover effect)
        """
        # First create the circular pixmap
        circular = self._create_circular_pixmap(source_pixmap, size)

        # Create a brightened version
        image = circular.toImage()

        # Apply brightness adjustment
        for y in range(image.height()):
            for x in range(image.width()):
                color = QtGui.QColor(image.pixelColor(x, y))
                if color.alpha() > 0:  # Only modify non-transparent pixels
                    # Increase RGB values
                    r = min(255, int(color.red() * brightness_factor))
                    g = min(255, int(color.green() * brightness_factor))
                    b = min(255, int(color.blue() * brightness_factor))
                    color.setRgb(r, g, b, color.alpha())
                    image.setPixelColor(x, y, color)

        return QtGui.QPixmap.fromImage(image)

    def _set_default_user_icon(self):
        """
        Resets the Twitch button to the default user icon.
        """
        # Recreate the default SVG icon pixmaps
        user_icon_svg = '<svg xmlns="http://www.w3.org/2000/svg" fill="{color}" viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 3c1.66 0 3 1.34 3 3s-1.34 3-3 3-3-1.34-3-3 1.34-3 3-3zm0 14.2c-2.5 0-4.71-1.28-6-3.22.03-1.99 4-3.08 6-3.08 1.99 0 5.97 1.09 6 3.08-1.29 1.94-3.5 3.22-6 3.22z"/></svg>'

        normal_pixmap = self._create_icon_pixmap(user_icon_svg, "#97b1b9")
        hover_pixmap = self._create_icon_pixmap(user_icon_svg, "#efeff1")

        # Restore default icon pixmaps
        self.btn_twitch._normal_pixmap = normal_pixmap
        self.btn_twitch._hover_pixmap = hover_pixmap
        self.btn_twitch._icon_label.setPixmap(normal_pixmap)

        # Mark that this button no longer has a profile picture
        self.btn_twitch._has_profile_picture = False

    def eventFilter(self, obj, event):
        """Handle hover and checked events for icon buttons"""
        if obj.objectName() == "TitlebarIconButton":
            if event.type() == QtCore.QEvent.Type.Enter:
                if hasattr(obj, "_icon_label") and hasattr(obj, "_hover_pixmap"):
                    obj._icon_label.setPixmap(obj._hover_pixmap)
            elif event.type() == QtCore.QEvent.Type.Leave:
                if hasattr(obj, "_icon_label"):
                    if obj.isCheckable() and obj.isChecked():
                        obj._icon_label.setPixmap(obj._checked_pixmap)
                    else:
                        obj._icon_label.setPixmap(obj._normal_pixmap)
            elif event.type() == QtCore.QEvent.Type.MouseButtonPress and obj.isCheckable():
                # Update icon immediately on press for checkable buttons
                if obj.isChecked():
                    obj._icon_label.setPixmap(obj._normal_pixmap) # Will be unchecked after click
                else:
                    obj._icon_label.setPixmap(obj._checked_pixmap) # Will be checked after click
            elif event.type() == QtCore.QEvent.Type.MouseButtonRelease and obj.isCheckable():
                # Ensure correct icon after release, especially for exclusive buttons
                QtCore.QTimer.singleShot(0, lambda: self._update_checkable_icon_state(obj))
        return super().eventFilter(obj, event)

    def _update_checkable_icon_state(self, btn):
        """Ensures the correct icon is displayed for checkable buttons after state change."""
        if btn.isCheckable():
            if btn.isChecked():
                btn._icon_label.setPixmap(btn._checked_pixmap)
            else:
                btn._icon_label.setPixmap(btn._normal_pixmap)

    def _emit_play_signal(self):
        text = self.search_input.text().strip()
        if text:
            self.play_stream_signal.emit(text)

    def _toggle_icon_buttons(self):
        """Toggle visibility of icon buttons with sequential animation"""
        if self.icons_visible:
            self._hide_icon_buttons()
        else:
            self._show_icon_buttons()
        self.icons_visible = not self.icons_visible
        # Toggle the burger menu animation state
        self.sidebar_toggle_btn.setChecked(self.icons_visible)

    def _show_icon_buttons(self):
        """Show icon buttons with sequential animation"""
        # Clear any existing animations
        for anim in self.icon_animations:
            if anim.state() == QtCore.QAbstractAnimation.State.Running:
                anim.stop()
        self.icon_animations.clear()

        # Animate each button sequentially
        for i, btn in enumerate(self.icon_buttons):
            btn.setVisible(True)

            # Create width animation
            anim = QtCore.QPropertyAnimation(btn, b"maximumWidth")
            anim.setDuration(150)
            anim.setStartValue(0)
            anim.setEndValue(32)
            anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)

            # Keep the animation alive until it starts/finishes
            anim.finished.connect(lambda a=anim: None)

            # Staggered start
            QtCore.QTimer.singleShot(i * 50, anim.start)

            self.icon_animations.append(anim)


    def _hide_icon_buttons(self):
        """Hide icon buttons with sequential animation"""
        # Clear any existing animations
        for anim in self.icon_animations:
            if anim.state() == QtCore.QAbstractAnimation.State.Running:
                anim.stop()
        self.icon_animations.clear()

        # Animate each button in reverse order
        for i, btn in enumerate(reversed(self.icon_buttons)):
            # Create width animation
            anim = QtCore.QPropertyAnimation(btn, b"maximumWidth")
            anim.setDuration(150)
            anim.setStartValue(32)
            anim.setEndValue(0)
            anim.setEasingCurve(QtCore.QEasingCurve.InCubic)

            # Hide button when animation finishes
            anim.finished.connect(lambda b=btn, a=anim: self._on_icon_hide_animation_finished(b, a))

            # Delay each animation by 50ms
            QtCore.QTimer.singleShot(i * 50, anim.start)

            self.icon_animations.append(anim)

    def _on_icon_hide_animation_finished(self, button: QtWidgets.QPushButton, animation: QtCore.QPropertyAnimation):
        """Slot to handle cleanup after an icon hide animation finishes."""
        if animation.endValue() == 0 and animation.state() == QtCore.QAbstractAnimation.State.Stopped:
            button.setVisible(False)

    def _open_account_manager(self):
        """Open settings with focus on Twitch accounts"""
        self.main_window.open_settings()

    def show_loading_indicator(self, channel_name: str):
        """Show loading toast notification with animated emote"""
        # Get a random emote from the loading widget's trending emotes
        if hasattr(self.main_window, 'loading_widget') and self.main_window.loading_widget.trending_emotes:
            emote_url = random.choice(self.main_window.loading_widget.trending_emotes)
            # Load emote asynchronously and update toast
            self._load_emote_for_toast(emote_url, channel_name)
        else:
            # Show toast without emote
            self.main_window.toast_manager.show_loading(f"Switching to {channel_name}...")

    def _load_emote_for_toast(self, url: str, channel_name: str):
        """Load emote in background thread for toast"""
        def load_task():
            try:
                req = urllib.request.Request(url, headers=_UA)
                with urllib.request.urlopen(req, timeout=3) as r:
                    return r.read()
            except Exception:
                return None

        # Show toast first without emote
        loading_toast = self.main_window.toast_manager.show_loading(f"Switching to {channel_name}...")
        
        # Then load emote and update toast
        future = EMOTE_LOAD_POOL.submit(load_task)
        
        def on_emote_loaded(fut):
            try:
                data = fut.result()
                if data and loading_toast and loading_toast.isVisible():
                    loading_toast.set_emote_data(data)
            except:
                pass
        
        future.add_done_callback(on_emote_loaded)

    def hide_loading_indicator(self):
        """Hide loading toast notification"""
        self.main_window.toast_manager.hide_loading()

    def toggle_search_bar(self):
        if self.search_input.isVisible():
            self.hide_search_bar()
        else:
            self.show_search_bar()

    def show_search_bar(self):
        self.search_input.show()
        self.search_input.setFocus()

    def hide_search_bar(self):
        self.search_input.hide()
        self.search_input.clear()


# ============================================================================
# MAIN WINDOW
# ============================================================================
class MainWindow(QtWidgets.QMainWindow):
    twitch_login_completed = QtCore.Signal()
    live_streams_fetched = QtCore.Signal(list)

    connect_discord_signal = QtCore.Signal()
    update_discord_presence_signal = QtCore.Signal(str, str, str, str, int, int, str, str)
    clear_discord_presence_signal = QtCore.Signal()
    disconnect_discord_signal = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("MainWindow")
        self.setWindowTitle("Stream Nook")
        self._closing = False # Flag to indicate app shutdown

        self.settings = load_settings()

        # Initialize log buffer and window before any calls to _log
        self._log_buffer = []
        self.log_win: LogWindow | None = None
        
        # Initialize toast notification manager
        self.toast_manager = ToastManager(self)

        self.current_twitch_account = None # Stores the currently active Twitch account details
        self._load_current_twitch_account() # Load current account on startup

        # ---------- DISCORD PRESENCE  ----------
        DISCORD_CLIENT_ID = "1436402207485464596"
        self.app_start_time = int(time.time())
        self.discord_thread = QtCore.QThread(self)
        self.discord_client = DiscordPresenceClient(client_id=DISCORD_CLIENT_ID)
        self.discord_client.moveToThread(self.discord_thread)
        self.discord_thread.started.connect(self.discord_client.connect_to_discord)
        self.discord_client.status_updated.connect(lambda msg: hasattr(self, "_log") and self._log(f"[Discord] {msg}"))
        self.discord_client.error_occurred.connect(lambda err: hasattr(self, "_log") and self._log(f"[Discord Error] {err}"))
        self.connect_discord_signal.connect(self.discord_client.connect_to_discord)
        self.update_discord_presence_signal.connect(self.discord_client.update_presence)
        self.clear_discord_presence_signal.connect(self.discord_client.clear_presence)
        self.disconnect_discord_signal.connect(self.discord_client.disconnect_from_discord)
        self.discord_client.disconnected.connect(self.discord_thread.quit)

        if self.settings.get("discord_rpc_enabled", True):
            self.discord_thread.start()
        # --- end Discord RPC setup ---

        # Create simplified menu bar
        self.menu_bar = MenuBar(self)

        # Load profile picture if available
        if self.current_twitch_account:
            profile_url = self.current_twitch_account.get("profileImageUrl")
            user_id = self.current_twitch_account.get("userID")
            if profile_url and user_id:
                QtCore.QTimer.singleShot(100, lambda: self.menu_bar.update_twitch_button_icon(profile_url, user_id))

        self.proc_streamlink_url = None
        self.proc_mpv = None
        self.proc_chatterino = None
        self.mpv_hwnd = 0
        self.chatterino_hwnd = 0
        self.mpv_container = None
        self.ch_container = None

        self.player_area = PlayerContainerWidget()
        self.player_layout = self.player_area.layout()

        self.player_stack = QtWidgets.QStackedWidget()
        self.player_layout.addWidget(self.player_stack)

        self.mpv_placeholder = QtWidgets.QLabel("Welcome to Stream Nook", alignment=QtCore.Qt.AlignCenter)
        self.mpv_placeholder.setObjectName("Placeholder")
        self.player_stack.addWidget(self.mpv_placeholder)

        self.loading_widget = LoadingWidget()
        self.player_stack.addWidget(self.loading_widget)
        self.loading_widget.setVisible(False)

        self.chat_area = QtWidgets.QWidget()
        self.chat_area.setObjectName("ChatArea")
        self.chat_layout = QtWidgets.QVBoxLayout(self.chat_area)
        self.chat_layout.setContentsMargins(0, 0, 0, 0)
        self.chat_layout.setSpacing(0)
        self.right_placeholder = QtWidgets.QLabel("Chatterino", alignment=QtCore.Qt.AlignCenter)
        self.right_placeholder.setObjectName("Placeholder")
        self.chat_layout.addWidget(self.right_placeholder, 1)

        self.splitter_h = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.splitter_h.setContentsMargins(0, 0, 0, 0)
        self.splitter_h.setHandleWidth(2)
        self.splitter_v = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.splitter_v.setContentsMargins(0, 0, 0, 0)
        self.splitter_v.setHandleWidth(2)

        self.page_player_only = QtWidgets.QWidget()
        self.page_player_only_layout = QtWidgets.QVBoxLayout(self.page_player_only)
        self.page_player_only_layout.setContentsMargins(0, 0, 0, 0)
        self.page_player_only_layout.setSpacing(0)
        self.page_right = QtWidgets.QWidget()
        self.page_right_layout = QtWidgets.QVBoxLayout(self.page_right)
        self.page_right_layout.setContentsMargins(0, 0, 0, 0)
        self.page_right_layout.setSpacing(0)
        self.page_right_layout.addWidget(self.splitter_h, 1)
        self.page_bottom = QtWidgets.QWidget()
        self.page_bottom_layout = QtWidgets.QVBoxLayout(self.page_bottom)
        self.page_bottom_layout.setContentsMargins(0, 0, 0, 0)
        self.page_bottom_layout.setSpacing(0)
        self.page_bottom_layout.addWidget(self.splitter_v, 1)

        self.central_stack = QtWidgets.QStackedWidget()
        self.central_stack.addWidget(self.page_player_only)
        self.central_stack.addWidget(self.page_right)
        self.central_stack.addWidget(self.page_bottom)

        main_content_widget = QtWidgets.QWidget()
        main_content_layout = QtWidgets.QVBoxLayout(main_content_widget)
        main_content_layout.setContentsMargins(0, 0, 0, 0)
        main_content_layout.setSpacing(0)
        main_content_layout.addWidget(self.central_stack, 1)

        content_container = QtWidgets.QHBoxLayout()
        content_container.setSpacing(0)
        content_container.setContentsMargins(0,0,0,0)
        content_container.addWidget(main_content_widget, 1)

        overall_layout = QtWidgets.QVBoxLayout()
        overall_layout.setSpacing(0)
        overall_layout.setContentsMargins(0,0,0,0)
        overall_layout.addWidget(self.menu_bar)
        overall_layout.addLayout(content_container)

        central_widget = QtWidgets.QWidget()
        central_widget.setObjectName("CentralWidget")
        central_widget.setLayout(overall_layout)
        self.setCentralWidget(central_widget)

        self._apply_clip_children_to_toplevel()

        self.live_overlay = LiveStreamsOverlay(self)
        self.live_overlay.stream_selected.connect(self._play_from_live_overlay)
        self.live_streams_fetched.connect(self._process_live_streams_on_main_thread)

        self.menu_bar.play_stream_signal.connect(self.play_clicked)
        self.menu_bar.sidebar_toggle_btn.setChecked(False)

        self._log(f"Settings file: {SETTINGS_FILE}")
        for k in ("streamlink_path", "mpv_path", "chatterino_path", "mpv_config_dir"):
            self._log(f"{k} = {self.settings.get(k, '')}")

        # Ensure default mpv.conf exists if use_mpv_config is enabled
        if self.settings.get("use_mpv_config", True):
            mpv_config_path = Path(self.settings.get("mpv_config_dir", str(_default_mpv_config_dir()))) / "mpv.conf"
            if not mpv_config_path.exists():
                try:
                    mpv_config_path.parent.mkdir(parents=True, exist_ok=True)
                    mpv_config_path.write_text(DEFAULT_MPV_CONF_CONTENT, encoding="utf-8")
                    self._log(f"Created default mpv.conf at {mpv_config_path}")
                except Exception as e:
                    self._log(f"Failed to create default mpv.conf: {e}")

        initial_place = self.settings.get("chat_placement", "right")
        self.set_chat_placement(initial_place)

    def _load_current_twitch_account(self):
        accounts = self.settings.get("accounts", {})
        current_username = accounts.get("current", "")
        if current_username:
            for uid, account_data in accounts.items():
                if uid != "current" and account_data.get("username") == current_username:
                    self.current_twitch_account = account_data
                    self._log(f"Loaded current Twitch account: {current_username}")
                    return
        self.current_twitch_account = None
        self._log("No current Twitch account loaded.")

    def _download_profile_picture(self, url: str, user_id: str) -> QtGui.QPixmap:
        """
        Downloads and caches a Twitch profile picture.
        Returns a QPixmap of the profile picture, or None if download fails.
        """
        if not url:
            return None

        # Create cache directory for profile pictures
        cache_dir = APP_DIR / "profile_pictures"
        cache_dir.mkdir(parents=True, exist_ok=True)

        # Cache file path based on user ID
        cache_file = cache_dir / f"{user_id}.png"

        # Check if cached version exists and is recent (less than 7 days old)
        if cache_file.exists():
            file_age = time.time() - cache_file.stat().st_mtime
            if file_age < (7 * 24 * 60 * 60):  # 7 days in seconds
                # Load from cache
                pixmap = QtGui.QPixmap(str(cache_file))
                if not pixmap.isNull():
                    return pixmap

        # Download profile picture
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=5) as r:
                image_data = r.read()

            # Create pixmap from downloaded data
            pixmap = QtGui.QPixmap()
            if pixmap.loadFromData(image_data):
                # Save to cache
                pixmap.save(str(cache_file), "PNG")
                return pixmap
        except Exception as e:
            self._log(f"Failed to download profile picture: {e}")

        return None

    def _get_current_twitch_credentials(self) -> tuple[str, str] | tuple[None, None]:
        if self.current_twitch_account:
            return self.current_twitch_account.get("clientID"), self.current_twitch_account.get("oauthToken")
        return None, None


    def _log(self, msg: str):
        self._log_buffer.append(msg)
        if self.log_win: self.log_win.append(msg)

    def toggle_logs(self, show: bool):
        if show:
            if not self.log_win:
                self.log_win = LogWindow(self)
                for line in self._log_buffer: self.log_win.append(line)
            self.log_win.show()
            self.log_win.raise_()
            self.log_win.activateWindow()
        elif self.log_win:
            self.log_win.close()
            self.log_win = None


    def set_chat_placement(self, placement: str):
        placement = placement.lower().strip()
        if placement not in ("hidden", "right", "bottom"): placement = "right"

        if placement == "hidden":
            self._ensure_not_parented(self.player_area)
            self.page_player_only_layout.addWidget(self.player_area, 1)
            self._ensure_not_parented(self.chat_area)
            self.central_stack.setCurrentWidget(self.page_player_only)
        elif placement == "right":
            self._mount_in_splitter(self.splitter_h, True)
            self.central_stack.setCurrentWidget(self.page_right)
        else:
            self._mount_in_splitter(self.splitter_v, False)
            self.central_stack.setCurrentWidget(self.page_bottom)

        self._apply_default_splitter_sizes(placement)
        self._apply_default_geometry_for(placement)
        self.settings["chat_placement"] = placement
        save_settings(self.settings)
        self.chat_area.update() # Force repaint of the chat area to clear artifacts

        # Update the checked state of the chat placement buttons in the menu bar
        for btn in self.menu_bar.icon_buttons:
            if btn.property("placement_id") == placement:
                btn.setChecked(True)
            else:
                btn.setChecked(False)

    def _ensure_not_parented(self, w: QtWidgets.QWidget):
        if w.parent() is not None: w.setParent(None)

    def _mount_in_splitter(self, splitter: QtWidgets.QSplitter, horizontal: bool):
        for i in reversed(range(splitter.count())): splitter.widget(i).setParent(None)
        self._ensure_not_parented(self.player_area)
        self._ensure_not_parented(self.chat_area)
        splitter.addWidget(self.player_area)
        splitter.addWidget(self.chat_area)
        splitter.setStretchFactor(0, 3 if horizontal else 4)
        splitter.setStretchFactor(1, 2 if horizontal else 3)

    def _apply_default_splitter_sizes(self, placement: str):
        if placement == "right": self.splitter_h.setSizes([PLAYER_DEFAULT_W, CHAT_DEFAULT_W])
        elif placement == "bottom": self.splitter_v.setSizes([PLAYER_DEFAULT_H, CHAT_DEFAULT_H])

    def _apply_default_geometry_for(self, placement: str):
        # Only apply default geometry if the window is not maximized
        if not self.isMaximized():
            w, h = PLAYER_DEFAULT_W, PLAYER_DEFAULT_H
            if placement == "hidden": self.resize(w, h)
            elif placement == "right": self.resize(w + CHAT_DEFAULT_W + SPLITTER_HANDLE_PX, h)
            else: self.resize(w, h + CHAT_DEFAULT_H + SPLITTER_HANDLE_PX)

            # Center the window on the screen
            screen = self.screen()
            if screen:
                screen_geometry = screen.availableGeometry()
                x = screen_geometry.x() + (screen_geometry.width() - self.width()) // 2
                y = screen_geometry.y() + (screen_geometry.height() - self.height()) // 2
                self.move(x, y)

    def embed_mpv_hwnd(self, hwnd: int):
        if self.mpv_container:
            self.player_stack.removeWidget(self.mpv_container)
            self.mpv_container.deleteLater()

        container = NativeChildContainer(hwnd, self)
        container.setMinimumSize(200, 150)
        self.player_stack.addWidget(container)
        self.player_stack.setCurrentWidget(container)

        try: add_clip_children(int(self.player_area.winId()))
        except Exception: pass
        self.mpv_container = container
        self.mpv_hwnd = hwnd
        self._log("mpv embedded.")
        QtCore.QTimer.singleShot(0, container.fit_child)
        self.loading_widget.stop()

        self.menu_bar.hide_loading_indicator()

    def launch_and_embed_chatterino(self, channel_name: str = None):
            path = self.settings.get("chatterino_path") or ""
            if not path or not Path(path).exists():
                self._log("Set a valid Chatterino path in Settings.")
                return
            
            if self.proc_chatterino:
                try:
                    if self.proc_chatterino.poll() is None: # Check if it's running
                        self._log("Terminating existing Chatterino...")
                        self.proc_chatterino.terminate()
                        self.proc_chatterino.wait(timeout=5)
                except Exception as e: 
                    self._log(f"Error terminating existing Chatterino: {e}")
                
                if self.proc_chatterino and self.proc_chatterino.poll() is None:
                    self._log("Chatterino did not terminate, killing...")
                    try:
                        self.proc_chatterino.kill()
                        self.proc_chatterino.wait(timeout=2)
                    except Exception as e:
                        self._log(f"Error killing Chatterino: {e}")

                self.proc_chatterino = None
                self.chatterino_hwnd = 0
                if self.ch_container: self.ch_container.setParent(None); self.ch_container = None
            
            self.detach_chatterino()
            cmd = [path]
            if channel_name: cmd.extend(["--channels", f"t:{channel_name}"])
            try: self.proc_chatterino = subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
            except Exception as e: self._log(f"Failed to launch Chatterino: {e}"); return
            
            hwnd = 0
            for _ in range(50):
                time.sleep(0.1)
                if self.proc_chatterino.poll() is not None: break
                hwnd = find_main_window_for_pid(self.proc_chatterino.pid)
                if hwnd: break
            if hwnd: self.embed_chatterino_hwnd(hwnd)
            else: self._log("Could not find Chatterino window.")

    def embed_chatterino_hwnd(self, hwnd: int):
        while self.chat_layout.count():
            item = self.chat_layout.takeAt(0)
            if w := item.widget(): w.hide(); w.setParent(None); w.deleteLater()
        container = NativeChildContainer(hwnd, self)
        container.setMinimumSize(200, 150)
        self.chat_layout.addWidget(container, 1)
        try: add_clip_children(int(self.chat_area.winId()))
        except Exception: pass
        self.ch_container = container
        self.chatterino_hwnd = hwnd
        self._log("Chatterino embedded.")
        container.fit_child()
        QtCore.QTimer.singleShot(0, container.fit_child)

    def detach_chatterino(self):
        if self.chatterino_hwnd:
            try: SetParent(self.chatterino_hwnd, 0)
            except Exception: pass
        if self.ch_container: self.ch_container.setParent(None); self.ch_container = None
        self.chatterino_hwnd = 0
        ph = QtWidgets.QLabel("Chatterino", alignment=QtCore.Qt.AlignCenter)
        ph.setObjectName("Placeholder")
        self.chat_layout.addWidget(ph, 1)
        self.right_placeholder = ph

    def open_settings(self):
        # Initialize and show the settings overlay
        if not hasattr(self, 'settings_overlay'):
            self.settings_overlay = SettingsOverlay(self.settings, self)
            self.twitch_login_completed.connect(self.settings_overlay._populate_accounts)

        self.settings_overlay.show()
        self.settings_overlay.raise_()

    def _apply_clip_children_to_toplevel(self):
        try: self.winId(); add_clip_children(int(self.winId()))
        except Exception: pass

    def play_clicked(self, url_in: str, show_loading_overlay: bool = True):
            url_in = normalize_input(url_in)
            if not url_in: self._log("No channel/URL."); return

            channel_name = self._extract_channel_name_from_url(url_in) or url_in

            if self.mpv_container is not None:
                self.menu_bar.show_loading_indicator(channel_name)

            if show_loading_overlay:
                        self.loading_widget.start(f"Loading {channel_name}'s stream...")
                        self.player_stack.setCurrentWidget(self.loading_widget)

            sl = self.settings.get("streamlink_path") or "streamlink"
            quality = self.settings.get("quality", "best")
            sl_args_extra = self.settings.get("streamlink_args", "").strip()

            extra_args_list = shlex.split(sl_args_extra)
            filtered_extra_args = []
            skip_next = False
            for arg in extra_args_list:
                if skip_next:
                    skip_next = False
                    continue
                if arg == "--default-stream":
                    skip_next = True
                    continue
                filtered_extra_args.append(arg)

            args = [url_in, quality, "--stream-url"]
            final_args = filtered_extra_args + args

            proc = QtCore.QProcess(self)
            proc.setProgram(sl)
            proc.setArguments(final_args)
            proc.setProcessChannelMode(QtCore.QProcess.ProcessChannelMode.MergedChannels)
            self.proc_streamlink_url = proc
            def done(code, status):
                out = proc.readAllStandardOutput().data().decode(errors="replace").strip()
                if code == 0 and out.startswith("http"):
                    channel_name = self._extract_channel_name_from_url(url_in)

                    title, game_name = self._fetch_stream_title(channel_name) if channel_name else (None, None)
                    self.start_mpv_with_url(out, title, channel_name, game_name)
                    
                    if channel_name: self.launch_and_embed_chatterino(channel_name)
                else:
                    self._log(f"Failed to resolve stream URL (code={code}).")
                    self.loading_widget.stop()
                    self.player_stack.setCurrentWidget(self.mpv_placeholder)
                    self.menu_bar.hide_loading_indicator()
                self.proc_streamlink_url = None
            proc.finished.connect(done)
            proc.start()

    def _fetch_stream_title(self, channel_name: str) -> tuple[str | None, str | None]:
            cid, tok = self._get_current_twitch_credentials()
            if not cid or not tok:
                return None, None

            headers = {"Client-Id": cid, "Authorization": f"Bearer {tok}"}
            try:
                req = urllib.request.Request(f"https://api.twitch.tv/helix/streams?user_login={urllib.parse.quote(channel_name)}", headers=headers)
                with urllib.request.urlopen(req, timeout=5) as r:
                    data = json.loads(r.read().decode("utf-8"))

                stream_data = (data.get("data") or [{}])[0]
                title = stream_data.get("title")
                game_name = stream_data.get("game_name")
                return title, game_name
                
            except Exception as e:
                self._log(f"Twitch: Error fetching stream title: {e}")
                return None, None


    def start_mpv_with_url(self, media_url: str, stream_title: str | None = None, channel_name: str | None = None, game_name: str | None = None):
            if self.proc_mpv and self.proc_mpv.poll() is None:
                try: self.proc_mpv.terminate(); self.proc_mpv.wait(timeout=5)
                except Exception: pass
                self.proc_mpv = None; self.mpv_hwnd = 0
                if self.mpv_container:
                    self.player_stack.removeWidget(self.mpv_container)
                    self.mpv_container.deleteLater()
                    self.mpv_container = None
                self.player_stack.setCurrentWidget(self.mpv_placeholder)

            # Update Discord Presence
            try:
                if channel_name:
                    details = f"Watching {channel_name}"

                    title = (stream_title or "").strip()
                    if title and len(title) > 50:
                        title = title[:50] + "…"

                    category = (game_name or "").strip()
                    state = title or "Live on Twitch"

                    # FIX: Call the correctly imported function
                    discord_img = resolve_discord_game_image(category, _fetch_detectables_from_discord())
                    large_image_key = discord_img or twitch_boxart_url(category)
                    small_image_key = twitch_logo_url()

                    stream_url = f"https://twitch.tv/{channel_name}" if channel_name else ""
                    category_name = game_name or ""
                    
                    self.start_time = int(time.time()) # Set start time for 'Watching'
                    
                    self.update_discord_presence_signal.emit(
                        details,
                        state,
                        large_image_key,
                        small_image_key,
                        self.start_time,
                        3,  # WATCHING
                        stream_url,
                        category_name
                    )

                else:
                    self.update_discord_presence_signal.emit(
                        "Browsing channels",
                        "Idle",
                        "icon_256x256",
                        "",
                        self.app_start_time,
                        0,  # "Playing"
                        "",
                        ""
                    )
            except Exception as e:
                self._log(f"Failed to update Discord presence: {e}")

            mpv = self.settings.get("mpv_path") or "mpv"
            final_title = stream_title or self.settings.get("mpv_title", "StreamNookMPV")
            cmd = [mpv, "--force-window=immediate", "--keep-open=no", "--no-border", "--cache=no", f"--title={final_title}"]
            user_args = shlex.split(self.settings.get("player_args", "").strip())
            if bool(self.settings.get("use_mpv_config", True)):
                user_args = [a for a in user_args if a.strip() != "--no-config"]
                cfg_dir = self.settings.get("mpv_config_dir") or _default_mpv_config_dir()
                if (Path(cfg_dir) / "mpv.conf").exists(): cmd.append(f"--config-dir={str(Path(cfg_dir))}")
            cmd += user_args
            cmd.append(media_url)
            try: self.proc_mpv = subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
            except Exception as e:
                self._log(f"Failed to launch mpv: {e}")
                self.loading_widget.stop()
                self.player_stack.setCurrentWidget(self.mpv_placeholder)
                self.menu_bar.hide_loading_indicator()
                return
            hwnd = 0
            for _ in range(100):
                time.sleep(0.1)
                if self.proc_mpv.poll() is not None: break
                hwnd = find_main_window_for_pid(self.proc_mpv.pid)
                if hwnd: break
            if hwnd: self.embed_mpv_hwnd(hwnd)
            else:
                self._log("Could not find mpv window to embed.")
                self.loading_widget.stop()
                self.player_stack.setCurrentWidget(self.mpv_placeholder)
                self.menu_bar.hide_loading_indicator()

    def login_with_twitch(self):
        cid = TWITCH_CLIENT_ID
        if not cid:
            self.toast_manager.show_error("Twitch Client-ID is missing.")
            return

        self.loading_widget.start("Sign in with Twitch (Device Code)...")
        self.player_stack.setCurrentWidget(self.loading_widget)

        self.twitch_login_worker = TwitchLoginWorker(cid, TWITCH_SCOPES)
        self.twitch_login_worker.login_success.connect(self._handle_twitch_login_success)
        self.twitch_login_worker.login_failure.connect(self._handle_twitch_login_failure)

        def _on_device_code_ready(user_code: str, verification_uri: str, expires_in: int):
            try:
                webbrowser.open(verification_uri)
            except Exception:
                pass
            self.toast_manager.show_info(
                f"Twitch activation: Enter code {user_code} at the opened page",
                duration=6000
            )
            if hasattr(self, "_log"):
                self._log(f"Twitch device login: user_code={user_code}, url={verification_uri}")

        self.twitch_login_worker.device_code_ready.connect(_on_device_code_ready)

        self.twitch_login_future = TWITCH_LOGIN_POOL.submit(self.twitch_login_worker.run_device_flow)
        self.twitch_login_future.add_done_callback(self._on_twitch_login_finished)


    def _refresh_twitch_token(self, account_data: dict) -> bool:
        """
        Refreshes an expired Twitch OAuth token using the refresh token.
        Returns True if successful, False otherwise.
        """
        refresh_token = account_data.get("refreshToken")
        client_id = account_data.get("clientID", TWITCH_CLIENT_ID)

        if not refresh_token:
            self._log("No refresh token available. Re-authentication required.")
            return False

        try:
            data = urllib.parse.urlencode({
                "client_id": client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }).encode("ascii")

            req = urllib.request.Request(
                "https://id.twitch.tv/oauth2/token",
                data=data,
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    payload = json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 400:
                    self._log(f"Refresh failed (400 Bad Request). Body: {e.read().decode()}")
                    account_data["refreshToken"] = ""
                    user_id = account_data.get("userID")
                    if user_id:
                        self.settings["accounts"][f"uid{user_id}"] = account_data
                        save_settings(self.settings)
                    return False
                raise

            new_access_token = payload.get("access_token")
            new_refresh_token = payload.get("refresh_token")
            expires_in = payload.get("expires_in", 0)

            if not new_access_token:
                self._log("Token refresh failed: No access token returned")
                return False

            account_data["oauthToken"] = new_access_token
            if new_refresh_token:
                account_data["refreshToken"] = new_refresh_token
            account_data["tokenExpiry"] = int(time.time()) + expires_in if expires_in > 0 else 0

            user_id = account_data.get("userID")
            if user_id:
                self.settings["accounts"][f"uid{user_id}"] = account_data
                save_settings(self.settings)
                self._load_current_twitch_account()
                self._log("Twitch token refreshed successfully")

            return True

        except Exception as e:
            self._log(f"Error refreshing Twitch token: {e}")
            return False



    @QtCore.Slot(str, str, str, str, str, int, str)
    def _handle_twitch_login_success(self, user_id: str, username: str, display_name: str, access_token: str, refresh_token: str, expires_in: int, profile_image_url: str):
        if "accounts" not in self.settings: self.settings["accounts"] = {"current": ""}

        token_expiry = int(time.time()) + expires_in if expires_in > 0 else 0

        self.settings["accounts"][f"uid{user_id}"] = {
            "username": display_name or username,
            "userID": user_id,
            "clientID": TWITCH_CLIENT_ID,
            "oauthToken": access_token,
            "refreshToken": refresh_token,
            "tokenExpiry": token_expiry,
            "profileImageUrl": profile_image_url or ""
        }
        self.settings["accounts"]["current"] = display_name or username
        save_settings(self.settings)
        self._load_current_twitch_account()
        self.menu_bar.update_twitch_button_icon(profile_image_url, user_id)
        self.toast_manager.show_success(f"Login successful for {display_name or username}!")
        self.loading_widget.stop()
        self.player_stack.setCurrentWidget(self.mpv_placeholder)
        self.twitch_login_completed.emit()

    @QtCore.Slot(str)
    def _handle_twitch_login_failure(self, error_message: str):
        self.toast_manager.show_error(error_message)
        self.loading_widget.stop()
        self.player_stack.setCurrentWidget(self.mpv_placeholder)

    def _on_twitch_login_finished(self, future):
        try:
            future.result()
        except Exception as e:
            self._log(f"Twitch login worker encountered an unhandled exception: {e}")
            self.loading_widget.stop()
            self.player_stack.setCurrentWidget(self.mpv_placeholder)

    def show_live_overlay(self):
        cid, tok = self._get_current_twitch_credentials()
        if not tok:
            self.toast_manager.show_warning("Please login to Twitch first.")
            return

        self.loading_widget.start("Loading live streams...")
        if self.mpv_hwnd == 0:
            self.player_stack.setCurrentWidget(self.loading_widget)

        future = self._fetch_live_streams()
        future.add_done_callback(self._on_live_streams_fetched)

    def _on_live_streams_fetched(self, future):
        try:
            streams = future.result()
            self.live_streams_fetched.emit(streams)
        except Exception as e:
            self._log(f"Error fetching live streams in thread: {e}")
            self.live_streams_fetched.emit([])

    @QtCore.Slot(list)
    def _process_live_streams_on_main_thread(self, streams: list):
        self.loading_widget.stop()

        if not streams:
            self.toast_manager.show_info("None of your followed channels are currently live.")
            if self.mpv_hwnd == 0:
                self.player_stack.setCurrentWidget(self.mpv_placeholder)
            return
        self.live_overlay.set_streams(streams)
        self.live_overlay.show()
        self.live_overlay.raise_()

    def _fetch_live_streams_task(self) -> list:
        """
        Fetches live streams from Twitch API. This method is designed to be run in a separate thread.
        Includes automatic token refresh and retry logic.
        """
        if self.current_twitch_account:
            token_expiry = self.current_twitch_account.get("tokenExpiry", 0)
            if token_expiry > 0 and time.time() > (token_expiry - 300):
                self._log("Twitch token expired or expiring soon, refreshing...")
                if not self._refresh_twitch_token(self.current_twitch_account):
                    raise RuntimeError("Failed to refresh expired Twitch token. Please re-authenticate.")

        cid, tok = self._get_current_twitch_credentials()
        if not cid or not tok:
            raise RuntimeError("No Twitch credentials available.")

        headers = {"Client-Id": cid, "Authorization": f"Bearer {tok}"}

        try:
            req_user = urllib.request.Request("https://api.twitch.tv/helix/users", headers=headers)
            with urllib.request.urlopen(req_user, timeout=10) as r:
                user_data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 401:
                self._log("Received 401 error, attempting to refresh token...")
                if self.current_twitch_account and self._refresh_twitch_token(self.current_twitch_account):
                    cid, tok = self._get_current_twitch_credentials()
                    headers = {"Client-Id": cid, "Authorization": f"Bearer {tok}"}
                    req_user = urllib.request.Request("https://api.twitch.tv/helix/users", headers=headers)
                    with urllib.request.urlopen(req_user, timeout=10) as r:
                        user_data = json.loads(r.read().decode("utf-8"))
                else:
                    raise RuntimeError("Authentication failed. Please re-login to Twitch.")
            else:
                raise

        user_id = (user_data.get("data") or [{}])[0].get("id")
        if not user_id:
            raise RuntimeError("Could not resolve user ID for fetching followed streams.")

        url_followed_streams = f"https://api.twitch.tv/helix/streams/followed?user_id={urllib.parse.quote(user_id)}&first=100"
        try:
            req_followed = urllib.request.Request(url_followed_streams, headers=headers)
            with urllib.request.urlopen(req_followed, timeout=10) as r_followed:
                followed_streams_data = json.loads(r_followed.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 401:
                self._log("Received 401 error on followed streams request, attempting to refresh token...")
                if self.current_twitch_account and self._refresh_twitch_token(self.current_twitch_account):
                    cid, tok = self._get_current_twitch_credentials()
                    headers = {"Client-Id": cid, "Authorization": f"Bearer {tok}"}
                    req_followed = urllib.request.Request(url_followed_streams, headers=headers)
                    with urllib.request.urlopen(req_followed, timeout=10) as r_followed:
                        followed_streams_data = json.loads(r_followed.read().decode("utf-8"))
                else:
                    raise RuntimeError("Authentication failed. Please re-login to Twitch.")
            else:
                raise

        return followed_streams_data.get("data", [])

    def _fetch_live_streams(self):
        """
        Submits the live stream fetching task to the LIVE_STREAM_LOAD_POOL.
        """
        return LIVE_STREAM_LOAD_POOL.submit(self._fetch_live_streams_task)

    def _play_from_live_overlay(self, user_login: str):
        if user_login:
            show_loading = self.mpv_container is None
            self.play_clicked(user_login, show_loading_overlay=show_loading)
            self.launch_and_embed_chatterino(user_login)

    def _extract_channel_name_from_url(self, url: str) -> str | None:
        if "twitch.tv/" in url:
            return url.split("twitch.tv/")[1].split("/")[0].strip()
        return None

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
            """
            Mark we are closing and stop background threads cleanly before destruction.
            """
            self._closing = True
            
            loading_toast = None # Define toast outside of try block
            
            try:
                # --- MODIFIED SHUTDOWN TOAST LOGIC ---
                
                # 1. Manually create the LoadingToast
                # We bypass the manager to control the showing without animation
                loading_toast = LoadingToast("Shutting down...", self)
                
                # 2. Get its final position (from toast.py logic)
                margin = 20
                parent_height = self.geometry().height()
                end_x = margin
                # We use the toast's height, so we must let Qt calculate it first
                if hasattr(loading_toast, "height"):
                    toast_height = loading_toast.height()
                else:
                    toast_height = 90 # Fallback to max height
                
                y_pos = parent_height - toast_height - margin
                global_end = self.mapToGlobal(QtCore.QPoint(end_x, y_pos))

                # 3. Move it DIRECTLY to its final position (no animation)
                loading_toast.move(global_end)

                # 4. Load emote data (synchronously, short timeout)
                emote_url = "https://cdn.7tv.app/emote/01HDPKA22G0003DSKWRRPR1DC1/3x.avif"
                emote_data = None
                try:
                    req = urllib.request.Request(emote_url, headers=_UA)
                    with urllib.request.urlopen(req, timeout=1) as r:
                        emote_data = r.read()
                except Exception:
                    pass # Ignore if download fails

                if emote_data:
                    loading_toast.set_emote_data(emote_data)
                
                # 5. Show the toast
                # This just schedules the show/paint events
                loading_toast.show()
                loading_toast.raise_()
                
                # 6. Force the event loop to process the show/paint events
                app = QtWidgets.QApplication.instance()
                if app:
                    app.processEvents()
                    
                # --- END OF MODIFIED SECTION ---
                    
            except Exception as e:
                # Log the error, but continue shutdown
                self._log(f"Error showing shutdown toast: {e}")

            self._log("Shutting down external processes...")
            
            # Terminate MPV
            if self.proc_mpv and self.proc_mpv.poll() is None:
                try:
                    self._log("Terminating mpv...")
                    self.proc_mpv.terminate()
                    self.proc_mpv.wait(timeout=3)
                except Exception as e:
                    self._log(f"Error terminating mpv: {e}")
                    if self.proc_mpv.poll() is None:
                        self._log("mpv did not terminate, killing...")
                        self.proc_mpv.kill() # Force kill
            
            # Terminate Chatterino
            if self.proc_chatterino and self.proc_chatterino.poll() is None:
                try:
                    self._log("Terminating Chatterino...")
                    self.proc_chatterino.terminate()
                    self.proc_chatterino.wait(timeout=3)
                except Exception as e:
                    self._log(f"Error terminating Chatterino: {e}")
                    if self.proc_chatterino.poll() is None:
                        self.proc_chatterino.kill() # Force kill
            
            try:
                if hasattr(self, "stop_discord_client"):
                    self.stop_discord_client()
                if self.discord_thread.isRunning():
                    self._log("[Discord] Initiating graceful shutdown of Discord RPC thread.")
                    self.disconnect_discord_signal.emit()
            except Exception as e:
                self._log(f"Error during Discord client shutdown in closeEvent: {e}")
            
            # Clean up the toast we created, just in case
            if loading_toast:
                loading_toast.hide()
                loading_toast.deleteLater()

            super().closeEvent(event)


    def start_discord_client(self):
        """Starts the Discord RPC client thread and connects."""
        if not self.discord_thread.isRunning():
            self.discord_thread.start()
        else:
            self.connect_discord_signal.emit()

    def stop_discord_client(self):
        """
        Ask the Discord worker to disconnect and wait for the thread to stop.
        """
        if not self.discord_thread.isRunning():
            if hasattr(self, "_log"):
                self._log("[Discord] Stop requested, but thread is not running.")
            return

        if hasattr(self, "_log"):
            self._log("[Discord] Sending disconnect signal.")

        self.disconnect_discord_signal.emit()

        if not self.discord_thread.wait(5000):
            if hasattr(self, "_log"):
                self._log("[Discord] WARNING: Thread did not stop gracefully within 5 seconds.")
        else:
            if hasattr(self, "_log"):
                self._log("[Discord] Thread stopped successfully.")


# ============================================================================
# GLOBAL STYLESHEET
# ============================================================================
GLOBAL_STYLESHEET = """
#MainWindow, #CentralWidget, #ChatArea, SettingsDialog, LogWindow {
    background-color: #0c0c0d;
    color: #cccccc;
}
#MenuBar {
    background-color: #131314;
    border-bottom: 1px solid #30363d;
}
#TitleBar {
    background-color: #131314;
}
#TitleLabel {
    color: #cccccc;
    font-size: 11pt;
    font-weight: bold;
}
#Sidebar {
    background-color: #131314;
    border-right: 1px solid #30363d;
}
#Sidebar QLabel {
    color: #97b1b9;
    font-weight: bold;
}
#Placeholder {
    color: #555555;
    font-size: 14pt;
}
QSplitter::handle {
    background-color: #30363d;
}
QSplitter::handle:horizontal { width: 2px; }
QSplitter::handle:vertical { height: 2px; }

QPushButton {
    background-color: #333333;
    color: white;
    border: 1px solid #555555;
    padding: 6px;
    border-radius: 3px;
}
QPushButton:hover {
    background-color: #444444;
    border-color: #777777;
}
QPushButton:pressed {
    background-color: #2d2d2d;
}
#SidebarToggleButton {
    background-color: transparent;
    border: none;
    color: gray;
    font-size: 15pt;
    margin-top: -6px;
    margin-left: -6px;
}
#SidebarToggleButton:hover {
    color: white;
}
#WindowControlButton {
    background-color: transparent;
    border: none;
    color: gray;
    font-size: 13pt;
}
#WindowControlButton:hover { color: white; }
#SidebarButton {
    background-color: transparent;
    border: 1px solid #555555;
    border-radius: 4px;
    font-size: 13pt;
}
#SidebarButton:hover {
    background-color: #5285a6;
}
#SidebarButton:checked {
    background-color: #5285a6;
    border: 1px solid #88aaff;
}
#SidebarIconButton {
    background-color: transparent;
    border: none;
    padding: 0px;
}
#SidebarIconButton:hover {
    border-radius: 4px;
}
#SidebarIconButton:checked {
    background-color: transparent;
    border: 1px solid #88aaff;
    border-radius: 4px;
}

QLineEdit, QComboBox {
    background-color: #0d1117;
    color: white;
    border: 1px solid #30363d;
    padding: 4px;
    selection-background-color: #5285a6;
    border-radius: 3px;
    min-height: 18px;
}
QComboBox QAbstractItemView {
    background-color: #0d1117;
    color: white;
    selection-background-color: #5285a6;
    border: 1px solid #30363d;
}
QCheckBox {
    spacing: 8px;
}
QCheckBox::indicator:unchecked {
    background-color: #333333;
    border: 1px solid #555555;
}
QCheckBox::indicator:checked {
    background-color: #4CAF50;
    border: 1px solid #4CAF50;
}

QScrollBar:vertical {
    border: none;
    background: #171717;
    width: 10px;
    margin: 0px;
    border-radius: 5px;
}
QScrollBar::handle:vertical {
    background: #959595;
    border: none;
    min-height: 20px;
    border-radius: 5px;
}
QScrollBar::handle:vertical:hover {
    background: #ababab;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    border: none;
    background: none;
    height: 0px;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: none;
}

QScrollBar:horizontal {
    border: none;
    background: #171717;
    height: 10px;
    margin: 0px;
    border-radius: 5px;
}
QScrollBar::handle:horizontal {
    background: #959595;
    min-width: 20px;
    border-radius: 5px;
    border: none;
}
QScrollBar::handle:horizontal:hover {
    background: #ababab;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    border: none;
    background: none;
    width: 0px;
}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: none;
}

#OverlayContainer {
    background-color: rgba(14, 14, 16, 240);
    border-radius: 8px;
    border: 1px solid #30363d;
}
#OverlayTitle {
    color: #efeff1; font-size: 20px; font-weight: bold;
}
#OverlayCloseButton {
    background-color: #3a3a3d; color: #efeff1; border: none; border-radius: 4px; font-size: 18px;
}
#OverlayCloseButton:hover { background-color: #e74c3c; }
LiveStreamCard {
    background-color: #131314;
    border: 1px solid #30363d;
    border-radius: 6px;
}
LiveStreamCard:hover {
    border-color: #5285a6;
}
LiveStreamCard #CardTitle {
    color: #efeff1; font-weight: bold; font-size: 14px;
}
LiveStreamCard #CardText {
    color: #adadb8; font-size: 12px;
}
LiveStreamCard #CardSubtleText {
    color: #97b1b9; font-size: 11px;
}
LiveStreamCard QLabel {
    background-color: transparent;
}

#SettingsFormContainer {
    background-color: #0c0c0d;
}

#SettingsScrollArea {
    background-color: transparent;
    border: none;
}

#SettingsSectionTitle {
    font-size: 16pt;
    font-weight: bold;
    color: #efeff1;
    padding-top: 8px;
    padding-bottom: 8px;
    background-color: transparent;
}
"""


# ============================================================================
# MAIN EXECUTION
# ============================================================================
def main():
    """Main application entry point"""
    if sys.platform != "win32":
        print("This application targets Windows (win32) only.")
        return 1

    # Set AppUserModelID for Windows taskbar icon
    myappid = 'StreamNook.StreamNook'
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)

    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")

    # Set application icon
    app_icon_path = ASSETS_DIR / "icons" / "icon_256x256.ico"
    if app_icon_path.exists():
        app.setWindowIcon(QtGui.QIcon(str(app_icon_path)))
    else:
        print(f"Warning: {app_icon_path} not found. Using default icon.")

    # Load Satoshi Font
    font_path = ASSETS_DIR / "fonts" / "Satoshi" / "Fonts" / "OTF" / "Satoshi-Regular.otf"
    if font_path.exists():
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        if font_id != -1:
            font_families = QFontDatabase.applicationFontFamilies(font_id)
            if font_families:
                app.setFont(QtGui.QFont(font_families[0], 10))

    app.setStyleSheet(GLOBAL_STYLESHEET)

    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())