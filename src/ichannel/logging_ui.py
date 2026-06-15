from __future__ import annotations

import logging
import queue
import tkinter as tk
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter.scrolledtext import ScrolledText


class TkQueueHandler(logging.Handler):
    def __init__(self, message_queue: queue.Queue[str]) -> None:
        super().__init__()
        self.message_queue = message_queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.message_queue.put(self.format(record))
        except Exception:
            self.handleError(record)


class LogWindow:
    def __init__(self, title: str) -> None:
        self.root = tk.Tk()
        self.root.title(title)
        self.root.geometry("980x560")
        self.root.minsize(720, 360)

        self.text = ScrolledText(self.root, state="disabled", wrap="word")
        self.text.pack(fill="both", expand=True)

        self.queue: queue.Queue[str] = queue.Queue()
        self.on_close = lambda: None

        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(100, self._drain_queue)

    def run(self) -> None:
        self.root.mainloop()

    def _drain_queue(self) -> None:
        while True:
            try:
                message = self.queue.get_nowait()
            except queue.Empty:
                break
            self.text.configure(state="normal")
            self.text.insert("end", message + "\n")
            self.text.see("end")
            self.text.configure(state="disabled")
        self.root.after(100, self._drain_queue)

    def _close(self) -> None:
        self.on_close()
        self.root.after(250, self.root.destroy)


def configure_logging(log_window: LogWindow) -> None:
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()

    ui_handler = TkQueueHandler(log_window.queue)
    ui_handler.setFormatter(formatter)
    root_logger.addHandler(ui_handler)

    configure_file_logging(Path("data") / "ichannel.log", formatter=formatter)


def configure_file_logging(
    log_file: Path | str,
    *,
    formatter: logging.Formatter | None = None,
) -> None:
    if formatter is None:
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        if isinstance(handler, RotatingFileHandler):
            root_logger.removeHandler(handler)
            handler.close()

    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_path, maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
