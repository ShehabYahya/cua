from __future__ import annotations

import asyncio
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from activity import CATEGORIES, LABELS, Activity, closing_line, describe
from contracts import Decision, DesktopOverview, Element, Observation
from loop import AgentLoop

BANNED = ("selected", "candidate", "snapshot", "capture_id", "element_index",
          "confidence")
SLOT_TOKEN = re.compile(r"\bslot-\d")
ID_TOKEN = re.compile(r"\b[a-z]+-\d+\b")
STRICT_ID_TOKEN = re.compile(r"\b(?:[a-z]+-)+\d+(?:-[a-z0-9]+)*\b")

# ~15 representative (selected_id, tool, description) rows: every category, the
# real description formats the loop and candidates.py emit, and the id-stripping
# fallback.
REPRESENTATIVE = (
    ("done", None, "Report the task complete."),
    ("abstain", None, "Nothing in this window is safe or useful."),
    ("reobserve", None, "Take a fresh look at the window."),
    ("more-actions", None, "Show more of the remaining controls."),
    ("inspect-screen", None, "Capture this window's screenshot and ground it."),
    ("discover-apps", None, "Ask the Driver which applications are installed."),
    ("prepare-text", None, "Ask the companion model for the ordinary text."),
    ("switch-window-7-9", None, 'Switch to the window "Inbox — Mail".'),
    ("switch-window-7-9", None, "Switch to the window 'Firefox — New Tab'."),
    ("launch-app-1", None, 'Launch the application "Finder".'),
    ("visual-v2", "click", 'Activate visually grounded button "Send".'),
    ("click-12", "click", 'Activate push button "Save".'),
    ("browser-click-4", "browser_click", 'Click link "Docs".'),
    ("browser-drag-2", "browser_drag", 'Drag icon "File".'),
    ("type-3-slot-1", "type_text", 'Put prepared text slot-1 into entry "Search".'),
    ("bundle-browser-search-slot-2", "sequence",
     "Focus the browser address bar, type prepared text slot-2, and press Enter."),
    ("hotkey-address", "hotkey", "Press Ctrl+L to focus the address bar."),
    ("press-enter", "press_key", "Press Enter in the target window."),
    ("scroll-2", "scroll", "Scroll the target window down."),
    ("mystery-step-4", None, "Activate click-3 and slot-2 in the window."),
)


class ActivityTest(unittest.TestCase):
    def assert_invariant(self, activity: Activity) -> None:
        self.assertIn(activity.category, CATEGORIES)
        self.assertEqual(activity.label, LABELS[activity.category])
        text = activity.text
        self.assertTrue(text)
        for word in BANNED:
            self.assertNotIn(word, text)
        self.assertIsNone(SLOT_TOKEN.search(text))
        self.assertIsNone(ID_TOKEN.search(text))
        self.assertIsNone(STRICT_ID_TOKEN.search(text))

    # -- table and invariant ----------------------------------------------
    def test_invariant_holds_over_representative_inputs(self):
        for selected_id, tool, description in REPRESENTATIVE:
            with self.subTest(selected_id=selected_id, tool=tool):
                self.assert_invariant(
                    describe(selected_id=selected_id, tool=tool,
                             description=description)
                )

    def test_categories_and_labels_cover_every_rule(self):
        self.assertEqual(len(CATEGORIES), len(set(CATEGORIES)))
        self.assertEqual(set(CATEGORIES), set(LABELS))
        expected = {
            "done": "finish", "abstain": "stop", "reobserve": "refresh",
            "more-actions": "options", "inspect-screen": "look",
            "discover-apps": "look", "prepare-text": "compose",
            "switch-window-7-9": "switch", "launch-app-1": "launch",
            "visual-v2": "click", "click-3": "click", "browser-click-3": "click",
            "browser-pointer-3": "click", "browser-drag-3": "click",
            "type-2-slot-1": "type", "browser-type-3-slot-1": "type",
            "bundle-browser-search-slot-1": "type",
            "bundle-browser-new-tab-search-slot-1": "type",
            "bundle-fill-submit-2-slot-1": "type", "hotkey-address": "keys",
            "press-enter": "keys", "scroll-2": "scroll", "unknown-thing": "other",
        }
        for selected_id, category in expected.items():
            with self.subTest(selected_id=selected_id):
                row = describe(selected_id=selected_id, tool=None, description="")
                self.assertEqual(row.category, category)

    def test_tool_names_resolve_when_the_id_is_uninformative(self):
        cases = {
            "click": "click", "double_click": "click", "right_click": "click",
            "browser_click": "click", "type_text": "type", "browser_type": "type",
            "press_key": "keys", "hotkey": "keys", "scroll": "scroll",
        }
        for tool, category in cases.items():
            with self.subTest(tool=tool):
                row = describe(selected_id="opaque", tool=tool, description="")
                self.assertEqual(row.category, category)

    # -- text rules --------------------------------------------------------
    def test_finish_and_stop_texts(self):
        self.assertEqual(describe(selected_id="done", tool=None, description=""),
                         Activity("finish", "Finish", "Reported the task complete."))
        self.assertEqual(
            describe(selected_id="abstain", tool=None, description="").text,
            "Stopped: nothing available was safe or useful.",
        )

    def test_refresh_look_and_compose_texts(self):
        for selected_id, expected in (
            ("reobserve", "Took a fresh look at the window."),
            ("more-actions", "Showed more available actions."),
        ):
            self.assertEqual(
                describe(selected_id=selected_id, tool=None, description="").text,
                expected,
            )
        self.assertEqual(
            describe(selected_id="inspect-screen", tool=None, description="").text,
            "Captured the window to find the controls.",
        )
        self.assertEqual(
            describe(selected_id="discover-apps", tool=None, description="").text,
            "Checked which applications are installed.",
        )
        self.assertEqual(
            describe(selected_id="prepare-text", tool=None, description="").text,
            "Composed the text this step needed.",
        )

    def test_switch_uses_first_quoted_title_with_fallback(self):
        row = describe(
            selected_id="switch-window-7-9", tool=None,
            description='Switch to the window "Reports — Q3".',
        )
        self.assertEqual(row.category, "switch")
        self.assertEqual(row.label, "Switch")
        self.assertEqual(row.text, 'Switched to "Reports — Q3".')
        # loop.py formats this description with `!r`, so real window titles
        # arrive single-quoted and must still surface in the chat row.
        single = describe(
            selected_id="switch-window-7-9", tool=None,
            description="Switch to the window 'Firefox — New Tab'.",
        )
        self.assertEqual(single.text, 'Switched to "Firefox — New Tab".')
        fallback = describe(
            selected_id="switch-window-7-9", tool=None,
            description="Switch to the window.",
        )
        self.assertEqual(fallback.text, "Switched to another window.")

    def test_launch_uses_first_quoted_name_with_fallback(self):
        row = describe(
            selected_id="launch-app-2", tool=None,
            description='Launch the application "Finder".',
        )
        self.assertEqual(row.text, 'Opened "Finder".')
        # Same `!r` formatting as switch: application names arrive single-quoted.
        single = describe(
            selected_id="launch-app-2", tool=None,
            description="Launch the application 'Finder'.",
        )
        self.assertEqual(single.text, 'Opened "Finder".')
        fallback = describe(
            selected_id="launch-app-2", tool=None,
            description="Launch the application.",
        )
        self.assertEqual(fallback.text, "Opened an application.")

    def test_click_reads_quoted_label_and_optional_kind_word(self):
        cases = (
            ('Activate push button "Save".', "Clicked the Save button."),
            ('Activate link "Docs".', "Clicked the Docs link."),
            ('Activate tab "Inbox".', "Clicked the Inbox tab."),
            ('Open the context menu for button "Save".', "Clicked the Save button."),
            ('Double-click listitem "Reports".', "Clicked the Reports."),
            ('Activate visually grounded region "Send".', "Clicked the Send."),
        )
        for description, expected in cases:
            with self.subTest(description=description):
                self.assertEqual(
                    describe(selected_id="click-1", tool="click",
                             description=description).text,
                    expected,
                )

    def test_click_falls_back_without_a_quoted_label(self):
        self.assertEqual(
            describe(selected_id="click-1", tool="click",
                     description="Activate the highlighted control.").text,
            "Clicked a control on the screen.",
        )

    def test_type_never_echoes_a_slot_or_prepared_text_id(self):
        for selected_id, tool, description in (
            ("type-3-slot-1", "type_text",
             'Put prepared text slot-1 into entry "Search".'),
            ("bundle-browser-search-slot-9", "sequence",
             "Focus the address bar, type prepared text slot-9, then press Enter."),
            ("bundle-fill-submit-2-slot-4", "sequence",
             'Fill entry "Search" with prepared text slot-4 and submit it.'),
        ):
            with self.subTest(selected_id=selected_id):
                row = describe(selected_id=selected_id, tool=tool,
                               description=description)
                self.assertEqual(row.category, "type")
                self.assertEqual(
                    row.text, "Typed the prepared text into the focused field."
                )
                self.assertNotIn("slot", row.text)

    def test_keys_recovers_a_combo_with_fallback(self):
        for description, expected in (
            ("Press Ctrl+L in the target window.", "Pressed Ctrl+L."),
            ("Press Ctrl+Alt+Space in the target window.", "Pressed Ctrl+Alt+Space."),
            ("Press Enter in the target window.", "Pressed a keyboard shortcut."),
            ("Open a new tab.", "Pressed a keyboard shortcut."),
        ):
            with self.subTest(description=description):
                self.assertEqual(
                    describe(selected_id="hotkey-1", tool="hotkey",
                             description=description).text,
                    expected,
                )

    def test_scroll_text(self):
        self.assertEqual(
            describe(selected_id="scroll-2", tool="scroll",
                     description="Scroll the target window down.").text,
            "Scrolled the view.",
        )

    def test_other_strips_candidate_ids_with_fallback(self):
        row = describe(
            selected_id="mystery", tool=None,
            description="Activate click-3 and slot-2 in the window.",
        )
        self.assertEqual(row.category, "other")
        self.assertEqual(row.label, "Action")
        self.assertEqual(row.text, "Activate and in the window.")
        empty = describe(
            selected_id="mystery-9", tool=None,
            description="click-3 slot-2",
        )
        self.assertEqual(empty.text, "Took an action.")

    # -- closing line ------------------------------------------------------
    def test_closing_line_branches(self):
        cases = (
            ("completed", "", "Done."),
            ("abstained", "",
             "I stopped without acting: nothing I could see was safe or useful."),
            ("budget_exhausted", "", "I ran out of steps before finishing."),
            ("cancelled", "", "Cancelled."),
            ("dry_run", "", "Dry run: I stopped before acting."),
            ("refused", "The Driver refused this action.", "The Driver refused this action."),
            ("refused", "", "I stopped."),
            ("blocked", "Sensitive text is off-limits.", "Sensitive text is off-limits."),
            ("blocked", "", "I stopped."),
            ("failed", "provider exploded", "provider exploded"),
            ("failed", "", "Something went wrong."),
            ("", "", "Something went wrong."),
        )
        for status, message, expected in cases:
            with self.subTest(status=status, message=message):
                self.assertEqual(closing_line(status, message), expected)

    # -- loop and runtime wiring -------------------------------------------
    def test_loop_reports_one_activity_row_per_selected_step(self):
        rows = []
        agent = AgentLoop(
            FakeDriver(),
            FakeChooser(),
            max_steps=4,
            activity=lambda step, selected_id, confidence, activity: rows.append(
                (step, selected_id, confidence, activity)
            ),
        )
        result = asyncio.run(agent.run("open new tab", act=True))
        self.assertEqual(result.status, "completed")
        self.assertEqual([row[1] for row in rows], ["click-1", "done"])
        self.assertEqual(rows[0][0], 1)
        self.assertEqual(rows[0][2], 0.99)
        self.assertEqual(rows[0][3].category, "click")
        self.assertEqual(rows[0][3].text, "Clicked the New Tab button.")
        self.assertEqual(rows[1][3].category, "finish")

    def test_activity_failure_never_breaks_the_loop(self):
        def boom(*args):
            raise RuntimeError("chat view is gone")

        agent = AgentLoop(FakeDriver(), FakeChooser(), max_steps=4, activity=boom)
        result = asyncio.run(agent.run("open new tab", act=True))
        self.assertEqual(result.status, "completed")


class FakeDriver:
    capture_bound_click = False
    coordinate_click_supported = False

    def __init__(self):
        self.counter = 0
        self.clicked = False

    def _window(self):
        return {
            "app_name": "Firefox",
            "title": "Search the web" if self.clicked else "New Tab",
            "pid": 7,
            "window_id": 9,
            "is_focused": True,
            "is_on_screen": True,
        }

    async def desktop_overview(self, *, include_screenshot=True, include_apps=True):
        return DesktopOverview((self._window(),), ())

    async def list_windows(self):
        return [self._window()]

    async def list_apps(self):
        return []

    async def has_window(self, app):
        return True

    async def ensure_app(self, app):
        return None

    async def revive_session(self):
        pass

    async def observe(self, app=None, *, include_screenshot=True, windows=None,
                      target_window=None):
        self.counter += 1
        return Observation(
            f"s{self.counter}",
            7,
            9,
            "Firefox",
            "Search the web" if self.clicked else "New Tab",
            (
                Element(
                    1,
                    f"s{self.counter}:1",
                    "push button",
                    "New Tab",
                    actions=("click",),
                ),
            ),
        )

    async def execute(self, candidate):
        self.clicked = True
        return {"effect": "confirmed"}

    def with_foreground(self, candidate):
        return candidate


class FakeChooser:
    async def choose(self, *, observation, candidates, **kwargs):
        selected = "done" if observation.window_title == "Search the web" else "click-1"
        ids = {candidate.id for candidate in candidates}
        if selected not in ids:
            raise AssertionError(f"{selected} missing from {sorted(ids)}")
        return Decision(selected, 0.99, {selected: 0.99})


if __name__ == "__main__":
    unittest.main()
