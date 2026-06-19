from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ichannel.config import ConfigError, load_config  # noqa: E402


def write_env(path: Path, *extra_lines: str) -> None:
    lines = [
        "DISCORD_BOT_TOKEN=token",
        "STREAM_USER_ID=42",
        "ANDROID_TV_HOST=192.0.2.10",
        "ANDROID_TV_CERTFILE=data/androidtv_cert.pem",
        "ANDROID_TV_KEYFILE=data/androidtv_key.pem",
        *extra_lines,
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class ConfigTests(unittest.TestCase):
    def test_boolean_config_accepts_explicit_false(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            write_env(env_file, "DESKTOP_AUTOMATION_ENABLED=false")

            with patch.dict(os.environ, {}, clear=True):
                config = load_config(env_file)

        self.assertFalse(config.desktop_automation_enabled)

    def test_boolean_config_rejects_unknown_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            write_env(env_file, "DESKTOP_AUTOMATION_ENABLED=treu")

            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(ConfigError, "DESKTOP_AUTOMATION_ENABLED"):
                    load_config(env_file)

    def test_vlc_args_reject_invalid_quoting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            write_env(env_file, 'VLC_ARGS="unterminated')

            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(ConfigError, "VLC_ARGS"):
                    load_config(env_file)

    def test_vlc_args_strip_grouping_quotes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            write_env(env_file, r'VLC_ARGS=--input "C:\Video Files\clip.mp4" --fullscreen')

            with patch.dict(os.environ, {}, clear=True):
                config = load_config(env_file)

        self.assertEqual(
            config.vlc_args,
            ["--input", r"C:\Video Files\clip.mp4", "--fullscreen"],
        )

    def test_rejects_negative_timeout_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            write_env(env_file, "ANDROID_TV_POWER_TIMEOUT_SECONDS=-1")

            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(ConfigError, "ANDROID_TV_POWER_TIMEOUT_SECONDS"):
                    load_config(env_file)

    def test_rejects_non_finite_numeric_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            write_env(env_file, "REMOTE_KEY_RATE_PER_SECOND=nan")

            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(ConfigError, "REMOTE_KEY_RATE_PER_SECOND"):
                    load_config(env_file)

    def test_rejects_non_positive_discord_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            write_env(env_file, "STREAM_USER_ID=0")

            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(ConfigError, "STREAM_USER_ID"):
                    load_config(env_file)


if __name__ == "__main__":
    unittest.main()
