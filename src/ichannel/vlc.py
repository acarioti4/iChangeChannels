from __future__ import annotations

import asyncio
import ctypes
import logging
import sys
import subprocess
import time
from pathlib import Path

import psutil

SW_SHOWNOACTIVATE = 4
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040


class VLCError(RuntimeError):
    """Raised when VLC cannot be started or detected."""


class VLCManager:
    def __init__(
        self,
        vlc_path: str,
        vlc_args: list[str],
        process_names: list[str],
        window_rect: tuple[int, int, int, int] | None,
        window_show_cmd: int | None,
        start_timeout_seconds: float,
        logger: logging.Logger,
    ) -> None:
        self.vlc_path = vlc_path
        self.vlc_args = vlc_args
        self.process_names = {name.lower() for name in process_names}
        self.window_rect = window_rect
        self.window_show_cmd = window_show_cmd
        self.start_timeout_seconds = start_timeout_seconds
        self.logger = logger

    def is_running(self) -> bool:
        for process in psutil.process_iter(attrs=["name", "exe"]):
            try:
                name = (process.info.get("name") or "").lower()
                exe = (Path(process.info.get("exe") or "").name).lower()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if name in self.process_names or exe in self.process_names:
                return True
        return False

    async def ensure_open(self) -> bool:
        if self.is_running():
            self.logger.info("VLC is already running")
            return True

        self.logger.info("Starting VLC: %s", self.vlc_path)
        try:
            subprocess.Popen(
                [self.vlc_path, *self.vlc_args],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                close_fds=True,
            )
        except OSError as exc:
            raise VLCError(f"Could not start VLC at {self.vlc_path}") from exc

        deadline = asyncio.get_running_loop().time() + self.start_timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.5)
            if self.is_running():
                self.logger.info("VLC is running")
                await asyncio.to_thread(self.apply_window_placement)
                return True

        raise VLCError("VLC did not appear before timeout")

    def apply_window_placement(self) -> None:
        if self.window_rect is None and self.window_show_cmd is None:
            return
        if sys.platform != "win32":
            self.logger.warning("VLC window placement is only supported on Windows")
            return

        deadline = time.monotonic() + 5
        handle = self._find_vlc_window()
        while not handle and time.monotonic() < deadline:
            time.sleep(0.25)
            handle = self._find_vlc_window()

        if not handle:
            self.logger.warning("Could not find a VLC window to position")
            return

        if self.window_rect is not None:
            left, top, width, height = self.window_rect
            ctypes.windll.user32.ShowWindow(ctypes.c_void_p(handle), SW_SHOWNOACTIVATE)
            flags = SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW
            ok = ctypes.windll.user32.SetWindowPos(
                ctypes.c_void_p(handle),
                None,
                left,
                top,
                width,
                height,
                flags,
            )
            if ok:
                self.logger.info(
                    "Applied VLC window rectangle: left=%s top=%s width=%s height=%s",
                    left,
                    top,
                    width,
                    height,
                )
            else:
                self.logger.warning("Windows rejected the VLC window rectangle")

        if self.window_show_cmd is not None:
            if self.window_show_cmd == 3:
                ctypes.windll.user32.ShowWindow(ctypes.c_void_p(handle), self.window_show_cmd)
                self.logger.info("Applied VLC maximized window state")
            else:
                self.logger.info(
                    "Kept VLC visible without activating it; captured show command was %s",
                    self.window_show_cmd,
                )

    def _find_vlc_window(self) -> int | None:
        pids = self._running_pids()
        if not pids:
            return None

        user32 = ctypes.windll.user32
        handles: list[int] = []

        enum_windows_proc = ctypes.WINFUNCTYPE(
            ctypes.c_bool,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )

        @enum_windows_proc
        def callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids:
                handles.append(int(hwnd))
                return False
            return True

        user32.EnumWindows(callback, None)
        return handles[0] if handles else None

    def _running_pids(self) -> set[int]:
        pids: set[int] = set()
        for process in psutil.process_iter(attrs=["pid", "name", "exe"]):
            try:
                name = (process.info.get("name") or "").lower()
                exe = (Path(process.info.get("exe") or "").name).lower()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if name in self.process_names or exe in self.process_names:
                pids.add(int(process.info["pid"]))
        return pids
