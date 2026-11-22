"""Windows top-level window / process enumeration and point-picking for audio isolation."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
from typing import Optional

import psutil

_IGNORED_EXES = {
    "explorer.exe",
    "textinputhost.exe",
    "shellexperiencehost.exe",
    "searchhost.exe",
    "startmenuexperiencehost.exe",
    "lockapp.exe",
    "systemsettings.exe",
}

_MEDIA_HINTS = (
    "msedge.exe",
    "chrome.exe",
    "firefox.exe",
    "brave.exe",
    "opera.exe",
    "vlc.exe",
    "potplayermini64.exe",
    "potplayermini.exe",
    "mpv.exe",
    "obs64.exe",
    "discord.exe",
    "telegram.exe",
    "wechat.exe",
    "qq.exe",
    "cloudmusic.exe",
    "qqmusic.exe",
    "spotify.exe",
    "bilibili.exe",
    "douyin.exe",
)


def _resolve_root_pid(pid: int) -> tuple[int, str]:
    """Walk up same-executable parents so multi-process browsers map to root PID."""
    try:
        proc = psutil.Process(pid)
        exe_name = proc.name()
        cur = proc
        for _ in range(6):
            parent = cur.parent()
            if parent is None:
                break
            try:
                if parent.name().lower() == exe_name.lower():
                    cur = parent
                else:
                    break
            except Exception:
                break
        return int(cur.pid), exe_name
    except Exception:
        return int(pid), f"pid_{pid}"


def list_audio_target_apps(exclude_pids: Optional[set[int]] = None) -> list[dict]:
    """Enumerate running desktop applications with visible windows."""
    excluded = set(exclude_pids or set())
    user32 = ctypes.windll.user32
    found: dict[int, dict] = {}

    enum_proc_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def _callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if not title:
            return True

        pid_val = wt.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_val))
        raw_pid = int(pid_val.value)
        if raw_pid <= 4 or raw_pid in excluded:
            return True

        root_pid, exe_name = _resolve_root_pid(raw_pid)
        if root_pid in excluded or exe_name.lower() in _IGNORED_EXES:
            return True

        if root_pid not in found:
            display_title = title
            short_title = (
                display_title if len(display_title) <= 28 else display_title[:27] + "…"
            )
            found[root_pid] = {
                "pid": root_pid,
                "exe": exe_name,
                "title": display_title,
                "label": f"{short_title} [{exe_name} · {root_pid}]",
            }
        return True

    cb = enum_proc_type(_callback)
    user32.EnumWindows(cb, 0)

    # Supplement with running browser/media root processes (e.g. minimized or another desktop)
    media_set = set(_MEDIA_HINTS)
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            pid = int(proc.info["pid"])
            pname = str(proc.info["name"] or "")
            if pid <= 4 or pid in excluded or pname.lower() not in media_set:
                continue
            root_pid, exe_name = _resolve_root_pid(pid)
            if root_pid in excluded or root_pid in found:
                continue
            display_title = exe_name
            found[root_pid] = {
                "pid": root_pid,
                "exe": exe_name,
                "title": display_title,
                "label": f"{display_title} [{exe_name} · {root_pid}]",
            }
        except Exception:
            continue

    items = list(found.values())
    items.sort(
        key=lambda item: (
            0 if item["exe"].lower() in _MEDIA_HINTS else 1,
            item["exe"].lower(),
            item["pid"],
        )
    )
    return items


def pick_process_at_point(
    x: int, y: int, exclude_pids: Optional[set[int]] = None
) -> Optional[dict]:
    """Identify the application root process owning the window under (x, y)."""
    excluded = set(exclude_pids or set())
    user32 = ctypes.windll.user32

    pt = wt.POINT(int(x), int(y))
    hwnd = user32.WindowFromPoint(pt)
    if not hwnd:
        return None
    root_hwnd = user32.GetAncestor(hwnd, 2) or hwnd  # GA_ROOT = 2

    pid_val = wt.DWORD(0)
    user32.GetWindowThreadProcessId(root_hwnd, ctypes.byref(pid_val))
    raw_pid = int(pid_val.value)
    if raw_pid <= 4 or raw_pid in excluded:
        return None

    root_pid, exe_name = _resolve_root_pid(raw_pid)
    if root_pid in excluded:
        return None

    length = user32.GetWindowTextLengthW(root_hwnd)
    title = ""
    if length > 0:
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(root_hwnd, buf, length + 1)
        title = buf.value.strip()
    if not title:
        title = exe_name

    short_title = title if len(title) <= 28 else title[:27] + "…"
    return {
        "pid": root_pid,
        "exe": exe_name,
        "title": title,
        "label": f"{short_title} [{exe_name} · {root_pid}]",
    }
