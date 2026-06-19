from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ichannel.state import StateStore  # noqa: E402


class StateStoreTests(unittest.TestCase):
    def test_malformed_active_session_shape_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "state.json"
            state_file.write_text(
                '{"active_session": {"guild_id": 123}}',
                encoding="utf-8",
            )

            store = StateStore(state_file)

            self.assertIsNone(store.get_active())

    def test_non_object_active_session_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "state.json"
            state_file.write_text('{"active_session": "broken"}', encoding="utf-8")

            store = StateStore(state_file)

            self.assertIsNone(store.get_active())

    def test_non_object_state_file_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "state.json"
            state_file.write_text("[]", encoding="utf-8")

            store = StateStore(state_file)

            self.assertIsNone(store.get_active())


if __name__ == "__main__":
    unittest.main()
