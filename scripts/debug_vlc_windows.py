from __future__ import annotations

import ctypes
from pathlib import Path

import psutil


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def main() -> int:
    vlc_pids: set[int] = set()
    for process in psutil.process_iter(attrs=["pid", "name", "exe"]):
        name = (process.info.get("name") or "").lower()
        exe = (Path(process.info.get("exe") or "").name).lower()
        if name in {"vlc.exe", "vlc"} or exe in {"vlc.exe", "vlc"}:
            vlc_pids.add(int(process.info["pid"]))

    print(f"VLC PIDs: {sorted(vlc_pids)}")
    if not vlc_pids:
        return 1

    user32 = ctypes.windll.user32
    enum_windows_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    found = 0

    @enum_windows_proc
    def callback(hwnd, _lparam):
        nonlocal found
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in vlc_pids:
            return True

        title_buffer = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title_buffer, len(title_buffer))
        rect = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        visible = bool(user32.IsWindowVisible(hwnd))
        found += 1
        print(
            "HWND={hwnd} PID={pid} visible={visible} title={title!r} "
            "rect={left},{top},{width},{height}".format(
                hwnd=int(hwnd),
                pid=int(pid.value),
                visible=visible,
                title=title_buffer.value,
                left=int(rect.left),
                top=int(rect.top),
                width=int(rect.right - rect.left),
                height=int(rect.bottom - rect.top),
            )
        )
        return True

    user32.EnumWindows(callback, None)
    print(f"VLC-owned top-level windows found: {found}")
    return 0 if found else 1


if __name__ == "__main__":
    raise SystemExit(main())
