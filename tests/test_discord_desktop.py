from __future__ import annotations

import logging
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ichannel.discord_desktop import (  # noqa: E402
    DesktopAutomationConfig,
    DiscordDesktopController,
)


class FakeElementInfo:
    def __init__(self, name: str, control_type: str) -> None:
        self.name = name
        self.control_type = control_type
        self.runtime_id = None


class FakeRect:
    def __init__(
        self,
        left: int = 10,
        top: int = 10,
        width: int = 40,
        height: int = 40,
    ) -> None:
        self.left = left
        self.top = top
        self._width = width
        self._height = height

    def width(self) -> int:
        return self._width

    def height(self) -> int:
        return self._height


class FakeElement:
    def __init__(
        self,
        name: str,
        control_type: str = "Button",
        *,
        visible: bool = True,
        enabled: bool = True,
        rect: FakeRect | None = None,
    ) -> None:
        self._name = name
        self._visible = visible
        self._enabled = enabled
        self._rect = rect or FakeRect()
        self._parent = None
        self.element_info = FakeElementInfo(name, control_type)
        self.clicked = False
        self.invoked = False

    def window_text(self) -> str:
        return self._name

    def is_visible(self) -> bool:
        return self._visible

    def is_enabled(self) -> bool:
        return self._enabled

    def rectangle(self) -> FakeRect:
        return self._rect

    def parent(self):
        return self._parent

    def invoke(self) -> None:
        self.invoked = True

    def click_input(self) -> None:
        self.clicked = True


class FakeWindow:
    def __init__(self, elements: list[FakeElement]) -> None:
        self._elements = elements
        for element in elements:
            element._parent = self

    def descendants(self) -> list[FakeElement]:
        return self._elements

    def children(self) -> list[FakeElement]:
        return self._elements


class DiscordShareScreenSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = DiscordDesktopController(
            DesktopAutomationConfig(
                dm_search="iChangeChannels",
                vlc_window_title="VLC media player",
            ),
            logging.getLogger("ichannel.tests"),
        )

    def test_clicks_share_screen_instead_of_camera_when_camera_appears_first(self) -> None:
        camera_button = FakeElement("")
        camera_label = FakeElement("Turn On Camera", control_type="Text")
        share_button = FakeElement("")
        share_label = FakeElement("Share Your Screen", control_type="Text")
        window = FakeWindow([camera_button, camera_label, share_button, share_label])

        clicked = self.controller._click_share_screen_button(window, timeout=0.01)

        self.assertTrue(clicked)
        self.assertFalse(camera_button.clicked)
        self.assertFalse(camera_button.invoked)
        self.assertFalse(camera_label.clicked)
        self.assertFalse(camera_label.invoked)
        self.assertFalse(share_label.clicked)
        self.assertFalse(share_label.invoked)
        self.assertTrue(share_button.invoked)

    def test_rejects_camera_label_even_when_it_contains_share_screen_text(self) -> None:
        ambiguous_camera = FakeElement("Turn On Camera Share Your Screen")
        window = FakeWindow([ambiguous_camera])

        clicked = self.controller._click_share_screen_button(window, timeout=0.01)

        self.assertFalse(clicked)
        self.assertFalse(ambiguous_camera.clicked)
        self.assertFalse(ambiguous_camera.invoked)

    def test_clicks_exact_share_screen_text_control(self) -> None:
        share_button = FakeElement("")
        share_label = FakeElement("Share Your Screen", control_type="Text")
        window = FakeWindow([share_button, share_label])

        clicked = self.controller._click_share_screen_button(window, timeout=0.01)

        self.assertTrue(clicked)
        self.assertFalse(share_label.clicked)
        self.assertFalse(share_label.invoked)
        self.assertTrue(share_button.invoked)

    def test_does_not_click_generic_or_adjacent_controls(self) -> None:
        camera = FakeElement("Turn On Camera", control_type="Text", rect=FakeRect(left=10, top=100))
        screen = FakeElement("", rect=FakeRect(left=60, top=100))
        generic_screen = FakeElement("Screen", rect=FakeRect(left=110, top=100))
        window = FakeWindow([camera, screen, generic_screen])

        clicked = self.controller._click_share_screen_button(window, timeout=0.01)

        self.assertFalse(clicked)
        self.assertFalse(camera.clicked)
        self.assertFalse(camera.invoked)
        self.assertFalse(screen.clicked)
        self.assertFalse(screen.invoked)
        self.assertFalse(generic_screen.clicked)
        self.assertFalse(generic_screen.invoked)

    def test_ignores_hidden_screen_share_and_clicks_visible_screen_share(self) -> None:
        hidden_share_screen = FakeElement("Share Your Screen", visible=False)
        visible_share_screen = FakeElement("Share Your Screen")
        window = FakeWindow([hidden_share_screen, visible_share_screen])

        clicked = self.controller._click_share_screen_button(window, timeout=0.01)

        self.assertTrue(clicked)
        self.assertFalse(hidden_share_screen.clicked)
        self.assertFalse(hidden_share_screen.invoked)
        self.assertTrue(visible_share_screen.invoked)

    def test_start_vlc_stream_flow_does_not_activate_camera(self) -> None:
        camera_button = FakeElement("")
        camera_label = FakeElement("Turn On Camera", control_type="Text")
        share_button = FakeElement("")
        share_label = FakeElement("Share Your Screen", control_type="Text")
        vlc_window = FakeElement("VLC media player", control_type="ListItem")
        go_live = FakeElement("Go Live")
        windows = iter(
            [
                FakeWindow([camera_button, camera_label, share_button, share_label]),
                FakeWindow([vlc_window]),
                FakeWindow([go_live]),
            ]
        )

        with patch.object(
            self.controller, "_focus_discord", side_effect=lambda: next(windows)
        ):
            with patch("ichannel.discord_desktop.time.sleep"):
                self.controller._start_vlc_stream_sync()

        self.assertFalse(camera_button.clicked)
        self.assertFalse(camera_button.invoked)
        self.assertFalse(camera_label.clicked)
        self.assertFalse(camera_label.invoked)
        self.assertTrue(share_button.invoked)
        self.assertFalse(share_label.invoked)
        self.assertTrue(vlc_window.clicked)
        self.assertTrue(go_live.clicked)

    def test_start_vlc_stream_turns_camera_off_before_screen_share(self) -> None:
        camera_off_button = FakeElement("")
        camera_off_label = FakeElement("Turn Off Camera", control_type="Text")
        share_button = FakeElement("")
        share_screen = FakeElement("Share Your Screen", control_type="Text")
        vlc_window = FakeElement("VLC media player", control_type="ListItem")
        go_live = FakeElement("Go Live")
        windows = iter(
            [
                FakeWindow([camera_off_button, camera_off_label, share_button, share_screen]),
                FakeWindow([share_button, share_screen]),
                FakeWindow([vlc_window]),
                FakeWindow([go_live]),
            ]
        )

        with patch.object(
            self.controller, "_focus_discord", side_effect=lambda: next(windows)
        ):
            with patch("ichannel.discord_desktop.time.sleep"):
                self.controller._start_vlc_stream_sync()

        self.assertTrue(camera_off_button.invoked)
        self.assertFalse(camera_off_label.invoked)
        self.assertTrue(share_button.invoked)
        self.assertFalse(share_screen.invoked)
        self.assertTrue(vlc_window.clicked)
        self.assertTrue(go_live.clicked)


if __name__ == "__main__":
    unittest.main()
