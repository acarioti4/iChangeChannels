from __future__ import annotations

import ctypes
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"


@dataclass(frozen=True)
class WindowSnapshot:
    pid: int
    title: str
    left: int
    top: int
    width: int
    height: int
    show_cmd: int
    launch_args: str | None = None


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("showCmd", ctypes.c_uint),
        ("ptMinPosition", POINT),
        ("ptMaxPosition", POINT),
        ("rcNormalPosition", RECT),
    ]


def main() -> int:
    if sys.platform != "win32":
        print("This script only captures VLC window placement on Windows.")
        return 1

    snapshot = capture_vlc_window()
    if snapshot is None:
        print("No visible VLC window was found in this desktop session.")
        print("Start or restore VLC, get it exactly how you want it, then run this script again.")
        return 1

    rect = f"{snapshot.left},{snapshot.top},{snapshot.width},{snapshot.height}"
    print("Captured VLC window:")
    print(f"  PID: {snapshot.pid}")
    print(f"  Title: {snapshot.title or '(untitled)'}")
    print(f"  VLC_WINDOW_RECT={rect}")
    print(f"  VLC_WINDOW_SHOW_CMD={snapshot.show_cmd}")
    print("  Note: VLC may be behind other windows; it only needs to be visible, not foreground.")
    if snapshot.launch_args:
        print(f"  VLC_ARGS={snapshot.launch_args}")

    if ENV_FILE.exists():
        answer = input("Write this VLC setup to .env? [Y/n]: ").strip().lower()
        if answer in {"", "y", "yes"}:
            values = {
                "VLC_WINDOW_RECT": rect,
                "VLC_WINDOW_SHOW_CMD": str(snapshot.show_cmd),
            }
            if snapshot.launch_args:
                values["VLC_ARGS"] = snapshot.launch_args
            update_env(values)
            print(".env updated.")
    else:
        print("No .env file found. Copy these values into .env after creating it.")

    return 0


def capture_vlc_window() -> WindowSnapshot | None:
    try:
        import psutil
    except ImportError:
        print("psutil is required so the script can identify the exact vlc.exe process.")
        return None

    vlc_pids: set[int] = set()
    launch_args_by_pid: dict[int, str] = {}
    for process in psutil.process_iter(attrs=["pid", "name", "exe"]):
        try:
            name = (process.info.get("name") or "").lower()
            exe = (Path(process.info.get("exe") or "").name).lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if name in {"vlc.exe", "vlc"} or exe in {"vlc.exe", "vlc"}:
            pid = int(process.info["pid"])
            vlc_pids.add(pid)
            try:
                cmdline = process.cmdline()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                cmdline = []
            args = cmdline[1:] if len(cmdline) > 1 else []
            if args:
                launch_args_by_pid[pid] = subprocess.list2cmdline(args)

    if not vlc_pids:
        return None

    user32 = ctypes.windll.user32
    snapshots: list[WindowSnapshot] = []

    enum_windows_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @enum_windows_proc
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True

        title_buffer = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title_buffer, len(title_buffer))
        title = title_buffer.value

        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in vlc_pids:
            return True

        rect = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True

        placement = WINDOWPLACEMENT()
        placement.length = ctypes.sizeof(WINDOWPLACEMENT)
        user32.GetWindowPlacement(hwnd, ctypes.byref(placement))

        normal = placement.rcNormalPosition
        if placement.showCmd == 3:
            left = int(normal.left)
            top = int(normal.top)
            width = int(normal.right - normal.left)
            height = int(normal.bottom - normal.top)
        else:
            left = int(rect.left)
            top = int(rect.top)
            width = int(rect.right - rect.left)
            height = int(rect.bottom - rect.top)

        snapshots.append(
            WindowSnapshot(
                pid=int(pid.value),
                title=title,
                left=left,
                top=top,
                width=width,
                height=height,
                show_cmd=int(placement.showCmd),
                launch_args=launch_args_by_pid.get(int(pid.value)),
            )
        )
        return True

    user32.EnumWindows(callback, None)
    snapshots.sort(key=lambda item: item.width * item.height, reverse=True)
    return snapshots[0] if snapshots else None


def update_env(values: dict[str, str]) -> None:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    remaining = dict(values)
    next_lines: list[str] = []

    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in remaining:
            next_lines.append(f"{key}={remaining.pop(key)}")
        else:
            next_lines.append(line)

    for key, value in remaining.items():
        next_lines.append(f"{key}={value}")

    ENV_FILE.write_text("\n".join(next_lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
