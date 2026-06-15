from __future__ import annotations

import asyncio
import logging
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ichannel.bot import build_bot
from ichannel.config import ConfigError, load_config
from ichannel.logging_ui import LogWindow, configure_file_logging, configure_logging


def main() -> None:
    log_window = LogWindow(title="iChangeChannels")
    configure_logging(log_window)
    logger = logging.getLogger("ichannel")

    try:
        config = load_config(ROOT / ".env")
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        log_window.run()
        return
    configure_file_logging(config.log_file)

    bot = build_bot(config)
    bot_loop: asyncio.AbstractEventLoop | None = None

    def bot_thread() -> None:
        nonlocal bot_loop

        async def runner() -> None:
            nonlocal bot_loop
            bot_loop = asyncio.get_running_loop()
            await bot.start(config.discord_token)

        try:
            asyncio.run(runner())
        except Exception:
            logger.exception("Bot stopped with an unhandled error")

    thread = threading.Thread(target=bot_thread, name="discord-bot", daemon=True)
    thread.start()

    def on_close() -> None:
        logger.info("Closing iChangeChannels")
        if bot_loop and bot_loop.is_running():
            asyncio.run_coroutine_threadsafe(bot.close(), bot_loop)

    log_window.on_close = on_close
    log_window.run()


if __name__ == "__main__":
    main()
