"""Win32 desktop helpers: WDA_EXCLUDEFROMCAPTURE, click-through, SendInput, and GlobalHotKey."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import time
from typing import Optional

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QWidget

WDA_NONE = 0x00000000
WDA_EXCLUDEFROMCAPTURE = 0x00000011

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000

LWA_COLORKEY = 0x00000001
LWA_ALPHA = 0x00000002

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010

COLORKEY_RGB: tuple[int, int, int] = (1, 2, 3)
# Win32 COLORREF is 0x00BBGGRR -> (R=1, G=2, B=3)
COLORKEY_COLORREF: int = 0x00030201

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

_VK_SPECIAL: dict[str, int] = {
    "SPACE": 0x20,
    "TAB": 0x09,
    "ENTER": 0x0D,
    "RETURN": 0x0D,
    "ESC": 0x1B,
    "ESCAPE": 0x1B,
    "BACKSPACE": 0x08,
    "BACK": 0x08,
    "INSERT": 0x2D,
    "INS": 0x2D,
    "DELETE": 0x2E,
    "DEL": 0x2E,
    "HOME": 0x24,
    "END": 0x23,
    "PGUP": 0x21,
    "PAGEUP": 0x21,
    "PGDN": 0x22,
    "PAGEDOWN": 0x22,
    "UP": 0x26,
    "DOWN": 0x28,
    "LEFT": 0x25,
    "RIGHT": 0x27,
    "CAPS": 0x14,
    "CAPSLOCK": 0x14,
    "TILDE": 0xC0,
    "`": 0xC0,
    "-": 0xBD,
    "=": 0xBB,
    "[": 0xDB,
    "]": 0xDD,
    "\\": 0xDC,
    ";": 0xBA,
    "'": 0xDE,
    ",": 0xBC,
    ".": 0xBE,
    "/": 0xBF,
}
for _i in range(1, 13):
    _VK_SPECIAL[f"F{_i}"] = 0x6F + _i


def is_win10_2004_or_newer() -> bool:
    """Return True if OS is Windows 10 Build 19041 (v2004) or newer (supports WDA_EXCLUDEFROMCAPTURE)."""
    try:
        ver = sys.getwindowsversion()
        return (ver.major > 10) or (ver.major == 10 and ver.build >= 19041)
    except Exception:
        return False


def set_exclude_from_capture(widget: QWidget, enabled: bool) -> bool:
    """Make a Qt top-level widget completely invisible to mss/DXGI/BitBlt screen capture."""
    if enabled and not is_win10_2004_or_newer():
        return False
    try:
        hwnd = int(widget.winId())
        if not hwnd:
            return False
        affinity = WDA_EXCLUDEFROMCAPTURE if enabled else WDA_NONE
        user32 = ctypes.windll.user32
        user32.SetWindowDisplayAffinity.argtypes = [wt.HWND, wt.DWORD]
        user32.SetWindowDisplayAffinity.restype = wt.BOOL
        ok = bool(user32.SetWindowDisplayAffinity(wt.HWND(hwnd), wt.DWORD(affinity)))
        return ok
    except Exception:
        return False


def get_window_display_affinity(widget: QWidget) -> int:
    """Query current Win32 GetWindowDisplayAffinity value (0x11 = WDA_EXCLUDEFROMCAPTURE)."""
    try:
        hwnd = int(widget.winId())
        if not hwnd:
            return 0
        out = wt.DWORD(0)
        user32 = ctypes.windll.user32
        user32.GetWindowDisplayAffinity.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
        user32.GetWindowDisplayAffinity.restype = wt.BOOL
        if user32.GetWindowDisplayAffinity(wt.HWND(hwnd), ctypes.byref(out)):
            return int(out.value)
    except Exception:
        pass
    return 0


def set_window_click_through(widget: QWidget, enabled: bool) -> None:
    """Toggle Win32 WS_EX_TRANSPARENT so clicks pass straight through floating overlays."""
    try:
        hwnd = int(widget.winId())
        if not hwnd:
            return
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if enabled:
            style = style | WS_EX_LAYERED | WS_EX_TRANSPARENT
        else:
            style = style & ~WS_EX_TRANSPARENT
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    except Exception:
        pass


def set_window_colorkey_alpha(
    widget: QWidget, alpha: float, colorkey_ref: int = COLORKEY_COLORREF
) -> bool:
    """Apply Win32 LWA_COLORKEY | LWA_ALPHA so #010203 background is transparent and text alpha is independent."""
    try:
        hwnd = int(widget.winId())
        if not hwnd:
            return False
        user32 = ctypes.windll.user32
        ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if not (ex_style & WS_EX_LAYERED):
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style | WS_EX_LAYERED)
        byte_alpha = max(1, min(255, int(round(float(alpha) * 255.0))))
        user32.SetLayeredWindowAttributes.argtypes = [wt.HWND, wt.DWORD, wt.BYTE, wt.DWORD]
        user32.SetLayeredWindowAttributes.restype = wt.BOOL
        return bool(
            user32.SetLayeredWindowAttributes(
                wt.HWND(hwnd),
                wt.DWORD(colorkey_ref),
                wt.BYTE(byte_alpha),
                wt.DWORD(LWA_COLORKEY | LWA_ALPHA),
            )
        )
    except Exception:
        return False


def set_window_no_activate(widget: QWidget) -> None:
    """Ensure backdrop HUD windows never steal activation focus when interacted with."""
    try:
        hwnd = int(widget.winId())
        if not hwnd:
            return
        user32 = ctypes.windll.user32
        ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if not (ex_style & WS_EX_NOACTIVATE):
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style | WS_EX_NOACTIVATE)
    except Exception:
        pass


def place_window_behind(back_widget: QWidget, front_widget: QWidget) -> bool:
    """Keep back_widget strictly behind front_widget in Win32 Z-order without activating."""
    try:
        back_hwnd = int(back_widget.winId())
        front_hwnd = int(front_widget.winId())
        if not back_hwnd or not front_hwnd or back_hwnd == front_hwnd:
            return False
        user32 = ctypes.windll.user32
        user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
        user32.SetWindowPos.restype = wt.BOOL
        return bool(
            user32.SetWindowPos(
                wt.HWND(back_hwnd),
                wt.HWND(front_hwnd),
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            )
        )
    except Exception:
        return False


# Win32 SendInput structures (exact 64-bit union size = 40 bytes)
class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wt.LONG),
        ("dy", wt.LONG),
        ("mouseData", wt.DWORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wt.WORD),
        ("wScan", wt.WORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wt.DWORD),
        ("wParamL", wt.WORD),
        ("wParamH", wt.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", _MOUSEINPUT),
        ("ki", _KEYBDINPUT),
        ("hi", _HARDWAREINPUT),
    ]


class _INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wt.DWORD),
        ("union", _INPUT_UNION),
    ]


def _kbd_input(vk: int, keyup: bool = False) -> _INPUT:
    inp = _INPUT()
    inp.type = 1  # INPUT_KEYBOARD
    inp.union.ki.wVk = vk
    inp.union.ki.wScan = 0
    inp.union.ki.dwFlags = 0x0002 if keyup else 0  # KEYEVENTF_KEYUP
    inp.union.ki.time = 0
    inp.union.ki.dwExtraInfo = None
    return inp


def send_ctrl_v_to_foreground() -> None:
    """Release Alt if held and synthesize Ctrl+V into the currently active foreground window."""
    try:
        user32 = ctypes.windll.user32
        VK_MENU = 0x12
        VK_CONTROL = 0x11
        VK_V = 0x56

        seq = (
            _kbd_input(VK_MENU, keyup=True),
            _kbd_input(VK_CONTROL, keyup=False),
            _kbd_input(VK_V, keyup=False),
            _kbd_input(VK_V, keyup=True),
            _kbd_input(VK_CONTROL, keyup=True),
        )
        arr = (_INPUT * len(seq))(*seq)
        time.sleep(0.06)
        user32.SendInput(len(seq), ctypes.byref(arr), ctypes.sizeof(_INPUT))
    except Exception:
        pass


def parse_hotkey(spec: str) -> Optional[tuple[int, int]]:
    """Parse hotkey string like 'Alt+Q', 'Ctrl+Shift+T', 'F8' into (modifiers_mask, vk_code)."""
    if not spec or not isinstance(spec, str):
        return None
    parts = [p.strip().upper() for p in spec.split("+") if p.strip()]
    if not parts:
        return None

    mods = 0
    key_token: Optional[str] = None
    for tok in parts:
        if tok in ("ALT", "MENU"):
            mods |= MOD_ALT
        elif tok in ("CTRL", "CONTROL"):
            mods |= MOD_CONTROL
        elif tok == "SHIFT":
            mods |= MOD_SHIFT
        elif tok in ("WIN", "SUPER", "META"):
            mods |= MOD_WIN
        else:
            key_token = tok

    if not key_token:
        return None

    vk: Optional[int] = None
    if key_token in _VK_SPECIAL:
        vk = _VK_SPECIAL[key_token]
    elif len(key_token) == 1:
        ch = key_token[0]
        if ("A" <= ch <= "Z") or ("0" <= ch <= "9"):
            vk = ord(ch)

    if vk is None:
        return None
    return mods, vk


def normalize_hotkey_spec(spec: str) -> str:
    """Normalize display representation like 'Alt+Q' or 'Ctrl+Shift+X'."""
    parsed = parse_hotkey(spec)
    if parsed is None:
        return spec.strip()
    mods, vk = parsed
    tokens: list[str] = []
    if mods & MOD_CONTROL:
        tokens.append("Ctrl")
    if mods & MOD_ALT:
        tokens.append("Alt")
    if mods & MOD_SHIFT:
        tokens.append("Shift")
    if mods & MOD_WIN:
        tokens.append("Win")

    key_name: Optional[str] = None
    for name, code in _VK_SPECIAL.items():
        if code == vk and len(name) <= 6:
            key_name = name.title() if not name.startswith("F") else name
            break
    if key_name is None:
        key_name = chr(vk)
    tokens.append(key_name)
    return "+".join(tokens)


class GlobalHotkeyManager(QObject):
    """Thread-safe Win32 RegisterHotKey dispatcher bound to a dedicated HWND with toggle & hold modes."""

    triggered = Signal(str)  # slot_name
    released = Signal(str)  # slot_name (emitted for hold_mode slots when key releases)

    class _Filter(QAbstractNativeEventFilter):
        def __init__(self, owner: "GlobalHotkeyManager") -> None:
            super().__init__()
            self._owner = owner

        def nativeEventFilter(self, eventType, message):
            try:
                if bytes(eventType) == b"windows_generic_MSG" and message is not None:
                    msg = ctypes.cast(int(message), ctypes.POINTER(wt.MSG)).contents
                    if int(msg.message) == WM_HOTKEY:
                        hid = int(msg.wParam)
                        if hid in self._owner._id_to_slot:
                            self._owner._on_wm_hotkey(hid)
                            return True, 0
            except Exception:
                pass
            return False, 0

    def __init__(self, app: QApplication, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._app = app
        self._user32 = ctypes.windll.user32
        self._next_id = 0x5100

        # Dedicated hidden native HWND guarantees WM_HOTKEY delivery across all focus states
        self._msg_win = QWidget(None)
        self._msg_win.setWindowTitle("GLIMPSE_HOTKEY_SINK")
        self._msg_hwnd = int(self._msg_win.winId())

        # slot_name -> (hotkey_id, mods, vk, hold_mode, spec)
        self._slots: dict[str, tuple[int, int, int, bool, str]] = {}
        self._id_to_slot: dict[int, str] = {}
        self._held_slots: dict[str, tuple[int, int]] = {}  # slot_name -> (mods, vk)
        self._last_trigger_ts: dict[str, float] = {}

        self._filter = self._Filter(self)
        self._app.installNativeEventFilter(self._filter)

        self._hold_timer = QTimer(self)
        self._hold_timer.setInterval(45)
        self._hold_timer.timeout.connect(self._poll_held_keys)

    def register_slot(self, name: str, spec: str, hold_mode: bool = False) -> bool:
        """Register or replace a named global hotkey slot. Returns True if OS registration succeeded."""
        self.unregister_slot(name)
        parsed = parse_hotkey(spec)
        if parsed is None:
            return False
        mods, vk = parsed
        hid = self._next_id
        self._next_id += 1
        ok = bool(self._user32.RegisterHotKey(wt.HWND(self._msg_hwnd), hid, mods | MOD_NOREPEAT, vk))
        if ok:
            self._slots[name] = (hid, mods, vk, hold_mode, normalize_hotkey_spec(spec))
            self._id_to_slot[hid] = name
        return ok

    def unregister_slot(self, name: str) -> None:
        info = self._slots.pop(name, None)
        if info is not None:
            hid = info[0]
            self._id_to_slot.pop(hid, None)
            self._held_slots.pop(name, None)
            try:
                self._user32.UnregisterHotKey(wt.HWND(self._msg_hwnd), hid)
            except Exception:
                pass

    def unregister_all(self) -> None:
        for name in list(self._slots.keys()):
            self.unregister_slot(name)
        self._hold_timer.stop()
        try:
            self._app.removeNativeEventFilter(self._filter)
        except Exception:
            pass
        try:
            self._msg_win.close()
        except Exception:
            pass

    def _on_wm_hotkey(self, hid: int) -> None:
        slot_name = self._id_to_slot.get(hid)
        if not slot_name:
            return
        info = self._slots.get(slot_name)
        if not info:
            return
        _hid, mods, vk, hold_mode, _spec = info
        now = time.monotonic()
        if hold_mode:
            if slot_name not in self._held_slots:
                self._held_slots[slot_name] = (mods, vk)
                self.triggered.emit(slot_name)
                if not self._hold_timer.isActive():
                    self._hold_timer.start()
        else:
            last = self._last_trigger_ts.get(slot_name, 0.0)
            if (now - last) < 0.18:
                return
            self._last_trigger_ts[slot_name] = now
            self.triggered.emit(slot_name)

    def _is_combo_down(self, slot_name: str, mods: int, vk: int) -> bool:
        # For Push-to-Talk quick_voice, keeping primary key down keeps mic open even if Alt lifted slightly earlier
        if not (self._user32.GetAsyncKeyState(vk) & 0x8000):
            return False
        if slot_name == "quick_voice":
            return True
        if (mods & MOD_ALT) and not (self._user32.GetAsyncKeyState(0x12) & 0x8000):
            return False
        if (mods & MOD_CONTROL) and not (self._user32.GetAsyncKeyState(0x11) & 0x8000):
            return False
        if (mods & MOD_SHIFT) and not (self._user32.GetAsyncKeyState(0x10) & 0x8000):
            return False
        return True

    def _poll_held_keys(self) -> None:
        if not self._held_slots:
            self._hold_timer.stop()
            return
        released_names: list[str] = []
        for slot_name, (mods, vk) in list(self._held_slots.items()):
            if not self._is_combo_down(slot_name, mods, vk):
                released_names.append(slot_name)
        for slot_name in released_names:
            self._held_slots.pop(slot_name, None)
            self.released.emit(slot_name)
        if not self._held_slots:
            self._hold_timer.stop()
