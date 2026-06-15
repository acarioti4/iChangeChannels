from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

CLICKABLE_TEXT_CONTROL_TYPES = {"Button", "Text", "Hyperlink", "ListItem", "MenuItem", "Custom"}
CONTAINER_CONTROL_TYPES = {"Document", "Pane", "Group", "Window"}
JOIN_VOICE_BUTTON_LABELS = ["Join Voice", "Join Voice Channel"]
SHARE_SCREEN_BUTTON_LABELS = ["Share Your Screen", "Share Screen"]
SHARE_SCREEN_REJECT_FRAGMENTS = ["camera", "video"]
CAMERA_OFF_BUTTON_LABELS = [
    "Turn Off Camera",
    "Stop Camera",
    "Turn Off Video",
    "Stop Video",
]
MAX_CLICKABLE_TEXT_LENGTH = 220
MAX_OFFSCREEN_COORDINATE = 100000


class DesktopAutomationError(RuntimeError):
    """Raised when Discord desktop automation cannot complete."""


@dataclass(frozen=True)
class DesktopAutomationConfig:
    dm_search: str
    vlc_window_title: str


class DiscordDesktopController:
    def __init__(self, config: DesktopAutomationConfig, logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger

    async def join_voice_from_dm_link(self, join_url: str) -> None:
        try:
            await asyncio.to_thread(self._join_voice_from_dm_link_sync, join_url)
        except DesktopAutomationError:
            raise
        except Exception as exc:
            raise DesktopAutomationError(str(exc)) from exc

    async def start_vlc_stream(self) -> None:
        try:
            await asyncio.to_thread(self._start_vlc_stream_sync)
        except DesktopAutomationError:
            raise
        except Exception as exc:
            raise DesktopAutomationError(str(exc)) from exc

    def _join_voice_from_dm_link_sync(self, join_url: str) -> None:
        self.logger.info("Desktop automation: opening Discord DM for join link")
        self._focus_discord()
        self._quick_switch(self.config.dm_search)
        time.sleep(1.5)
        window = self._focus_discord()

        if not self._click_dm_join_control(window, join_url, timeout=8):
            raise DesktopAutomationError(
                "Discord DM opened, but neither the expected join link nor Discord's "
                "Join Voice button was visible or clickable: "
                f"{join_url}"
            )
        self.logger.info("Desktop automation: clicked Discord join link in DM")
        time.sleep(2)

    def _start_vlc_stream_sync(self) -> None:
        self.logger.info("Desktop automation: starting VLC stream in Discord")
        window = self._focus_discord()

        if self._turn_off_camera_if_on(window, timeout=1.5):
            time.sleep(0.5)
            window = self._focus_discord()

        if not self._click_share_screen_button(window, timeout=6):
            details = self._describe_relevant_controls(window)
            raise DesktopAutomationError(
                "Could not find Discord's exact Share Your Screen control while in the "
                f"voice channel. Visible related controls: {details}"
            )

        time.sleep(1.5)
        window = self._focus_discord()
        if not self._click_text_containing(
            window,
            [self.config.vlc_window_title],
            timeout=6,
        ):
            raise DesktopAutomationError(
                "After clicking Share Your Screen, the configured VLC window was not visible or clickable."
            )

        time.sleep(1)
        window = self._focus_discord()
        if not self._click_button(window, ["Go Live", "Start Streaming"], 6):
            raise DesktopAutomationError(
                "VLC was selected, but Discord's Go Live button was not visible or clickable."
            )

        self.logger.info("Desktop automation: stream start sequence submitted")

    def _focus_discord(self):
        from pywinauto import Desktop

        desktop = Desktop(backend="uia")
        candidates = []
        process_candidates = []
        title_candidates = []
        for window in desktop.windows():
            title = window.window_text() or ""
            if "discord" in title.lower():
                candidates.append(window)
                if title.lower() == "discord" or title.lower().endswith(" - discord"):
                    title_candidates.append(window)
                try:
                    import psutil

                    process_name = psutil.Process(window.process_id()).name().lower()
                    if process_name.startswith("discord"):
                        process_candidates.append(window)
                except Exception:
                    pass

        candidates = process_candidates or title_candidates or candidates
        if not candidates:
            raise DesktopAutomationError("No Discord desktop window was found")

        window = candidates[0]
        try:
            window.restore()
        except Exception as exc:
            raise DesktopAutomationError("Discord window was found but could not be restored.") from exc
        try:
            window.set_focus()
        except Exception as exc:
            raise DesktopAutomationError("Discord window was found but could not be focused.") from exc
        time.sleep(0.5)
        return window

    def _quick_switch(self, text: str) -> None:
        try:
            import pyautogui
            import pyperclip

            pyautogui.hotkey("ctrl", "k")
            time.sleep(0.2)
            pyperclip.copy(text)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.2)
            pyautogui.press("enter")
        except Exception as exc:
            raise DesktopAutomationError(
                f"Could not submit '{text}' through Discord quick switcher."
            ) from exc

    def _click_button(self, window, labels: list[str], timeout: float) -> bool:
        normalized = [label.lower() for label in labels]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for element in self._descendants(window):
                try:
                    name = (element.window_text() or element.element_info.name or "")
                    control_type = element.element_info.control_type
                except Exception:
                    continue
                if self._matches_button_label(name, control_type, normalized):
                    try:
                        element.click_input()
                        return True
                    except Exception as exc:
                        raise DesktopAutomationError(
                            f"Found Discord button '{self._short_name(name)}', but clicking it failed."
                        ) from exc
            time.sleep(0.25)
        return False

    def _click_text_containing(self, window, fragments: list[str], timeout: float) -> bool:
        normalized = [fragment.lower() for fragment in fragments if fragment]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for element in self._descendants(window):
                try:
                    name = element.window_text() or element.element_info.name or ""
                    control_type = element.element_info.control_type
                except Exception:
                    continue
                if self._matches_text_fragment(name, control_type, normalized):
                    try:
                        element.click_input()
                        return True
                    except Exception as exc:
                        raise DesktopAutomationError(
                            f"Found Discord element '{self._short_name(name)}', but clicking it failed."
                        ) from exc
            time.sleep(0.25)
        return False

    def _click_share_screen_button(self, window, timeout: float) -> bool:
        normalized = [
            self._normalize_text(label) for label in SHARE_SCREEN_BUTTON_LABELS if label
        ]
        rejected = [
            self._normalize_text(fragment) for fragment in SHARE_SCREEN_REJECT_FRAGMENTS
        ]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            candidates = []
            for element in self._descendants(window):
                try:
                    name = element.window_text() or element.element_info.name or ""
                    control_type = element.element_info.control_type
                except Exception:
                    continue
                if self._matches_share_screen_button(
                    name, control_type, normalized, rejected
                ):
                    target = self._target_for_exact_labeled_control(element, control_type)
                    if target is not None and self._is_actionable_element(target):
                        candidates.append((element, target))

            if candidates:
                label_element, target = self._best_labeled_candidate(candidates)
                try:
                    name = (
                        label_element.window_text()
                        or label_element.element_info.name
                        or ""
                    )
                    control_type = label_element.element_info.control_type
                except Exception:
                    name = ""
                    control_type = "unknown"
                self.logger.info(
                    "Desktop automation: clicking Discord Share Your Screen target: label_type=%s label=%r label_rect=%s target_rect=%s",
                    control_type,
                    self._short_name(name),
                    self._element_rect_text(label_element),
                    self._element_rect_text(target),
                )
                try:
                    self._activate_element(target)
                    return True
                except Exception as exc:
                    raise DesktopAutomationError(
                        f"Found Discord Share Your Screen button '{self._short_name(name)}', but activating it failed."
                    ) from exc
            time.sleep(0.25)
        return False

    def _turn_off_camera_if_on(self, window, timeout: float) -> bool:
        normalized = [
            self._normalize_text(label) for label in CAMERA_OFF_BUTTON_LABELS if label
        ]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for element in self._descendants(window):
                try:
                    name = element.window_text() or element.element_info.name or ""
                    control_type = element.element_info.control_type
                except Exception:
                    continue
                if not self._matches_exact_button_label(name, control_type, normalized):
                    continue
                target = self._target_for_exact_labeled_control(element, control_type)
                if target is None or not self._is_actionable_element(target):
                    continue
                self.logger.info(
                    "Desktop automation: turning off Discord camera before screen share: label=%r label_rect=%s target_rect=%s",
                    self._short_name(name),
                    self._element_rect_text(element),
                    self._element_rect_text(target),
                )
                try:
                    self._activate_element(target)
                    return True
                except Exception as exc:
                    raise DesktopAutomationError(
                        f"Found Discord camera-off button '{self._short_name(name)}', but activating it failed."
                    ) from exc
            time.sleep(0.25)
        return False

    def _click_dm_join_control(self, window, join_url: str, timeout: float) -> bool:
        normalized_url = [join_url.lower()]
        normalized_buttons = [label.lower() for label in JOIN_VOICE_BUTTON_LABELS]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            elements = list(self._descendants(window))
            for element in reversed(elements):
                try:
                    name = element.window_text() or element.element_info.name or ""
                    control_type = element.element_info.control_type
                except Exception:
                    continue
                if self._matches_text_fragment(
                    name, control_type, normalized_url
                ) or self._matches_button_label(name, control_type, normalized_buttons):
                    try:
                        element.click_input()
                        return True
                    except Exception as exc:
                        raise DesktopAutomationError(
                            f"Found Discord join control '{self._short_name(name)}', but clicking it failed."
                        ) from exc
            time.sleep(0.25)
        return False

    def _descendants(self, window):
        try:
            return window.descendants()
        except Exception as exc:
            raise DesktopAutomationError("Could not enumerate Discord UI elements.") from exc

    def _matches_button_label(
        self, name: str, control_type: str, normalized_labels: list[str]
    ) -> bool:
        normalized_name = self._normalize_text(name)
        if not normalized_name:
            return False
        if control_type == "Button":
            return any(label in normalized_name for label in normalized_labels)
        if control_type not in CLICKABLE_TEXT_CONTROL_TYPES:
            return False
        if len(normalized_name) > MAX_CLICKABLE_TEXT_LENGTH:
            return False
        return any(label == normalized_name or label in normalized_name for label in normalized_labels)

    def _matches_text_fragment(
        self, name: str, control_type: str, normalized_fragments: list[str]
    ) -> bool:
        if control_type in CONTAINER_CONTROL_TYPES:
            return False
        if control_type not in CLICKABLE_TEXT_CONTROL_TYPES:
            return False

        normalized_name = self._normalize_text(name)
        if not normalized_name or len(normalized_name) > MAX_CLICKABLE_TEXT_LENGTH:
            return False
        return any(fragment in normalized_name for fragment in normalized_fragments)

    def _matches_share_screen_button(
        self,
        name: str,
        control_type: str,
        normalized_labels: list[str],
        rejected_fragments: list[str],
    ) -> bool:
        if control_type not in {"Button", "Text"}:
            return False

        normalized_name = self._normalize_text(name)
        if not normalized_name or len(normalized_name) > MAX_CLICKABLE_TEXT_LENGTH:
            return False
        if any(fragment in normalized_name for fragment in rejected_fragments):
            return False
        return normalized_name in normalized_labels

    def _matches_exact_button_label(
        self, name: str, control_type: str, normalized_labels: list[str]
    ) -> bool:
        if control_type not in {"Button", "Text"}:
            return False
        normalized_name = self._normalize_text(name)
        if not normalized_name or len(normalized_name) > MAX_CLICKABLE_TEXT_LENGTH:
            return False
        return normalized_name in normalized_labels

    def _target_for_exact_labeled_control(self, element, control_type: str):
        if control_type == "Button":
            return element
        if control_type != "Text":
            return None
        return self._immediately_preceding_sibling_button(element)

    def _immediately_preceding_sibling_button(self, element):
        try:
            parent = element.parent()
            siblings = parent.children()
        except Exception:
            return None
        for index, sibling in enumerate(siblings):
            if not self._same_element(sibling, element):
                continue
            if index == 0:
                return None
            previous = siblings[index - 1]
            try:
                if previous.element_info.control_type != "Button":
                    return None
            except Exception:
                return None
            return previous
        return None

    def _best_labeled_candidate(self, candidates):
        return min(candidates, key=lambda candidate: self._element_area(candidate[1]))

    def _same_element(self, first, second) -> bool:
        if first is second:
            return True
        try:
            if first == second:
                return True
        except Exception:
            pass
        try:
            first_id = first.element_info.runtime_id
            second_id = second.element_info.runtime_id
            if first_id and second_id and first_id == second_id:
                return True
        except Exception:
            pass
        try:
            first_rect = first.rectangle()
            second_rect = second.rectangle()
            first_name = first.window_text() or first.element_info.name or ""
            second_name = second.window_text() or second.element_info.name or ""
            return (
                first.element_info.control_type == second.element_info.control_type
                and first_name == second_name
                and first_rect.left == second_rect.left
                and first_rect.top == second_rect.top
                and first_rect.right == second_rect.right
                and first_rect.bottom == second_rect.bottom
            )
        except Exception:
            return False

    def _is_actionable_element(self, element) -> bool:
        try:
            if hasattr(element, "is_visible") and not element.is_visible():
                return False
        except Exception:
            return False
        try:
            if hasattr(element, "is_enabled") and not element.is_enabled():
                return False
        except Exception:
            return False
        try:
            rect = element.rectangle()
        except Exception:
            return False
        if rect.width() <= 0 or rect.height() <= 0:
            return False
        if (
            abs(rect.left) > MAX_OFFSCREEN_COORDINATE
            or abs(rect.top) > MAX_OFFSCREEN_COORDINATE
        ):
            return False
        return True

    def _element_area(self, element) -> int:
        try:
            rect = element.rectangle()
            return max(0, rect.width()) * max(0, rect.height())
        except Exception:
            return 10**12

    def _activate_element(self, element) -> None:
        try:
            element.invoke()
            return
        except Exception:
            pass
        try:
            element.iface_invoke.Invoke()
            return
        except Exception:
            pass
        element.click_input()

    def _element_rect_text(self, element) -> str:
        try:
            rect = element.rectangle()
            return (
                f"left={rect.left} top={rect.top} "
                f"width={rect.width()} height={rect.height()}"
            )
        except Exception:
            return "unavailable"

    def _describe_relevant_controls(self, window) -> str:
        controls = []
        for element in self._descendants(window):
            try:
                name = element.window_text() or element.element_info.name or ""
                control_type = element.element_info.control_type
            except Exception:
                continue
            normalized_name = self._normalize_text(name)
            if control_type not in {"Button", "Text"}:
                continue
            if not normalized_name:
                continue
            if not any(
                fragment in normalized_name
                for fragment in ["share", "screen", "camera", "video", "stream"]
            ):
                continue
            controls.append(
                f"{control_type} name={self._short_name(name)!r} "
                f"rect={self._element_rect_text(element)}"
            )
        if not controls:
            return "none"
        return "; ".join(controls[:20])

    def _normalize_text(self, text: str) -> str:
        return " ".join(text.lower().split())

    def _short_name(self, name: str) -> str:
        collapsed = " ".join(name.split())
        if len(collapsed) <= 120:
            return collapsed
        return collapsed[:117] + "..."
